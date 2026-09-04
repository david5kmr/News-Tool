"""Doppelerkennung: URL-Hash exakt, Titel unscharf.

Nachrichtenagenturen streuen denselben Text ueber viele Portale; ohne die
zweite Stufe stehen im Digest drei Mal dieselbe Klinikinsolvenz.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Tracking-Parameter, die dieselbe Seite unter tausend URLs erscheinen lassen.
TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "at_")
TRACKING_PARAMS = {
    "fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "source",
    "cmp", "wt_mc", "xtor", "ocid", "cmpid", "sr_share",
}

# Deutsche Stoppwoerter, die zwei Titel aehnlicher wirken lassen, als sie sind.
STOPWORDS = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
    "eines", "und", "oder", "aber", "fuer", "für", "von", "vom", "mit", "auf",
    "zu", "zum", "zur", "im", "in", "am", "an", "ist", "sind", "wird", "werden",
    "bei", "nach", "aus", "als", "auch", "sich", "es", "so", "wie", "nicht",
}

_WORD_RE = re.compile(r"[a-z0-9äöüß]+")

TITLE_SIMILARITY_THRESHOLD = 0.86


def canonical_url(url: str) -> str:
    """Fragment und Tracking-Parameter weg, Host klein, Doppel-Slash raus."""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if (scheme == "https" and netloc.endswith(":443")) or (
        scheme == "http" and netloc.endswith(":80")
    ):
        netloc = netloc.rsplit(":", 1)[0]

    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
        and not k.lower().startswith(TRACKING_PREFIXES)
    ]
    path = re.sub(r"/{2,}", "/", parts.path)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()


def normalize_title(title: str) -> str:
    """Kleinschreibung, Umlaute erhalten, Satzzeichen und Stoppwoerter raus."""
    lowered = unicodedata.normalize("NFC", title or "").casefold()
    words = [w for w in _WORD_RE.findall(lowered) if w not in STOPWORDS]
    return " ".join(words)


def title_similarity(a: str, b: str) -> float:
    """Jaccard auf Wortmengen, kombiniert mit Zeichenaehnlichkeit.

    Jaccard allein greift bei Umstellungen zu kurz, SequenceMatcher allein
    haelt zwei verschiedene Meldungen aus derselben Quelle faelschlich fuer
    identisch. Das Maximum beider ist in der Praxis das robustere Signal.
    """
    if not a or not b:
        return 0.0
    set_a, set_b = set(a.split()), set(b.split())
    if not set_a or not set_b:
        return 0.0
    jaccard = len(set_a & set_b) / len(set_a | set_b)
    ratio = SequenceMatcher(None, a, b).ratio()
    return max(jaccard, ratio)


def find_duplicate(
    title_norm: str,
    known: list[tuple[int, str]],
    *,
    threshold: float = TITLE_SIMILARITY_THRESHOLD,
) -> int | None:
    """Erste id aus `known`, deren Titel nah genug ist — sonst None."""
    if not title_norm:
        return None
    best_id: int | None = None
    best_score = threshold
    for item_id, other in known:
        score = title_similarity(title_norm, other)
        if score >= best_score:
            best_score = score
            best_id = item_id
            if score >= 0.99:
                break
    return best_id
