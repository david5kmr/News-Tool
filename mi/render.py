"""Mail-Rendering: Text und HTML aus denselben Daten.

Kein Template-Framework — die zwei Layouts sind ueberschaubar, und eine
Abhaengigkeit weniger heisst ein Update weniger.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Sequence

STYLE = """
body{margin:0;padding:0;background:#f6f6f4;}
.wrap{max-width:640px;margin:0 auto;padding:24px 16px 40px;
 font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
 font-size:15px;line-height:1.55;color:#1c1c1a;}
h1{font-size:19px;margin:0 0 4px;letter-spacing:-0.01em;}
.meta{color:#7a7a72;font-size:13px;margin:0 0 24px;}
.item{background:#fff;border:1px solid #e6e6e0;border-radius:8px;
 padding:16px 18px;margin:0 0 14px;}
.item h2{font-size:16px;margin:0 0 6px;line-height:1.35;}
.item .src{color:#7a7a72;font-size:12px;margin:0 0 10px;
 text-transform:uppercase;letter-spacing:0.04em;}
.item p{margin:0 0 10px;}
.einordnung{background:#f2f5ee;border-left:3px solid #6b8e4e;
 padding:9px 12px;border-radius:0 4px 4px 0;margin:0 0 10px;}
.einordnung strong{color:#4a6636;font-size:12px;text-transform:uppercase;
 letter-spacing:0.05em;display:block;margin-bottom:2px;}
a{color:#2f5d8c;}
.ahead{background:#fff;border:1px solid #e6e6e0;border-radius:8px;
 padding:16px 18px;margin:24px 0 0;}
.ahead h3{font-size:14px;margin:0 0 8px;text-transform:uppercase;
 letter-spacing:0.05em;color:#7a7a72;}
.alert{background:#fff;border:1px solid #e0c9c9;border-left:4px solid #b4483c;
 border-radius:8px;padding:16px 18px;}
.alert .trigger{color:#b4483c;font-size:12px;text-transform:uppercase;
 letter-spacing:0.05em;margin:0 0 8px;font-weight:600;}
.foot{color:#9a9a92;font-size:12px;margin-top:28px;
 border-top:1px solid #e6e6e0;padding-top:12px;}
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{escape(title)}</title><style>{STYLE}</style></head>"
        f"<body><div class=\"wrap\">{body}</div></body></html>"
    )


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "ohne Datum"
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y")
    except ValueError:
        return iso[:10]


# ------------------------------------------------------------------- digest

def digest_subject(subject_suffix: str, *, today: datetime | None = None) -> str:
    day = (today or datetime.now()).strftime("%d.%m.")
    suffix = (subject_suffix or "").strip()
    return f"Marktbrief {day} — {suffix}" if suffix else f"Marktbrief {day}"


def digest_text(items: Sequence[dict[str, Any]], week_ahead: str, meta: str) -> str:
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        lines.append(f"{index}. {item['headline']}")
        lines.append(f"   {item['source_name']} · {_fmt_date(item['published_at'])}")
        lines.append(f"   {item['what']}")
        lines.append(f"   Einordnung: {item['einordnung']}")
        lines.append(f"   {item['url']}")
        lines.append("")
    if week_ahead:
        lines.append("Diese Woche im Blick")
        lines.append("-" * 40)
        lines.append(week_ahead)
        lines.append("")
    lines.append(meta)
    return "\n".join(lines)


def digest_html(items: Sequence[dict[str, Any]], week_ahead: str, meta: str,
                subject: str) -> str:
    blocks = [
        f"<h1>{escape(subject)}</h1>",
        f"<p class=\"meta\">{escape(meta)}</p>",
    ]
    for item in items:
        blocks.append(
            "<div class=\"item\">"
            f"<h2><a href=\"{escape(item['url'])}\">{escape(item['headline'])}</a></h2>"
            f"<p class=\"src\">{escape(item['source_name'])} &middot; "
            f"{escape(_fmt_date(item['published_at']))}</p>"
            f"<p>{escape(item['what'])}</p>"
            "<div class=\"einordnung\"><strong>Einordnung</strong>"
            f"{escape(item['einordnung'])}</div>"
            f"<p><a href=\"{escape(item['url'])}\">Zur Quelle</a></p>"
            "</div>"
        )
    if week_ahead:
        blocks.append(
            "<div class=\"ahead\"><h3>Diese Woche im Blick</h3>"
            f"<p>{escape(week_ahead).replace(chr(10), '<br>')}</p></div>"
        )
    blocks.append(f"<p class=\"foot\">{escape(meta)}</p>")
    return _page(subject, "".join(blocks))


# -------------------------------------------------------------------- alert

def alert_subject(title: str) -> str:
    short = title if len(title) <= 70 else title[:67].rstrip() + "…"
    return f"[Alert] {short}"


def alert_text(item: dict[str, Any], trigger: str) -> str:
    return "\n".join(
        [
            f"ALERT — {trigger}",
            "",
            item["title"],
            f"{item['source']} · {_fmt_date(item.get('published_at'))} · "
            f"Score {item.get('score')}",
            "",
            item.get("summary") or "(keine Zusammenfassung)",
            "",
            item["url"],
        ]
    )


def alert_html(item: dict[str, Any], trigger: str) -> str:
    body = (
        "<div class=\"alert\">"
        f"<p class=\"trigger\">Alert &middot; {escape(trigger)}</p>"
        f"<h2><a href=\"{escape(item['url'])}\">{escape(item['title'])}</a></h2>"
        f"<p class=\"src\">{escape(item['source'])} &middot; "
        f"{escape(_fmt_date(item.get('published_at')))} &middot; "
        f"Score {escape(str(item.get('score')))}</p>"
        f"<p>{escape(item.get('summary') or '')}</p>"
        f"<p><a href=\"{escape(item['url'])}\">Zur Quelle</a></p>"
        "</div>"
    )
    return _page(alert_subject(item["title"]), body)


# ------------------------------------------------------------ competitor diff

def competitor_subject(changed: int) -> str:
    if changed == 1:
        return "Wettbewerber-Check — 1 Seite geaendert"
    return f"Wettbewerber-Check — {changed} Seiten geaendert"


def competitor_text(changes: Sequence[dict[str, Any]]) -> str:
    if not changes:
        return "Keine Aenderungen auf den beobachteten Seiten."
    lines: list[str] = []
    for change in changes:
        lines.append(f"{change['competitor']} — {change['url']}")
        for line in change["added"][:12]:
            lines.append(f"  + {line}")
        for line in change["removed"][:6]:
            lines.append(f"  - {line}")
        lines.append("")
    return "\n".join(lines)


def competitor_html(changes: Sequence[dict[str, Any]]) -> str:
    if not changes:
        body = "<h1>Wettbewerber-Check</h1><p>Keine Aenderungen.</p>"
        return _page("Wettbewerber-Check", body)

    blocks = ["<h1>Wettbewerber-Check</h1>"]
    for change in changes:
        added = "".join(
            f"<p style=\"margin:0;color:#2d6a2d\">+ {escape(line)}</p>"
            for line in change["added"][:12]
        )
        removed = "".join(
            f"<p style=\"margin:0;color:#8c3a3a\">− {escape(line)}</p>"
            for line in change["removed"][:6]
        )
        blocks.append(
            "<div class=\"item\">"
            f"<h2>{escape(change['competitor'])}</h2>"
            f"<p class=\"src\"><a href=\"{escape(change['url'])}\">"
            f"{escape(change['url'])}</a></p>{added}{removed}</div>"
        )
    return _page("Wettbewerber-Check", "".join(blocks))
