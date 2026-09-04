"""RSS-/Atom-Collector."""

from __future__ import annotations

import feedparser

from ..net import Fetcher, Response
from ..sources import Source
from .base import RawItem, clean_text, parse_date

FEED_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"


def fetch(
    source: Source,
    fetcher: Fetcher,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    text_limit: int = 20_000,
) -> tuple[list[RawItem], Response]:
    """Feed abrufen und in RawItems uebersetzen.

    Bedingte Requests (ETag/Last-Modified) sparen Bandbreite und machen den
    Zwei-Stunden-Takt der Alert-Quellen fuer die Gegenseite unauffaellig.
    """
    if not source.url:
        raise ValueError(f"Quelle {source.id} hat keine verifizierte URL")

    resp = fetcher.get(
        source.url, etag=etag, last_modified=last_modified, accept=FEED_ACCEPT
    )
    if not resp.ok:
        return [], resp

    return parse_feed(resp.content, source, text_limit=text_limit), resp


def parse_feed(payload: bytes | str, source: Source, *, text_limit: int = 20_000) -> list[RawItem]:
    parsed = feedparser.parse(payload)
    items: list[RawItem] = []
    for entry in parsed.entries[: source.max_items_per_run]:
        link = (entry.get("link") or "").strip()
        title = (entry.get("title") or "").strip()
        if not link or not title:
            continue
        items.append(
            RawItem(
                url=link,
                title=title,
                source=source.id,
                published_at=parse_date(
                    entry.get("published_parsed")
                    or entry.get("updated_parsed")
                    or entry.get("published")
                    or entry.get("updated")
                ),
                raw_text=clean_text(_entry_text(entry), limit=text_limit),
                extra={"feed_title": parsed.feed.get("title", "") if parsed.feed else ""},
            )
        )
    return items


def _entry_text(entry) -> str:
    """Volltext bevorzugen, sonst Zusammenfassung — was der Feed hergibt."""
    contents = entry.get("content") or []
    if contents:
        joined = "\n\n".join(c.get("value", "") for c in contents if c.get("value"))
        if joined.strip():
            return joined
    return entry.get("summary") or entry.get("description") or ""
