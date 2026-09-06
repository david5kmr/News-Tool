"""Vorflugkontrolle: laeuft dieses System ueberhaupt schon?

Die geplanten Workflows feuern ab dem ersten Push. Ohne diese Pruefung
scheitern sie alle zwei Stunden an fehlender Konfiguration und schicken
Fehlermails — das Gegenteil von dem, was das System leisten soll.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .sources import load as load_sources

CHECKS = ("sources", "anthropic", "mail")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def has_anthropic_access(config: Config) -> bool:
    return bool(
        config.anthropic_api_key
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or (Path.home() / ".config" / "anthropic").exists()
    )


def check_sources(config: Config) -> Check:
    try:
        registry = load_sources(config.sources_path)
    except (OSError, ValueError) as exc:
        return Check("Quellen", False, f"sources.yaml unlesbar: {exc}",
                     "Datei pruefen")
    usable = [s for s in registry.sources if s.is_usable]
    if usable:
        return Check("Quellen", True, f"{len(usable)} verifiziert")
    return Check(
        "Quellen", False, "keine einzige verifiziert — Bauschritt 1 offen",
        "`mi verify-sources` lokal ausfuehren und sources.lock.yaml committen",
    )


def check_anthropic(config: Config) -> Check:
    if has_anthropic_access(config):
        return Check("Anthropic", True, "Zugang vorhanden")
    return Check(
        "Anthropic", False, "kein API-Key",
        "Secret ANTHROPIC_API_KEY im Repository setzen",
    )


def check_mail(config: Config) -> Check:
    mail = config.mail
    if mail.backend == "console":
        return Check("Versand", True, "console (Trockenlauf, verschickt nichts)")
    if not mail.recipients:
        return Check("Versand", False, "kein Empfaenger",
                     "Secret MI_MAIL_TO setzen")
    if mail.backend == "resend" and not mail.resend_api_key:
        return Check("Versand", False, "resend ohne Schluessel",
                     "Secret RESEND_API_KEY setzen")
    if mail.backend == "smtp" and not mail.smtp_host:
        return Check("Versand", False, "smtp ohne Host", "Secret SMTP_HOST setzen")
    return Check("Versand", True, f"{mail.backend} an {len(mail.recipients)} Empfaenger")


def run(config: Config, required: tuple[str, ...] = CHECKS) -> list[Check]:
    runners = {
        "sources": check_sources,
        "anthropic": check_anthropic,
        "mail": check_mail,
    }
    return [runners[name](config) for name in required if name in runners]


def render(checks: list[Check]) -> str:
    lines = ["Vorflugkontrolle", "-" * 60]
    for check in checks:
        mark = "OK  " if check.ok else "FEHLT"
        lines.append(f"  {mark} {check.name:<12} {check.detail}")
        if not check.ok and check.fix:
            lines.append(f"       → {check.fix}")
    if all(c.ok for c in checks):
        lines.append("")
        lines.append("Alles da. Der Lauf kann starten.")
    else:
        lines.append("")
        lines.append(
            "Noch nicht startklar — der Lauf wird uebersprungen (kein Fehler)."
        )
    return "\n".join(lines)
