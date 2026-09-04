"""Gemeinsame Bausteine der Collectoren."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

_WS_RE = re.compile(r"[ \t ]+")
_NL_RE = re.compile(r"\n{3,}")


@dataclass
class RawItem:
    """Ein eingesammelter Beitrag, noch ungefiltert und unbewertet."""

    url: str
    title: str
    source: str
    published_at: str | None = None
    raw_text: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def clean_text(html_or_text: str, *, limit: int | None = None) -> str:
    """HTML zu lesbarem Text. Skripte, Navigation und Fussleisten fliegen raus."""
    if not html_or_text:
        return ""
    if "<" in html_or_text and ">" in html_or_text:
        soup = BeautifulSoup(html_or_text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        text = soup.get_text("\n")
    else:
        text = html_or_text
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = _NL_RE.sub("\n\n", text).strip()
    if limit is not None and len(text) > limit:
        text = text[:limit].rstrip() + "\n[gekuerzt]"
    return text


def parse_date(value: Any) -> str | None:
    """Beliebiges Datumsformat zu ISO-8601 in UTC. None, wenn unlesbar."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (tuple, list)) and len(value) >= 6:
        try:
            dt = datetime(*[int(v) for v in value[:6]], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = date_parser.parse(text)
        except (ValueError, OverflowError, TypeError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def is_recent(published_at: str | None, *, max_age_days: int) -> bool:
    """Ohne Datum gilt ein Item als aktuell — lieber einmal zu viel pruefen."""
    if not published_at:
        return True
    try:
        dt = datetime.fromisoformat(published_at)
    except ValueError:
        return True
    age = datetime.now(timezone.utc) - dt
    return age.days <= max_age_days
