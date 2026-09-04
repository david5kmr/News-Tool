"""Monatliche Verdichtung.

Am Monatsersten laeuft der Vormonat je Themenstrang durch Sonnet. Die Briefe
gehen als Kontext in Digest und `mi ask` — das ist der Schritt, der aus einem
Nachrichtenstapel Marktkenntnis macht.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import db
from .config import Config
from .llm import LLM, interest_profile, load_prompt

log = logging.getLogger(__name__)

TOPIC_STREAMS = ("goae", "klinikmarkt", "wettbewerb", "politik")

# Items unterhalb dieses Scores sind Rauschen — sie bleiben im Archiv, aber
# im Monatsbrief haben sie nichts verloren.
MIN_SCORE_FOR_BRIEF = 3
MAX_ITEMS_PER_TOPIC = 120


@dataclass
class MonthlyStats:
    month: str = ""
    written: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"month": self.month, "written": self.written, "empty": self.empty}


def previous_month(today: date | None = None) -> str:
    today = today or date.today()
    year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return f"{year:04d}-{month:02d}"


def run(
    conn: sqlite3.Connection,
    config: Config,
    llm: LLM,
    *,
    month: str | None = None,
) -> MonthlyStats:
    month = month or previous_month()
    stats = MonthlyStats(month=month)

    for topic in TOPIC_STREAMS:
        rows = _items_for(conn, month, topic)
        if not rows:
            stats.empty.append(topic)
            log.info("Monat %s, Strang %s: keine Items", month, topic)
            continue

        text = llm.text(
            model=config.model_monthly,
            system=load_prompt(
                "monthly", PROFIL=interest_profile(), MONTH=month, TOPIC=topic
            ),
            messages=[{"role": "user", "content": _render(rows)}],
            max_tokens=4_000,
            effort="high",
        ).strip()

        db.upsert_monthly_brief(conn, month, topic, text, len(rows))
        conn.commit()
        stats.written.append(topic)
        log.info("Monatsbrief %s/%s aus %d Items geschrieben", month, topic, len(rows))

    return stats


def _items_for(conn: sqlite3.Connection, month: str, topic: str) -> list[sqlite3.Row]:
    """Items des Monats, deren topics-JSON den Strang enthaelt.

    LIKE auf dem JSON-Array reicht hier: die Strangnamen sind fest und
    ueberschneiden sich nicht als Teilzeichenketten.
    """
    return conn.execute(
        """
        SELECT id, title, source, published_at, summary, score, url
        FROM items
        WHERE score >= ?
          AND dedupe_of IS NULL
          AND strftime('%Y-%m', coalesce(published_at, fetched_at)) = ?
          AND topics LIKE ?
        ORDER BY score DESC, coalesce(published_at, fetched_at) ASC
        LIMIT ?
        """,
        (MIN_SCORE_FOR_BRIEF, month, f'%"{topic}"%', MAX_ITEMS_PER_TOPIC),
    ).fetchall()


def _render(rows: list[sqlite3.Row]) -> str:
    parts = [f"{len(rows)} Meldungen:\n"]
    for row in rows:
        parts.append(
            f"- [{(row['published_at'] or '')[:10]}] {row['title']} "
            f"({row['source']}, Score {row['score']})\n"
            f"  {row['summary'] or '—'}"
        )
    return "\n".join(parts)
