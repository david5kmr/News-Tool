"""Sofort-Alerts.

Score >= 8 UND ein Trigger. Beides, nicht eines von beiden — sonst kippt der
Kanal in Rauschen, und dann liest ihn nach zwei Wochen niemand mehr.
Deshalb auch das harte Tageslimit.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Sequence

from . import db
from .config import Config
from .mailer import Mail, send
from .render import alert_html, alert_subject, alert_text

log = logging.getLogger(__name__)

# Alle Muster stehen in transliterierter Form (ae/oe/ue/ss). `_norm` wandelt
# den Text vorher genauso um, damit "Tübingen" und "Tuebingen" beide treffen —
# Feeds schreiben mal so, mal so, und ein verpasster Alert ist teurer als eine
# haessliche Konstante.

# Watchlist Eigennamen — ein Treffer loest immer aus.
WATCHLIST: tuple[str, ...] = (
    "Schön Klinik",
    "ADK GmbH",
    "Kreiskrankenhaus Ehingen",
    "Dedalus",
    "ORBIS",
    "Doctario",
    "MediCoda",
    "Qodia",
    "Avelios",
    "Nelly",
    "Felia",
)

# Ereignisklasse 1: offizielle Aeusserung zu GOAEneu von BAEK, PKV oder BMG.
GOAE_TERMS = (r"goae[\s-]*neu", r"goae-novelle", r"neue\s+goae", r"\bgoae?\b")
GOAE_ACTORS = (
    r"bundesaerztekammer", r"\bbaek\b", r"pkv[- ]?verband",
    r"verband\s+der\s+privaten\s+krankenversicherung",
    r"bundesgesundheitsministerium", r"\bbmg\b", r"gesundheitsminister",
)

# Ereignisklasse 2: Gesetzesvorhaben mit Privatliquidations-Bezug erreicht eine
# neue Verfahrensstufe.
PRIVAT_TERMS = (
    r"privatliquidation", r"privataerztlich", r"wahlleistung",
    r"chefarztabrechnung", r"privatabrechnung", r"goae[\s-]*neu", r"\bgoae?\b",
)
VERFAHRENSSTUFEN = (
    r"referentenentwurf", r"regierungsentwurf", r"kabinettsbeschluss",
    r"kabinett\s+beschl", r"bundesrat", r"bundestag", r"erste\s+lesung",
    r"zweite\s+lesung", r"dritte\s+lesung", r"verkuendet",
    r"bundesgesetzblatt", r"in\s+kraft\s+getreten", r"anhoerung",
    r"verordnungsentwurf", r"zustimmungspflichtig",
)

# Ereignisklasse 3: Wettbewerber meldet Finanzierung, Klinikpartner, Launch.
WETTBEWERBER = (
    "doctario", "medicoda", "qodia", "avelios", "nelly", "felia", "dedalus",
    "orbis",
)
WETTBEWERBER_EVENTS = (
    r"finanzierungsrunde", r"series\s+[a-d]\b", r"seed[- ]?runde",
    r"millionen\s+eingesammelt", r"investment", r"kapitalerhoehung",
    r"neuer?\s+kunde", r"klinikpartner", r"partnerschaft", r"kooperation",
    r"launch", r"markteinfuehrung", r"produkt\w*\s+vorgestellt",
    r"uebernimmt", r"uebernahme", r"uebernommen",
)

# Ereignisklasse 4: Insolvenz/Uebernahme einer Klinikgruppe mit > 5 Standorten.
KLINIKGRUPPE = (
    r"klinikgruppe", r"klinikkonzern", r"klinikverbund", r"krankenhauskonzern",
    r"krankenhausgruppe", r"klinikkette",
)
GRUPPEN_EVENTS = (
    r"insolvenz", r"uebernahme", r"uebernimmt", r"uebernommen", r"verkauf",
    r"traegerwechsel", r"schutzschirmverfahren",
)
STANDORT_RE = re.compile(
    r"(\d{1,3})\s*(?:standorte|kliniken|haeuser|krankenhaeuser)", re.IGNORECASE
)
MIN_STANDORTE = 5

# Ereignisklasse 5: jedes Krankenhaus in Baden-Wuerttemberg mit
# Insolvenz/Traegerwechsel.
BW_TERMS = (
    r"baden[- ]wuerttemberg", r"\bbw\b", r"stuttgart", r"karlsruhe",
    r"freiburg", r"mannheim", r"heidelberg", r"\bulm\b", r"heilbronn",
    r"tuebingen", r"pforzheim", r"reutlingen", r"esslingen", r"ludwigsburg",
    r"konstanz", r"ravensburg", r"offenburg", r"villingen", r"schwenningen",
    r"aalen", r"goeppingen", r"ehingen", r"biberach", r"sigmaringen",
    r"alb[- ]donau",
)
BW_EVENTS = (
    r"insolvenz", r"traegerwechsel", r"schliessung", r"uebernahme",
    r"schutzschirmverfahren", r"verkauft",
)
KRANKENHAUS = (r"krankenhaus", r"klinik", r"klinikum", r"spital")


@dataclass
class Trigger:
    name: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.name}: {self.detail}" if self.detail else self.name


@dataclass
class AlertStats:
    checked: int = 0
    triggered: int = 0
    sent: int = 0
    deferred_to_digest: list[int] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "triggered": self.triggered,
            "sent": self.sent,
            "deferred_to_digest": self.deferred_to_digest,
            "triggers": self.triggers,
        }


# Umlaut-Transliteration. casefold() macht aus "ß" bereits "ss".
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})


def _norm(text: str) -> str:
    """Kleinschreibung plus Umlaut-Transliteration — so trifft ein Muster
    beide Schreibweisen, die in Feeds vorkommen."""
    return unicodedata.normalize("NFC", text or "").casefold().translate(_UMLAUTS)


def _any(patterns: Sequence[str], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def watchlist_hits(text: str, watchlist: Sequence[str] = WATCHLIST) -> list[str]:
    """Wortgrenzen-Treffer. Ohne \\b faengt 'Nelly' jedes 'Nellys' mit,
    aber auch jedes Vorkommen in einer laengeren Zeichenkette."""
    lowered = _norm(text)
    hits: list[str] = []
    for name in watchlist:
        # Auch der Watchlist-Name wird transliteriert, sonst findet
        # "Schön Klinik" das normalisierte "schoen klinik" nicht.
        parts = _norm(name).split()
        pattern = r"\b" + r"\s+".join(re.escape(part) for part in parts) + r"\b"
        if re.search(pattern, lowered):
            hits.append(name)
    return hits


def detect_triggers(item: dict[str, Any]) -> list[Trigger]:
    """Alle zutreffenden Trigger. Leere Liste = kein Alert."""
    text = _norm(
        " ".join(
            filter(
                None,
                [
                    str(item.get("title") or ""),
                    str(item.get("summary") or ""),
                    str(item.get("raw_text") or "")[:4000],
                ],
            )
        )
    )
    triggers: list[Trigger] = []

    if hits := watchlist_hits(text):
        triggers.append(Trigger("Watchlist", ", ".join(hits)))

    # 1 — offizielle Aeusserung zu GOAEneu
    goae = _any(GOAE_TERMS, text)
    actor = _any(GOAE_ACTORS, text)
    if goae and actor:
        triggers.append(Trigger("GOÄneu-Äußerung", f"{actor} zu {goae}"))

    # 2 — Gesetzesvorhaben erreicht neue Verfahrensstufe
    privat = _any(PRIVAT_TERMS, text)
    stufe = _any(VERFAHRENSSTUFEN, text)
    if privat and stufe:
        triggers.append(Trigger("Verfahrensstufe", f"{stufe} ({privat})"))

    # 3 — Wettbewerber meldet etwas
    competitor = next((c for c in WETTBEWERBER if re.search(rf"\b{c}\b", text)), None)
    event = _any(WETTBEWERBER_EVENTS, text)
    if competitor and event:
        triggers.append(Trigger("Wettbewerber", f"{competitor}: {event}"))

    # 4 — Klinikgruppe mit mehr als fuenf Standorten
    gruppe = _any(KLINIKGRUPPE, text)
    gruppen_event = _any(GRUPPEN_EVENTS, text)
    if gruppe and gruppen_event:
        standorte = _max_standorte(text)
        if standorte is None or standorte > MIN_STANDORTE:
            detail = f"{gruppe}, {gruppen_event}"
            if standorte is not None:
                detail += f", {standorte} Standorte"
            else:
                detail += ", Groesse unklar"
            triggers.append(Trigger("Klinikgruppe", detail))

    # 5 — Krankenhaus in Baden-Wuerttemberg
    if (
        _any(BW_TERMS, text)
        and _any(KRANKENHAUS, text)
        and (bw_event := _any(BW_EVENTS, text))
    ):
        triggers.append(Trigger("Baden-Württemberg", bw_event))

    return triggers


def _max_standorte(text: str) -> int | None:
    numbers = [int(m.group(1)) for m in STANDORT_RE.finditer(text)]
    return max(numbers) if numbers else None


def run(
    conn: sqlite3.Connection,
    config: Config,
    *,
    dry_run: bool = False,
) -> AlertStats:
    stats = AlertStats()
    already_sent = db.alerts_sent_today(conn)
    budget = max(0, config.max_alerts_per_day - already_sent)

    candidates = db.alert_candidates(conn, config.alert_min_score)
    stats.checked = len(candidates)

    for row in candidates:
        item = dict(row)
        triggers = detect_triggers(item)
        if not triggers:
            continue

        stats.triggered += 1
        label = " | ".join(str(t) for t in triggers)

        if budget <= 0:
            # Ueberzaehlige Treffer bleiben unversendet und wandern automatisch
            # in den Digest — `digested_at` ist noch NULL.
            stats.deferred_to_digest.append(int(row["id"]))
            log.info("Alert-Limit erreicht — Item %s geht in den Digest", row["id"])
            continue

        mail = Mail(
            subject=alert_subject(item["title"]),
            text=alert_text(item, label),
            html=alert_html(item, label),
        )
        if dry_run:
            print(mail.text)
            print("-" * 60)
        else:
            send(mail, config.mail)
            db.record_alert(conn, int(row["id"]), label)
            conn.commit()

        budget -= 1
        stats.sent += 1
        stats.triggers.append(label)
        log.info("Alert verschickt: %s (%s)", item["title"][:70], label)

    return stats
