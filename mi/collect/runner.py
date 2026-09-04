"""Orchestriert die Collectoren und schreibt in die Archiv-DB."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Callable

from .. import db
from ..config import Config
from ..dedupe import find_duplicate, normalize_title, url_hash
from ..net import Fetcher, Response
from ..sources import Source, SourceRegistry
from . import google_news, html as html_collector, rss
from .base import RawItem, is_recent

log = logging.getLogger(__name__)

Collector = Callable[..., tuple[list[RawItem], Response]]

COLLECTORS: dict[str, Collector] = {
    "rss": rss.fetch,
    "html": html_collector.fetch,
    "google_news": google_news.fetch,
}

# Nach so vielen Fehlversuchen in Folge ist eine Quelle kaputt, nicht zickig.
ERROR_STREAK_WARN = 3
MAX_AGE_DAYS = 21


@dataclass
class CollectStats:
    sources_run: int = 0
    sources_skipped: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)
    fetched: int = 0
    stored: int = 0
    duplicates_url: int = 0
    duplicates_title: int = 0
    too_old: int = 0
    not_modified: int = 0

    def as_dict(self) -> dict:
        return {
            "sources_run": self.sources_run,
            "sources_skipped": self.sources_skipped,
            "sources_failed": self.sources_failed,
            "fetched": self.fetched,
            "stored": self.stored,
            "duplicates_url": self.duplicates_url,
            "duplicates_title": self.duplicates_title,
            "too_old": self.too_old,
            "not_modified": self.not_modified,
        }


def collect(
    conn: sqlite3.Connection,
    registry: SourceRegistry,
    config: Config,
    *,
    cadence: str = "daily",
    allow_unverified: bool = False,
    only: set[str] | None = None,
) -> CollectStats:
    stats = CollectStats()
    fetcher = Fetcher(user_agent=config.user_agent, timeout=config.http_timeout)

    try:
        for source in registry.for_cadence(cadence):
            if only and source.id not in only:
                continue
            if not source.is_usable and not allow_unverified:
                stats.sources_skipped.append(source.id)
                log.info(
                    "Quelle %s uebersprungen (status=%s, url=%s) — "
                    "erst `mi verify-sources` laufen lassen",
                    source.id, source.status, source.url,
                )
                continue
            if not source.is_usable:
                log.warning(
                    "Quelle %s laeuft UNVERIFIZIERT (status=%s) — Treffer sind geraten",
                    source.id, source.status,
                )
            _collect_one(conn, source, fetcher, config, stats)
    finally:
        fetcher.close()

    return stats


def _collect_one(
    conn: sqlite3.Connection,
    source: Source,
    fetcher: Fetcher,
    config: Config,
    stats: CollectStats,
) -> None:
    collector = COLLECTORS.get(source.kind)
    if collector is None:
        stats.sources_skipped.append(source.id)
        log.info("Quelle %s: kind=%s hat keinen Collector", source.id, source.kind)
        return

    state = db.get_source_state(conn, source.id)
    try:
        items, resp = collector(
            source,
            fetcher,
            etag=state["etag"] if state else None,
            last_modified=state["last_modified"] if state else None,
            text_limit=config.raw_text_limit,
        )
    except Exception as exc:  # eine kaputte Quelle darf den Lauf nicht kippen
        streak = db.record_source_error(conn, source.id, f"{type(exc).__name__}: {exc}")
        stats.sources_failed.append(source.id)
        log.exception("Quelle %s fehlgeschlagen (Serie: %d)", source.id, streak)
        return

    stats.sources_run += 1

    if resp.not_modified:
        stats.not_modified += 1
        db.record_source_success(conn, source.id, etag=resp.headers.get("ETag"),
                                 last_modified=resp.headers.get("Last-Modified"),
                                 items_seen=0)
        return

    if not resp.ok and not items:
        streak = db.record_source_error(
            conn, source.id, resp.error or f"HTTP {resp.status}"
        )
        stats.sources_failed.append(source.id)
        level = logging.ERROR if streak >= ERROR_STREAK_WARN else logging.WARNING
        log.log(level, "Quelle %s: %s (Serie: %d)", source.id,
                resp.error or f"HTTP {resp.status}", streak)
        return

    stored = _store_items(conn, items, config, stats)
    db.record_source_success(
        conn,
        source.id,
        etag=resp.headers.get("ETag"),
        last_modified=resp.headers.get("Last-Modified"),
        items_seen=stored,
    )
    conn.commit()
    log.info("Quelle %s: %d geholt, %d neu", source.id, len(items), stored)


def _store_items(
    conn: sqlite3.Connection,
    items: list[RawItem],
    config: Config,
    stats: CollectStats,
) -> int:
    """Speichert neue Items. Duplikate werden nicht verworfen, sondern mit
    `dedupe_of` verknuepft — das Archiv soll vollstaendig bleiben."""
    known_titles = db.recent_titles(conn)
    stored = 0

    for item in items:
        stats.fetched += 1

        if not is_recent(item.published_at, max_age_days=MAX_AGE_DAYS):
            stats.too_old += 1
            continue

        hashed = url_hash(item.url)
        if db.url_hash_exists(conn, hashed):
            stats.duplicates_url += 1
            continue

        title_norm = normalize_title(item.title)
        duplicate_of = find_duplicate(title_norm, known_titles)
        if duplicate_of is not None:
            stats.duplicates_title += 1

        item_id = db.insert_item(
            conn,
            {
                "url": item.url,
                "url_hash": hashed,
                "title": item.title,
                "title_norm": title_norm,
                "source": item.source,
                "published_at": item.published_at,
                "raw_text": item.raw_text,
                "dedupe_of": duplicate_of,
            },
        )
        if item_id is None:
            stats.duplicates_url += 1
            continue

        known_titles.append((item_id, title_norm))
        stored += 1
        stats.stored += 1

    return stored
