"""Bauschritt 1: Feed-URLs verifizieren statt raten.

Fuer jede Quelle:
  1. `candidates` der Reihe nach abrufen und pruefen, ob wirklich ein
     Feed zurueckkommt (feedparser + mindestens ein Eintrag).
  2. Wenn keiner traegt: Autodiscovery auf der `homepage`
     (<link rel="alternate" type="application/rss+xml">).
  3. Wenn auch das nichts bringt: gaengige Pfade unter der Homepage probieren.
  4. Wenn immer noch nichts: status `no_feed` — dann ist ein HTML-Scraper
     mit Change-Detection auf der Uebersichtsseite noetig.

Ergebnis geht in sources.lock.yaml, damit sources.yaml samt Kommentaren
handgepflegt bleibt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import feedparser

from .db import utcnow
from .net import Fetcher, Response
from .sources import Source, SourceRegistry, write_lock

COMMON_FEED_PATHS = (
    "/feed",
    "/feed/",
    "/rss",
    "/rss.xml",
    "/index.xml",
    "/atom.xml",
    "/feed.xml",
    "/news/feed",
    "/presse/feed",
    "/aktuelles/feed",
)

FEED_LINK_RE = re.compile(
    r"""<link\b[^>]*?
        (?=[^>]*\brel\s*=\s*["']?alternate)
        [^>]*?\btype\s*=\s*["'](?P<type>application/(?:rss|atom)\+xml)["']
        [^>]*?>""",
    re.IGNORECASE | re.VERBOSE,
)
HREF_RE = re.compile(r"""\bhref\s*=\s*["'](?P<href>[^"']+)["']""", re.IGNORECASE)

GOOGLE_NEWS_PROBE = (
    "https://news.google.com/rss/search?q=Krankenhaus&hl=de&gl=DE&ceid=DE:de"
)


@dataclass
class VerifyResult:
    source_id: str
    status: str                 # verified | no_feed | broken | manual | skipped
    url: str | None = None
    kind: str | None = None
    entries: int = 0
    tried: list[str] = None     # type: ignore[assignment]
    note: str = ""

    def __post_init__(self) -> None:
        if self.tried is None:
            self.tried = []


def looks_like_feed(resp: Response) -> tuple[bool, int]:
    """Ein 200er reicht nicht — viele Seiten liefern die HTML-404 mit Status 200."""
    if not resp.ok or not resp.content:
        return False, 0
    ctype = (resp.headers.get("Content-Type") or "").lower()
    body_head = resp.text[:400].lstrip().lower()
    xml_ish = (
        "xml" in ctype
        or "rss" in ctype
        or body_head.startswith("<?xml")
        or "<rss" in body_head
        or "<feed" in body_head
    )
    if not xml_ish:
        return False, 0
    parsed = feedparser.parse(resp.content)
    entries = len(parsed.entries or [])
    if parsed.bozo and entries == 0:
        return False, 0
    return entries > 0, entries


def discover_feed_links(html: str, base_url: str) -> list[str]:
    """<link rel="alternate" type="application/rss+xml" href="..."> einsammeln."""
    found: list[str] = []
    for match in FEED_LINK_RE.finditer(html):
        href_match = HREF_RE.search(match.group(0))
        if not href_match:
            continue
        url = urljoin(base_url, href_match.group("href").strip())
        if url not in found:
            found.append(url)
    return found


def _candidate_paths(homepage: str) -> list[str]:
    parsed = urlparse(homepage)
    if not parsed.scheme or not parsed.netloc:
        return []
    root = f"{parsed.scheme}://{parsed.netloc}"
    return [root + path for path in COMMON_FEED_PATHS]


def verify_source(source: Source, fetcher: Fetcher) -> VerifyResult:
    """Kandidaten, dann Homepage-Autodiscovery, dann geratene Pfade."""
    if not source.enabled or source.status == "manual":
        return VerifyResult(
            source.id,
            status="manual" if source.status == "manual" else "skipped",
            note=source.notes or "in sources.yaml deaktiviert",
        )
    if source.kind == "google_news":
        return _verify_google_news(source, fetcher)

    tried: list[str] = []

    def try_urls(urls: Iterable[str]) -> VerifyResult | None:
        for url in urls:
            if url in tried:
                continue
            tried.append(url)
            resp = fetcher.get(
                url, accept="application/rss+xml, application/xml;q=0.9, */*;q=0.5"
            )
            is_feed, entries = looks_like_feed(resp)
            if is_feed:
                return VerifyResult(
                    source.id,
                    status="verified",
                    url=resp.url,
                    kind="rss",
                    entries=entries,
                    tried=tried,
                    note=f"{entries} Eintraege",
                )
        return None

    explicit = list(source.candidates) + ([source.url] if source.url else [])
    result = try_urls([u for u in explicit if u])
    if result:
        return result

    if source.homepage:
        home = fetcher.get(source.homepage)
        if home.ok:
            discovered = discover_feed_links(home.text, home.url)
            result = try_urls(discovered)
            if result:
                result.note += " (per Autodiscovery gefunden)"
                return result
            result = try_urls(_candidate_paths(source.homepage))
            if result:
                result.note += " (geratener Pfad — im Browser gegenpruefen)"
                return result
            return VerifyResult(
                source.id,
                status="no_feed",
                url=home.url,
                kind="html",
                tried=tried,
                note="Kein Feed — HTML-Scraper noetig, html-Selektoren pruefen",
            )
        return VerifyResult(
            source.id,
            status="broken",
            tried=tried,
            note=f"Homepage nicht erreichbar: {home.error or home.status}",
        )

    return VerifyResult(
        source.id, status="broken", tried=tried, note="Keine Kandidaten, keine Homepage"
    )


def _verify_google_news(source: Source, fetcher: Fetcher) -> VerifyResult:
    if not source.queries:
        return VerifyResult(source.id, status="broken", note="Keine queries definiert")
    resp = fetcher.get(GOOGLE_NEWS_PROBE)
    is_feed, entries = looks_like_feed(resp)
    if is_feed:
        return VerifyResult(
            source.id,
            status="verified",
            kind="google_news",
            entries=entries,
            tried=[GOOGLE_NEWS_PROBE],
            note=f"News-Endpunkt antwortet ({entries} Eintraege bei der Testabfrage)",
        )
    return VerifyResult(
        source.id,
        status="broken",
        tried=[GOOGLE_NEWS_PROBE],
        note=f"News-Endpunkt liefert keinen Feed: {resp.error or resp.status}",
    )


def verify_registry(
    registry: SourceRegistry,
    fetcher: Fetcher,
    *,
    only: set[str] | None = None,
) -> list[VerifyResult]:
    results: list[VerifyResult] = []
    for source in registry.sources:
        if only and source.id not in only:
            continue
        results.append(verify_source(source, fetcher))
    return results


def save_results(lock_path: Path, results: list[VerifyResult]) -> None:
    entries = {
        r.source_id: {
            "status": r.status,
            "url": r.url,
            "kind": r.kind,
            "entries": r.entries,
            "note": r.note,
            "tried": r.tried,
        }
        for r in results
        if r.status != "skipped"
    }
    write_lock(lock_path, entries, checked_at=utcnow())
