"""Archiv-Abfrage: `mi ask "Stand GOÄneu-Verhandlungen"`.

Antwortet aus dem eigenen Archiv statt aus dem Modellgedaechtnis — mit
Quellenlinks. Das ist der Teil, der in Investorengespraechen und
EXIST-Zwischenberichten gebraucht wird.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import db
from .config import Config
from .llm import LLM, interest_profile, load_prompt

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 25
SNIPPET_CHARS = 700

# FTS5-Syntaxzeichen; in einer Nutzerfrage sind sie Text, keine Operatoren.
_FTS_SPECIAL = re.compile(r'["\'()*:^-]')


@dataclass
class Answer:
    question: str
    text: str
    sources: list[dict[str, Any]] = field(default_factory=list)

    def render(self) -> str:
        lines = [self.text, ""]
        if self.sources:
            lines.append("Quellen")
            lines.append("-" * 60)
            for index, source in enumerate(self.sources, start=1):
                date = (source["published_at"] or "")[:10] or "ohne Datum"
                lines.append(f"[{index}] {source['title']}")
                lines.append(f"    {source['source']} · {date}")
                lines.append(f"    {source['url']}")
        return "\n".join(lines)


def fts_query(question: str) -> str:
    """Frage in eine FTS5-Query uebersetzen: Begriffe ODER-verknuepft.

    UND waere praeziser, liefert im Archiv aber oft null Treffer; die
    Reihenfolge macht bm25 danach ohnehin.
    """
    cleaned = _FTS_SPECIAL.sub(" ", question)
    terms = [t for t in re.findall(r"\w{3,}", cleaned, re.UNICODE)]
    if not terms:
        return ""
    return " OR ".join(f'"{term}"' for term in terms)


def search(
    conn: sqlite3.Connection, question: str, *, limit: int = DEFAULT_LIMIT
) -> list[sqlite3.Row]:
    query = fts_query(question)
    if not query:
        return []
    try:
        return conn.execute(
            """
            SELECT i.id, i.title, i.url, i.source, i.published_at, i.summary,
                   i.score, substr(coalesce(i.raw_text, ''), 1, ?) AS snippet
            FROM items_fts f
            JOIN items i ON i.id = f.rowid
            WHERE items_fts MATCH ?
            ORDER BY bm25(items_fts, 4.0, 2.0, 1.0), i.score DESC
            LIMIT ?
            """,
            (SNIPPET_CHARS, query, int(limit)),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        log.warning("FTS-Abfrage fehlgeschlagen (%s) — Fallback auf LIKE", exc)
        like = f"%{question.strip()}%"
        return conn.execute(
            """
            SELECT id, title, url, source, published_at, summary, score,
                   substr(coalesce(raw_text, ''), 1, ?) AS snippet
            FROM items
            WHERE title LIKE ? OR summary LIKE ?
            ORDER BY score DESC, id DESC LIMIT ?
            """,
            (SNIPPET_CHARS, like, like, int(limit)),
        ).fetchall()


def ask(
    conn: sqlite3.Connection,
    config: Config,
    llm: LLM,
    question: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> Answer:
    rows = search(conn, question, limit=limit)
    briefs = db.recent_briefs(conn, limit=12)

    if not rows and not briefs:
        return Answer(
            question=question,
            text="Das Archiv ist zu dieser Frage leer. Erst sammeln, dann fragen.",
        )

    text = llm.text(
        model=config.model_ask,
        system=load_prompt("ask", PROFIL=interest_profile()),
        messages=[{"role": "user", "content": _render_context(question, rows, briefs)}],
        max_tokens=8_000,
        effort="high",
    ).strip()

    return Answer(
        question=question,
        text=text,
        sources=[
            {
                "title": row["title"],
                "url": row["url"],
                "source": row["source"],
                "published_at": row["published_at"],
            }
            for row in rows
        ],
    )


def _render_context(
    question: str, rows: list[sqlite3.Row], briefs: list[sqlite3.Row]
) -> str:
    parts: list[str] = []

    if briefs:
        parts.append("## Monatsbriefe\n")
        for brief in briefs:
            parts.append(f"### {brief['month']} — {brief['topic']}\n{brief['text']}\n")

    parts.append(f"\n## Archiv-Auszuege ({len(rows)})\n")
    for index, row in enumerate(rows, start=1):
        parts.append(
            "\n".join(
                [
                    f"[{index}] {row['title']}",
                    f"    Quelle: {row['source']} · "
                    f"{(row['published_at'] or 'ohne Datum')[:10]} · "
                    f"Score {row['score']}",
                    f"    {row['summary'] or ''}",
                    f"    {(row['snippet'] or '').strip()[:500]}",
                ]
            )
        )

    parts.append(f"\n## Frage\n\n{question}")
    return "\n\n".join(parts)
