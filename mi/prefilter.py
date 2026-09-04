"""Prefilter: Relevanz 0-10 je Item, mit Haiku.

Items laufen gebuendelt durch — ein Request je Bündel statt je Item. Das
Interessensprofil steht als gecachter Systemprompt davor, damit es nicht
hundertmal am Tag neu bezahlt wird.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Sequence

from . import db
from .config import Config
from .llm import LLM, interest_profile, load_prompt

log = logging.getLogger(__name__)

VALID_TOPICS = {
    "goae", "politik", "klinikmarkt", "wettbewerb", "kis", "finanzierung",
    "sonstiges",
}

# Wie viel Volltext je Item in den Prompt geht. Mehr hilft der Bewertung kaum,
# kostet aber linear.
TEXT_BUDGET = 1_200

RATING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ratings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 10},
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                    "topics": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(VALID_TOPICS)},
                    },
                    "entities": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "score", "summary", "reason", "topics", "entities"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ratings"],
    "additionalProperties": False,
}


@dataclass
class PrefilterStats:
    scored: int = 0
    failed: int = 0
    batches: int = 0
    distribution: dict[int, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scored": self.scored,
            "failed": self.failed,
            "batches": self.batches,
            "distribution": {str(k): v for k, v in sorted(self.distribution.items())},
        }


def run(
    conn: sqlite3.Connection,
    config: Config,
    llm: LLM,
    *,
    limit: int = 500,
) -> PrefilterStats:
    stats = PrefilterStats()
    rows = db.unscored_items(conn, limit=limit)
    if not rows:
        return stats

    system = _system_prompt()
    batch_size = max(1, config.prefilter_batch_size)

    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        stats.batches += 1
        try:
            ratings = _rate_batch(llm, config.model_prefilter, system, batch)
        except Exception as exc:  # ein Bündel darf den Lauf nicht kippen
            stats.failed += len(batch)
            log.exception("Prefilter-Bündel %d fehlgeschlagen: %s", stats.batches, exc)
            continue

        _apply_ratings(conn, batch, ratings, stats)
        conn.commit()

    log.info(
        "Prefilter: %d bewertet, %d fehlgeschlagen, Verteilung %s",
        stats.scored, stats.failed, stats.as_dict()["distribution"],
    )
    return stats


def _system_prompt() -> list[dict[str, Any]]:
    """Als Block mit cache_control: das Profil ist bei jedem Bündel identisch."""
    return [
        {
            "type": "text",
            "text": load_prompt("prefilter", PROFIL=interest_profile()),
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _rate_batch(
    llm: LLM,
    model: str,
    system: list[dict[str, Any]],
    batch: Sequence[sqlite3.Row],
) -> dict[int, dict[str, Any]]:
    payload = llm.structured(
        model=model,
        system=system,
        messages=[{"role": "user", "content": _render_batch(batch)}],
        schema=RATING_SCHEMA,
        max_tokens=8_000,
        effort=None,     # Haiku 4.5 kennt output_config.effort nicht
        thinking=False,  # Klassifikation, kein Denkbedarf
    )
    return {
        int(entry["id"]): entry
        for entry in payload.get("ratings", [])
        if isinstance(entry, dict) and "id" in entry
    }


def _render_batch(batch: Sequence[sqlite3.Row]) -> str:
    parts = [f"Bewerte die folgenden {len(batch)} Items.\n"]
    for row in batch:
        text = (row["raw_text"] or "").strip()
        if len(text) > TEXT_BUDGET:
            text = text[:TEXT_BUDGET].rstrip() + " […]"
        parts.append(
            "\n".join(
                [
                    f"--- id: {row['id']}",
                    f"Quelle: {row['source']}",
                    f"Datum: {row['published_at'] or 'unbekannt'}",
                    f"Titel: {row['title']}",
                    f"Text: {text or '(kein Text im Feed — nur der Titel liegt vor)'}",
                ]
            )
        )
    return "\n\n".join(parts)


def _apply_ratings(
    conn: sqlite3.Connection,
    batch: Sequence[sqlite3.Row],
    ratings: dict[int, dict[str, Any]],
    stats: PrefilterStats,
) -> None:
    for row in batch:
        rating = ratings.get(int(row["id"]))
        if rating is None:
            stats.failed += 1
            log.warning("Item %s ohne Bewertung zurueckgekommen", row["id"])
            continue

        score = _clamp_score(rating.get("score"))
        if score is None:
            stats.failed += 1
            log.warning("Item %s: unbrauchbarer Score %r", row["id"], rating.get("score"))
            continue

        topics = [t for t in _as_list(rating.get("topics")) if t in VALID_TOPICS]
        entities = _as_list(rating.get("entities"))[:12]

        db.set_score(
            conn,
            int(row["id"]),
            score=score,
            summary=(rating.get("summary") or "").strip() or None,
            reason=(rating.get("reason") or "").strip() or None,
            topics=topics or ["sonstiges"],
            entities=entities,
        )
        for name in entities:
            db.touch_entity(conn, int(row["id"]), name)

        stats.scored += 1
        stats.distribution[score] = stats.distribution.get(score, 0) + 1


def _clamp_score(value: Any) -> int | None:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(10, score))


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
