"""Anthropic-Anbindung.

Eine Stelle fuer Modellwahl, strukturierte Ausgaben, Fehlerbehandlung und
Kostenzaehlung. Der Rest des Systems sieht nur `LLM.structured()` und
`LLM.text()`.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic

log = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

# USD je 1 Mio. Token (Stand 2026-06). Nur fuer die Kostenanzeige im Lauf-Log —
# fuer die Abrechnung gilt die Konsole.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Haiku 4.5 kennt weder adaptives Denken noch `output_config.effort`.
NO_ADAPTIVE_THINKING = ("claude-haiku-4-5", "claude-haiku-4-5-")

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0

    def add(self, model: str, usage: Any) -> None:
        inp = int(getattr(usage, "input_tokens", 0) or 0)
        out = int(getattr(usage, "output_tokens", 0) or 0)
        cached = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        created = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)

        self.input_tokens += inp + cached + created
        self.output_tokens += out
        self.cache_read_tokens += cached
        self.calls += 1

        in_price, out_price = PRICING.get(_price_key(model), (0.0, 0.0))
        self.cost_usd += (
            (inp + created * 1.25 + cached * 0.1) * in_price / 1_000_000
            + out * out_price / 1_000_000
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cost_usd": round(self.cost_usd, 4),
        }


def _price_key(model: str) -> str:
    for key in PRICING:
        if model.startswith(key):
            return key
    return model


class LLMError(RuntimeError):
    pass


@dataclass
class LLM:
    api_key: str | None = None
    max_retries: int = 3
    usage: Usage = field(default_factory=Usage)
    _client: anthropic.Anthropic | None = field(default=None, init=False, repr=False)
    # Modelle, bei denen output_config zurueckgewiesen wurde — dann laeuft der
    # Rest des Laufs direkt ueber den Prompt-JSON-Pfad.
    _no_structured: set[str] = field(default_factory=set, init=False, repr=False)

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            # Ohne api_key loest das SDK selbst auf (ANTHROPIC_API_KEY,
            # ANTHROPIC_AUTH_TOKEN oder ein `ant auth login`-Profil).
            self._client = (
                anthropic.Anthropic(api_key=self.api_key)
                if self.api_key
                else anthropic.Anthropic()
            )
        return self._client

    # ------------------------------------------------------------------ calls

    def text(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int = 16_000,
        effort: str | None = "high",
        thinking: bool = True,
    ) -> str:
        response = self._create(
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            effort=effort,
            thinking=thinking,
        )
        return _first_text(response)

    def structured(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        max_tokens: int = 16_000,
        effort: str | None = "high",
        thinking: bool = True,
    ) -> dict[str, Any]:
        """JSON nach Schema. Faellt auf Prompt-JSON zurueck, wenn das Modell
        `output_config.format` nicht unterstuetzt."""
        use_schema = model not in self._no_structured

        if use_schema:
            try:
                response = self._create(
                    model=model,
                    system=system,
                    messages=messages,
                    max_tokens=max_tokens,
                    effort=effort,
                    thinking=thinking,
                    output_config={"format": {"type": "json_schema", "schema": schema}},
                )
                return _parse_json(_first_text(response))
            except anthropic.BadRequestError as exc:
                if "output_config" not in str(exc) and "format" not in str(exc):
                    raise
                log.info(
                    "Modell %s nimmt kein output_config — Rest des Laufs ueber "
                    "Prompt-JSON: %s", model, exc,
                )
                self._no_structured.add(model)

        instruction = (
            "Antworte ausschliesslich mit JSON nach diesem Schema, ohne "
            "Vorwort und ohne Code-Fence:\n"
            + json.dumps(schema, ensure_ascii=False, indent=2)
        )
        patched = list(messages)
        patched[-1] = {
            **patched[-1],
            "content": f"{patched[-1]['content']}\n\n{instruction}",
        }
        response = self._create(
            model=model,
            system=system,
            messages=patched,
            max_tokens=max_tokens,
            effort=effort,
            thinking=thinking,
        )
        return _parse_json(_first_text(response))

    # ---------------------------------------------------------------- interna

    def _create(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int,
        effort: str | None,
        thinking: bool,
        output_config: dict[str, Any] | None = None,
    ):
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if not model.startswith(NO_ADAPTIVE_THINKING):
            if thinking:
                params["thinking"] = {"type": "adaptive"}
            if effort:
                params["output_config"] = {"effort": effort}
        if output_config:
            params["output_config"] = {
                **params.get("output_config", {}),
                **output_config,
            }

        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                # Streaming, weil Digest und Monatsbrief lange Antworten sind
                # und ein nicht-gestreamter Request in den HTTP-Timeout laeuft.
                with self.client.messages.stream(**params) as stream:
                    response = stream.get_final_message()
            except (anthropic.RateLimitError, anthropic.APIConnectionError,
                    anthropic.APITimeoutError) as exc:
                last = exc
                wait = 2**attempt
                if isinstance(exc, anthropic.RateLimitError):
                    wait = int(exc.response.headers.get("retry-after", wait))
                log.warning("Anthropic %s — neuer Versuch in %ss",
                            type(exc).__name__, wait)
                time.sleep(wait)
                continue
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500 and attempt < self.max_retries - 1:
                    last = exc
                    time.sleep(2**attempt)
                    continue
                raise

            if response.stop_reason == "refusal":
                detail = getattr(response, "stop_details", None)
                raise LLMError(
                    "Anfrage abgelehnt "
                    f"({getattr(detail, 'category', 'ohne Kategorie')})"
                )
            if response.stop_reason == "max_tokens":
                log.warning(
                    "Antwort bei max_tokens=%d abgeschnitten (Modell %s)",
                    max_tokens, model,
                )

            self.usage.add(model, response.usage)
            return response

        raise LLMError(f"Anthropic nach {self.max_retries} Versuchen nicht erreichbar: {last}")


def _first_text(response) -> str:
    for block in response.content:
        if block.type == "text":
            return block.text
    raise LLMError("Antwort ohne Textblock")


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = _JSON_BLOCK_RE.search(text)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise LLMError(f"Antwort ist kein JSON: {text[:300]}")


def load_prompt(name: str, **substitutions: str) -> str:
    """Prompt-Datei laden und {{PLATZHALTER}} ersetzen."""
    text = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    for key, value in substitutions.items():
        text = text.replace(f"{{{{{key}}}}}", value)
    if remaining := re.findall(r"\{\{([A-Z_]+)\}\}", text):
        raise ValueError(f"Prompt {name}: unersetzte Platzhalter {remaining}")
    return text


def interest_profile() -> str:
    return (PROMPT_DIR / "interessensprofil.md").read_text(encoding="utf-8").strip()
