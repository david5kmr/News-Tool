"""HTML-Collector fuer Quellen ohne Feed.

Liest die Uebersichtsseite, sammelt Links samt Ueberschrift und traegt sie als
Items ein. Der Volltext bleibt leer — der Prefilter arbeitet dann auf Titel
plus Quelle. Wer mehr braucht, setzt `html.follow_links: true`; das kostet
einen Abruf pro Treffer und ist nur bei kleinen Uebersichtsseiten sinnvoll.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..net import Fetcher, Response
from ..sources import Source
from .base import RawItem, clean_text, parse_date

# Links, die auf jeder Seite stehen und nie eine Meldung sind.
BOILERPLATE = {
    "impressum", "datenschutz", "kontakt", "suche", "startseite", "home",
    "newsletter", "cookie", "cookies", "barrierefreiheit", "sitemap", "login",
    "anmelden", "mehr", "weiter", "zurueck", "zurück", "alle anzeigen",
}
MIN_TITLE_WORDS = 3


def fetch(
    source: Source,
    fetcher: Fetcher,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    text_limit: int = 20_000,
) -> tuple[list[RawItem], Response]:
    url = source.url or source.homepage
    if not url:
        raise ValueError(f"Quelle {source.id} hat weder url noch homepage")

    resp = fetcher.get(url, etag=etag, last_modified=last_modified)
    if not resp.ok:
        return [], resp

    items = parse_listing(resp.text, resp.url, source)

    if source.html.get("follow_links"):
        for item in items:
            page = fetcher.get(item.url)
            if page.ok:
                item.raw_text = clean_text(page.text, limit=text_limit)

    return items, resp


def parse_listing(html: str, base_url: str, source: Source) -> list[RawItem]:
    soup = BeautifulSoup(html, "html.parser")
    selector = source.html.get("item_selector") or "main a, article a"
    date_selector = source.html.get("date_selector")

    base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    seen: set[str] = set()
    items: list[RawItem] = []

    for anchor in soup.select(selector):
        href = (anchor.get("href") or "").strip()
        title = " ".join(anchor.get_text(" ").split())
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        if len(title.split()) < MIN_TITLE_WORDS or title.casefold() in BOILERPLATE:
            continue

        url = urljoin(base_url, href)
        host = urlparse(url).netloc.lower().removeprefix("www.")
        # Fremde Domains auf einer Uebersichtsseite sind Werbung oder Partner.
        if base_host and host != base_host:
            continue
        if url in seen or url.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(url)

        items.append(
            RawItem(
                url=url,
                title=title,
                source=source.id,
                published_at=_nearby_date(anchor, date_selector),
            )
        )
        if len(items) >= source.max_items_per_run:
            break

    return items


def _nearby_date(anchor, date_selector: str | None) -> str | None:
    """Datum am Listeneintrag suchen: erst <time datetime=...>, dann Text."""
    for node in (anchor, anchor.parent, getattr(anchor.parent, "parent", None)):
        if node is None:
            continue
        candidates = node.select(date_selector) if date_selector else node.select("time")
        for candidate in candidates:
            parsed = parse_date(candidate.get("datetime") or candidate.get_text(" ").strip())
            if parsed:
                return parsed
    return None
