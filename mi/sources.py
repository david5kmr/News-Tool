"""Laden von sources.yaml und der generierten sources.lock.yaml.

sources.yaml ist handgepflegt und enthaelt Kommentare. Die Verifikation
schreibt deshalb nicht dorthin zurueck, sondern in eine Lockdatei — wie bei
einem Paketmanager. Effektive Quelle = sources.yaml + Lock-Eintrag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

LOCK_FILENAME = "sources.lock.yaml"

VALID_KINDS = {"rss", "html", "google_news", "website_diff", "api"}
VALID_STATUS = {"unverified", "verified", "no_feed", "broken", "manual"}
VALID_CADENCE = {"daily", "alerts", "weekly"}


@dataclass
class Source:
    id: str
    name: str
    kind: str
    topic: str = "sonstiges"
    cadence: str = "daily"
    status: str = "unverified"
    enabled: bool = True
    url: str | None = None
    homepage: str | None = None
    domain: str | None = None
    why: str = ""
    notes: str = ""
    candidates: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    html: dict[str, Any] = field(default_factory=dict)
    max_items_per_run: int = 40

    @property
    def is_usable(self) -> bool:
        """Laeuft die Quelle ohne `--allow-unverified`?"""
        if not self.enabled:
            return False
        if self.kind == "google_news":
            return bool(self.queries) and self.status in {"verified", "manual"}
        return bool(self.url) and self.status in {"verified", "manual"}

    def runs_in(self, cadence: str) -> bool:
        """`daily` schliesst die Alert-Quellen mit ein — was alle zwei Stunden
        geprueft wird, gehoert erst recht in den Tagesdigest."""
        if cadence == "daily":
            return self.cadence in {"daily", "alerts"}
        return self.cadence == cadence


@dataclass
class Competitor:
    id: str
    name: str
    pages: list[str]


@dataclass
class SourceRegistry:
    sources: list[Source]
    competitors: list[Competitor]
    defaults: dict[str, Any]
    path: Path

    def by_id(self, source_id: str) -> Source | None:
        return next((s for s in self.sources if s.id == source_id), None)

    def for_cadence(self, cadence: str) -> list[Source]:
        return [s for s in self.sources if s.enabled and s.runs_in(cadence)]


def load(path: Path, *, lock_path: Path | None = None) -> SourceRegistry:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults") or {}
    lock = _load_lock(lock_path if lock_path is not None else path.parent / LOCK_FILENAME)

    sources: list[Source] = []
    seen_ids: set[str] = set()
    for raw in data.get("sources") or []:
        src = _build_source(raw, defaults)
        if src.id in seen_ids:
            raise ValueError(f"Doppelte source-id in {path.name}: {src.id}")
        seen_ids.add(src.id)
        _apply_lock(src, lock.get(src.id))
        sources.append(src)

    competitors = [
        Competitor(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            pages=[str(p) for p in (raw.get("pages") or [])],
        )
        for raw in data.get("competitors") or []
    ]
    return SourceRegistry(
        sources=sources, competitors=competitors, defaults=defaults, path=path
    )


def _build_source(raw: dict[str, Any], defaults: dict[str, Any]) -> Source:
    missing = [k for k in ("id", "name", "kind") if not raw.get(k)]
    if missing:
        raise ValueError(f"Quelle ohne {', '.join(missing)}: {raw!r}")

    kind = str(raw["kind"])
    if kind not in VALID_KINDS:
        raise ValueError(f"Quelle {raw['id']}: unbekanntes kind {kind!r}")
    status = str(raw.get("status", "unverified"))
    if status not in VALID_STATUS:
        raise ValueError(f"Quelle {raw['id']}: unbekannter status {status!r}")
    cadence = str(raw.get("cadence") or defaults.get("cadence") or "daily")
    if cadence not in VALID_CADENCE:
        raise ValueError(f"Quelle {raw['id']}: unbekannte cadence {cadence!r}")

    return Source(
        id=str(raw["id"]),
        name=str(raw["name"]),
        kind=kind,
        topic=str(raw.get("topic") or "sonstiges"),
        cadence=cadence,
        status=status,
        enabled=bool(raw.get("enabled", True)),
        url=raw.get("url") or None,
        homepage=raw.get("homepage") or None,
        domain=raw.get("domain") or None,
        why=str(raw.get("why") or ""),
        notes=str(raw.get("notes") or ""),
        candidates=[str(c) for c in (raw.get("candidates") or [])],
        queries=[str(q) for q in (raw.get("queries") or [])],
        html=dict(raw.get("html") or {}),
        max_items_per_run=int(
            raw.get("max_items_per_run") or defaults.get("max_items_per_run") or 40
        ),
    )


def _apply_lock(src: Source, entry: dict[str, Any] | None) -> None:
    """Die Lockdatei darf `manual` nicht ueberschreiben — das ist eine
    bewusste Entscheidung von Hand."""
    if not entry or src.status == "manual":
        return
    if entry.get("url"):
        src.url = str(entry["url"])
    if entry.get("kind") in VALID_KINDS:
        src.kind = str(entry["kind"])
    if entry.get("status") in VALID_STATUS:
        src.status = str(entry["status"])


def _load_lock(lock_path: Path) -> dict[str, dict[str, Any]]:
    if not lock_path.exists():
        return {}
    data = yaml.safe_load(lock_path.read_text(encoding="utf-8")) or {}
    return {str(k): dict(v) for k, v in (data.get("sources") or {}).items()}


def write_lock(lock_path: Path, entries: dict[str, dict[str, Any]], *, checked_at: str) -> None:
    payload = {
        "# generiert von": "mi verify-sources — nicht von Hand bearbeiten",
        "checked_at": checked_at,
        "sources": entries,
    }
    lock_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=True), encoding="utf-8"
    )
