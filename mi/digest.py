"""Taeglicher Digest: Auswahl, Einordnung, Versand."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from . import db
from .config import Config
from .llm import LLM, interest_profile, load_prompt
from .mailer import Mail, send
from .render import digest_html, digest_subject, digest_text
from .sources import SourceRegistry

log = logging.getLogger(__name__)

DIGEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject_suffix": {"type": "string"},
        "week_ahead": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer"},
                    "headline": {"type": "string"},
                    "what": {"type": "string"},
                    "einordnung": {"type": "string"},
                },
                "required": ["item_id", "headline", "what", "einordnung"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["subject_suffix", "week_ahead", "items"],
    "additionalProperties": False,
}


@dataclass
class DigestResult:
    sent: bool
    reason: str = ""
    subject: str = ""
    item_ids: list[int] = field(default_factory=list)
    backend: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "reason": self.reason,
            "subject": self.subject,
            "items": len(self.item_ids),
            "backend": self.backend,
        }


def run(
    conn: sqlite3.Connection,
    config: Config,
    llm: LLM,
    registry: SourceRegistry,
    *,
    dry_run: bool = False,
) -> DigestResult:
    candidates = db.digest_candidates(conn, config.digest_min_score)
    if not candidates:
        log.info("Keine Items ab Score %d — kein Digest", config.digest_min_score)
        return DigestResult(sent=False, reason="keine Kandidaten")

    payload = _generate(conn, config, llm, candidates)
    chosen = _resolve_items(candidates, payload.get("items", []), registry)
    if not chosen:
        log.warning("Modell hat kein einziges Item ausgewaehlt — kein Digest")
        return DigestResult(sent=False, reason="Auswahl leer")

    subject = digest_subject(payload.get("subject_suffix", ""))
    week_ahead = (payload.get("week_ahead") or "").strip()
    meta = (
        f"{len(chosen)} von {len(candidates)} Meldungen ab Score "
        f"{config.digest_min_score} · erzeugt {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    mail = Mail(
        subject=subject,
        text=digest_text(chosen, week_ahead, meta),
        html=digest_html(chosen, week_ahead, meta, subject),
    )

    item_ids = [item["item_id"] for item in chosen]
    if dry_run:
        print(mail.text)
        return DigestResult(sent=False, reason="dry-run", subject=subject,
                            item_ids=item_ids, backend="dry-run")

    backend = send(mail, config.mail)
    db.mark_digested(conn, item_ids)
    conn.commit()
    log.info("Digest verschickt (%s): %s", backend, subject)
    return DigestResult(sent=True, subject=subject, item_ids=item_ids, backend=backend)


def _generate(
    conn: sqlite3.Connection,
    config: Config,
    llm: LLM,
    candidates: Sequence[sqlite3.Row],
) -> dict[str, Any]:
    system = [
        {
            "type": "text",
            "text": load_prompt(
                "digest",
                PROFIL=interest_profile(),
                MIN_ITEMS=str(config.digest_min_items),
                MAX_ITEMS=str(config.digest_max_items),
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]
    return llm.structured(
        model=config.model_digest,
        system=system,
        messages=[{"role": "user", "content": _render_candidates(conn, candidates)}],
        schema=DIGEST_SCHEMA,
        max_tokens=16_000,
        effort="high",
    )


def _render_candidates(
    conn: sqlite3.Connection, candidates: Sequence[sqlite3.Row]
) -> str:
    parts = ["## Archiv-Kontext (Monatsbriefe)\n"]
    briefs = db.recent_briefs(conn, limit=6)
    if briefs:
        for brief in briefs:
            parts.append(f"### {brief['month']} — {brief['topic']}\n{brief['text']}\n")
    else:
        parts.append(
            "(Noch keine Monatsbriefe im Archiv — 'Diese Woche im Blick' hat "
            "keinen Vorlauf, sag das offen.)\n"
        )

    parts.append(f"\n## Meldungen des Tages ({len(candidates)})\n")
    for row in candidates:
        parts.append(
            "\n".join(
                [
                    f"--- item_id: {row['id']}  (Score {row['score']})",
                    f"Quelle: {row['source']}",
                    f"Datum: {row['published_at'] or 'unbekannt'}",
                    f"Titel: {row['title']}",
                    f"Zusammenfassung: {row['summary'] or '—'}",
                    f"Themen: {row['topics'] or '[]'}",
                    f"URL: {row['url']}",
                ]
            )
        )
    return "\n\n".join(parts)


def _resolve_items(
    candidates: Sequence[sqlite3.Row],
    selected: Sequence[dict[str, Any]],
    registry: SourceRegistry,
) -> list[dict[str, Any]]:
    """Modellauswahl mit den DB-Zeilen zusammenfuehren. URLs kommen aus der DB,
    nicht aus der Modellantwort — ein halluzinierter Link waere der eine Fehler,
    der den Brief unbrauchbar macht."""
    by_id = {int(row["id"]): row for row in candidates}
    resolved: list[dict[str, Any]] = []
    seen: set[int] = set()

    for entry in selected:
        try:
            item_id = int(entry["item_id"])
        except (KeyError, TypeError, ValueError):
            continue
        row = by_id.get(item_id)
        if row is None or item_id in seen:
            log.warning("Digest: item_id %r nicht in den Kandidaten", entry.get("item_id"))
            continue
        seen.add(item_id)

        source = registry.by_id(row["source"])
        resolved.append(
            {
                "item_id": item_id,
                "headline": (entry.get("headline") or row["title"]).strip(),
                "what": (entry.get("what") or row["summary"] or "").strip(),
                "einordnung": (entry.get("einordnung") or "").strip(),
                "url": row["url"],
                "source_name": source.name if source else row["source"],
                "published_at": row["published_at"],
                "score": row["score"],
            }
        )
    return resolved
