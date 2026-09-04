"""Bauschritt 7: Wettbewerber-Website-Diff, woechentlich.

Kein Feed, sondern Change-Detection: Seite holen, auf Text reduzieren, mit dem
letzten Stand vergleichen. Interessant sind neue Zeilen — eine neue
Stellenanzeige oder ein neuer Produktabschnitt sagt mehr als jede
Pressemitteilung.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from difflib import unified_diff
from typing import Any

from .. import db
from ..config import Config
from ..net import Fetcher
from ..sources import Competitor
from .base import clean_text

log = logging.getLogger(__name__)

# Zeilen, die sich bei jedem Abruf aendern und keine Neuigkeit sind.
NOISE = re.compile(
    r"^\s*(?:\d{1,2}[.:]\d{2}|©\s*\d{4}|cookie|datenschutz|impressum|"
    r"alle rechte vorbehalten|zuletzt aktualisiert)",
    re.IGNORECASE,
)
MIN_LINE_LEN = 12
MAX_SNAPSHOT_CHARS = 60_000


@dataclass
class PageChange:
    competitor: str
    url: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "competitor": self.competitor,
            "url": self.url,
            "added": self.added,
            "removed": self.removed,
        }


@dataclass
class DiffStats:
    pages_checked: int = 0
    pages_failed: list[str] = field(default_factory=list)
    first_seen: list[str] = field(default_factory=list)
    changes: list[PageChange] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages_checked": self.pages_checked,
            "pages_failed": self.pages_failed,
            "first_seen": self.first_seen,
            "changes": [c.as_dict() for c in self.changes],
        }


def normalize_lines(text: str) -> list[str]:
    """Textzeilen ohne Rauschen und ohne Fragmente."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if len(line) < MIN_LINE_LEN or NOISE.match(line):
            continue
        lines.append(line)
    return lines


def diff_lines(old: list[str], new: list[str]) -> tuple[list[str], list[str]]:
    added: list[str] = []
    removed: list[str] = []
    for line in unified_diff(old, new, lineterm="", n=0):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added.append(line[1:].strip())
        elif line.startswith("-"):
            removed.append(line[1:].strip())
    return added, removed


def run(
    conn: sqlite3.Connection,
    competitors: list[Competitor],
    config: Config,
) -> DiffStats:
    stats = DiffStats()
    fetcher = Fetcher(user_agent=config.user_agent, timeout=config.http_timeout,
                      delay=1.5)
    try:
        for competitor in competitors:
            for url in competitor.pages:
                _check_page(conn, competitor, url, fetcher, stats)
    finally:
        fetcher.close()
    conn.commit()
    return stats


def _check_page(
    conn: sqlite3.Connection,
    competitor: Competitor,
    url: str,
    fetcher: Fetcher,
    stats: DiffStats,
) -> None:
    resp = fetcher.get(url)
    stats.pages_checked += 1
    if not resp.ok:
        stats.pages_failed.append(url)
        log.warning("Wettbewerberseite %s: %s", url, resp.error or resp.status)
        return

    text = clean_text(resp.text, limit=MAX_SNAPSHOT_CHARS)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    previous = conn.execute(
        """
        SELECT content_hash, text FROM page_snapshots
        WHERE url = ? ORDER BY fetched_at DESC, id DESC LIMIT 1
        """,
        (url,),
    ).fetchone()

    if previous is None:
        _store(conn, competitor.id, url, content_hash, text)
        stats.first_seen.append(url)
        log.info("Wettbewerberseite %s erstmals erfasst — Basis gelegt", url)
        return

    if previous["content_hash"] == content_hash:
        return

    added, removed = diff_lines(
        normalize_lines(previous["text"]), normalize_lines(text)
    )
    _store(conn, competitor.id, url, content_hash, text)

    if not added and not removed:
        # Der Hash aendert sich auch bei einem umsortierten Skript-Tag.
        return

    stats.changes.append(
        PageChange(competitor=competitor.name, url=url, added=added, removed=removed)
    )
    log.info("Wettbewerberseite %s: +%d/-%d Zeilen", url, len(added), len(removed))


def _store(
    conn: sqlite3.Connection, competitor_id: str, url: str, content_hash: str, text: str
) -> None:
    conn.execute(
        """
        INSERT INTO page_snapshots (competitor_id, url, content_hash, text, fetched_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (competitor_id, url, content_hash, text, db.utcnow()),
    )
    # Nur die letzten fuenf Staende je Seite behalten — mehr braucht der Diff nicht.
    conn.execute(
        """
        DELETE FROM page_snapshots
        WHERE url = ? AND id NOT IN (
            SELECT id FROM page_snapshots WHERE url = ?
            ORDER BY fetched_at DESC, id DESC LIMIT 5
        )
        """,
        (url, url),
    )
