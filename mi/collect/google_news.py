"""News-Query-Collector.

Google News stellt fuer jede Suche einen RSS-Feed bereit; das ist die
einfachste Art, Themen abzudecken, fuer die es keinen Fachfeed gibt
(`Krankenhaus Insolvenz`, Wettbewerbernamen).
"""

from __future__ import annotations

from urllib.parse import quote_plus

from ..net import Fetcher, Response
from ..sources import Source
from .base import RawItem
from .rss import FEED_ACCEPT, parse_feed

BASE = "https://news.google.com/rss/search"
LOCALE = "hl=de&gl=DE&ceid=DE:de"

# Ohne Zeitfenster liefert die Suche jahrealte Treffer.
DEFAULT_WINDOW = "when:7d"


def query_url(query: str, *, window: str = DEFAULT_WINDOW) -> str:
    terms = f"{query} {window}".strip()
    return f"{BASE}?q={quote_plus(terms)}&{LOCALE}"


def fetch(
    source: Source,
    fetcher: Fetcher,
    *,
    text_limit: int = 20_000,
    window: str = DEFAULT_WINDOW,
    **_ignored,
) -> tuple[list[RawItem], Response]:
    items: list[RawItem] = []
    last: Response | None = None
    per_query = max(1, source.max_items_per_run // max(1, len(source.queries)))

    for query in source.queries:
        resp = fetcher.get(query_url(query, window=window), accept=FEED_ACCEPT)
        last = resp
        if not resp.ok:
            continue
        for item in parse_feed(resp.content, source, text_limit=text_limit)[:per_query]:
            item.extra["query"] = query
            items.append(item)

    if last is None:
        last = Response(url=BASE, status=0, text="", content=b"", headers={},
                        error="Keine queries definiert")
    return items, last
