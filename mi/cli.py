"""Kommandozeile: `mi <befehl>`.

    mi verify-sources     Bauschritt 1 — Feeds pruefen, Lockdatei schreiben
    mi collect            Bauschritt 2 — sammeln und speichern
    mi prefilter          Bauschritt 3 — bewerten
    mi calibrate          Bauschritt 3 — Schwellen an echten Daten einstellen
    mi digest             Bauschritt 4 — Marktbrief bauen und verschicken
    mi alerts             Bauschritt 5 — Trigger pruefen, sofort melden
    mi preflight          Ist das System startklar?
    mi ask "..."          Bauschritt 6 — Archiv befragen
    mi monthly            Bauschritt 6 — Monat verdichten
    mi competitors        Bauschritt 7 — Wettbewerberseiten diffen
    mi status             Was steht in der DB, was laeuft schief
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import db
from .config import Config
from .llm import LLM
from .sources import LOCK_FILENAME, load as load_sources

log = logging.getLogger("mi")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _require_api_key(config: Config) -> None:
    """Frueh und deutlich scheitern statt mitten im Lauf.

    Wird erst aufgerufen, wenn feststeht, dass es ueberhaupt Arbeit gibt —
    ein Leerlauf soll keine Zugangsdaten verlangen.
    """
    from .preflight import has_anthropic_access

    if not has_anthropic_access(config):
        raise SystemExit(
            "Kein Anthropic-Zugang gefunden. ANTHROPIC_API_KEY setzen "
            "oder `ant auth login` ausfuehren."
        )


# ------------------------------------------------------------------ Befehle

def cmd_verify_sources(args: argparse.Namespace, config: Config) -> int:
    from .net import Fetcher
    from .verify import save_results, verify_registry

    registry = load_sources(config.sources_path)
    only = set(args.only) if args.only else None
    fetcher = Fetcher(user_agent=config.user_agent, timeout=config.http_timeout)
    try:
        results = verify_registry(registry, fetcher, only=only)
    finally:
        fetcher.close()

    lock_path = config.sources_path.parent / LOCK_FILENAME
    if not args.dry_run:
        save_results(lock_path, results)

    width = max((len(r.source_id) for r in results), default=10)
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
        marker = {"verified": "OK  ", "no_feed": "HTML", "broken": "FEHL",
                  "manual": "MAN ", "skipped": "----"}.get(result.status, "?   ")
        print(f"{marker} {result.source_id:<{width}}  {result.url or ''}")
        if result.note:
            print(f"     {result.note}")

    print()
    print(" · ".join(f"{status}: {n}" for status, n in sorted(counts.items())))
    if not args.dry_run:
        print(f"Ergebnis in {lock_path.name} geschrieben.")
    if counts.get("no_feed"):
        print(
            "Fuer die HTML-Quellen jetzt die `html.item_selector` in "
            "sources.yaml pruefen — ein Selektor, der nichts trifft, "
            "faellt sonst erst im Digest auf."
        )
    return 0


def cmd_collect(args: argparse.Namespace, config: Config) -> int:
    from .collect import collect

    registry = load_sources(config.sources_path)
    with db.session(config.db_path) as conn:
        run_id = db.start_run(conn, "collect")
        try:
            stats = collect(
                conn, registry, config,
                cadence=args.cadence,
                allow_unverified=args.allow_unverified,
                only=set(args.only) if args.only else None,
            )
        except Exception as exc:
            db.finish_run(conn, run_id, ok=False, detail={"error": str(exc)})
            raise
        db.finish_run(conn, run_id, ok=True, detail=stats.as_dict())

    print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
    if stats.sources_skipped and not args.allow_unverified:
        print(
            f"\n{len(stats.sources_skipped)} Quellen uebersprungen, weil noch "
            "unverifiziert. Erst `mi verify-sources` laufen lassen.",
            file=sys.stderr,
        )
    return 0


def cmd_prefilter(args: argparse.Namespace, config: Config) -> int:
    from .prefilter import run as run_prefilter

    with db.session(config.db_path) as conn:
        pending = len(db.unscored_items(conn, limit=1))
    if not pending:
        print("Nichts zu bewerten.")
        return 0

    _require_api_key(config)
    llm = LLM(api_key=config.anthropic_api_key)
    with db.session(config.db_path) as conn:
        run_id = db.start_run(conn, "prefilter")
        try:
            stats = run_prefilter(conn, config, llm, limit=args.limit)
        except Exception as exc:
            db.finish_run(conn, run_id, ok=False, detail={"error": str(exc)},
                          **_usage(llm))
            raise
        db.finish_run(conn, run_id, ok=True, detail=stats.as_dict(), **_usage(llm))

    print(json.dumps({**stats.as_dict(), "usage": llm.usage.as_dict()},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_calibrate(args: argparse.Namespace, config: Config) -> int:
    from .calibrate import build_report

    with db.session(config.db_path) as conn:
        report = build_report(conn, days=args.days, samples_per_score=args.samples)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
          if args.json else report.render())
    return 0


def cmd_digest(args: argparse.Namespace, config: Config) -> int:
    from .digest import run as run_digest

    with db.session(config.db_path) as conn:
        candidates = len(db.digest_candidates(conn, config.digest_min_score, limit=1))
    if not candidates:
        print(f"Keine Meldungen ab Score {config.digest_min_score} — kein Marktbrief.")
        return 0

    _require_api_key(config)
    registry = load_sources(config.sources_path)
    llm = LLM(api_key=config.anthropic_api_key)
    with db.session(config.db_path) as conn:
        run_id = db.start_run(conn, "digest")
        try:
            result = run_digest(conn, config, llm, registry, dry_run=args.dry_run)
        except Exception as exc:
            db.finish_run(conn, run_id, ok=False, detail={"error": str(exc)},
                          **_usage(llm))
            raise
        db.finish_run(conn, run_id, ok=result.sent, detail=result.as_dict(),
                      **_usage(llm))

    print(json.dumps({**result.as_dict(), "usage": llm.usage.as_dict()},
                     ensure_ascii=False, indent=2), file=sys.stderr)
    return 0


def cmd_alerts(args: argparse.Namespace, config: Config) -> int:
    from .alerts import run as run_alerts

    with db.session(config.db_path) as conn:
        run_id = db.start_run(conn, "alerts")
        try:
            stats = run_alerts(conn, config, dry_run=args.dry_run)
        except Exception as exc:
            db.finish_run(conn, run_id, ok=False, detail={"error": str(exc)})
            raise
        db.finish_run(conn, run_id, ok=True, detail=stats.as_dict())

    print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    return 0


def cmd_ask(args: argparse.Namespace, config: Config) -> int:
    from .ask import ask

    question = " ".join(args.question).strip()
    if not question:
        raise SystemExit("Keine Frage angegeben.")

    _require_api_key(config)
    llm = LLM(api_key=config.anthropic_api_key)

    with db.session(config.db_path) as conn:
        answer = ask(conn, config, llm, question, limit=args.limit)
    print(answer.render())
    return 0


def cmd_monthly(args: argparse.Namespace, config: Config) -> int:
    from .monthly import previous_month, run as run_monthly

    month = args.month or previous_month()
    with db.session(config.db_path) as conn:
        in_month = conn.execute(
            "SELECT count(*) AS n FROM items WHERE "
            "strftime('%Y-%m', coalesce(published_at, fetched_at)) = ?",
            (month,),
        ).fetchone()["n"]
    if not in_month:
        print(f"Keine Meldungen aus {month} — nichts zu verdichten.")
        return 0

    _require_api_key(config)
    llm = LLM(api_key=config.anthropic_api_key)
    with db.session(config.db_path) as conn:
        run_id = db.start_run(conn, "monthly")
        try:
            stats = run_monthly(conn, config, llm, month=month)
        except Exception as exc:
            db.finish_run(conn, run_id, ok=False, detail={"error": str(exc)},
                          **_usage(llm))
            raise
        db.finish_run(conn, run_id, ok=True, detail=stats.as_dict(), **_usage(llm))

    print(json.dumps({**stats.as_dict(), "usage": llm.usage.as_dict()},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_competitors(args: argparse.Namespace, config: Config) -> int:
    from .collect.website_diff import run as run_diff
    from .mailer import Mail, send
    from .render import competitor_html, competitor_subject, competitor_text

    registry = load_sources(config.sources_path)
    with db.session(config.db_path) as conn:
        run_id = db.start_run(conn, "competitors")
        try:
            stats = run_diff(conn, registry.competitors, config)
        except Exception as exc:
            db.finish_run(conn, run_id, ok=False, detail={"error": str(exc)})
            raise
        db.finish_run(conn, run_id, ok=True, detail=stats.as_dict())

    changes = [c.as_dict() for c in stats.changes]
    if changes and not args.dry_run:
        send(
            Mail(
                subject=competitor_subject(len(changes)),
                text=competitor_text(changes),
                html=competitor_html(changes),
            ),
            config.mail,
        )
    elif args.dry_run:
        print(competitor_text(changes))

    print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    return 0


def cmd_preflight(args: argparse.Namespace, config: Config) -> int:
    """Exit 0 = startklar, Exit 1 = noch nicht. Die Workflows lesen den Code."""
    from .preflight import CHECKS, render, run as run_preflight

    required = tuple(args.require.split(",")) if args.require else CHECKS
    checks = run_preflight(config, required)
    print(render(checks))
    return 0 if all(c.ok for c in checks) else 1


def cmd_status(args: argparse.Namespace, config: Config) -> int:
    registry = load_sources(config.sources_path)
    with db.session(config.db_path) as conn:
        totals = conn.execute(
            """
            SELECT count(*) AS items,
                   sum(CASE WHEN score IS NULL THEN 1 ELSE 0 END) AS unscored,
                   sum(CASE WHEN alerted = 1 THEN 1 ELSE 0 END) AS alerted,
                   sum(CASE WHEN digested_at IS NOT NULL THEN 1 ELSE 0 END) AS digested
            FROM items
            """
        ).fetchone()
        oldest = conn.execute(
            "SELECT min(fetched_at) AS d FROM items"
        ).fetchone()["d"]
        briefs = conn.execute("SELECT count(*) AS n FROM monthly_briefs").fetchone()["n"]
        cost = conn.execute(
            "SELECT coalesce(sum(cost_usd), 0) AS c FROM runs "
            "WHERE started_at >= datetime('now', '-30 days')"
        ).fetchone()["c"]
        broken = conn.execute(
            "SELECT source_id, error_streak, last_error FROM source_state "
            "WHERE error_streak >= 2 ORDER BY error_streak DESC"
        ).fetchall()
        recent_runs = conn.execute(
            "SELECT kind, started_at, ok FROM runs ORDER BY id DESC LIMIT 8"
        ).fetchall()

    by_status: dict[str, int] = {}
    for source in registry.sources:
        by_status[source.status] = by_status.get(source.status, 0) + 1

    print("Quellen")
    print("-" * 60)
    for status, count in sorted(by_status.items()):
        print(f"  {status:<12} {count}")
    unverified = by_status.get("unverified", 0)
    if unverified:
        print(f"\n  ⚠ {unverified} Quellen unverifiziert — Bauschritt 1 offen.")
        print("    `mi verify-sources` ausfuehren.")

    print()
    print("Archiv")
    print("-" * 60)
    print(f"  Items gesamt      {totals['items'] or 0}")
    print(f"  davon unbewertet  {totals['unscored'] or 0}")
    print(f"  im Digest         {totals['digested'] or 0}")
    print(f"  als Alert         {totals['alerted'] or 0}")
    print(f"  Monatsbriefe      {briefs}")
    print(f"  aeltestes Item    {oldest or '—'}")
    print(f"  Kosten 30 Tage    ${cost:.2f}")

    if broken:
        print()
        print("Quellen mit Fehlern")
        print("-" * 60)
        for row in broken:
            print(f"  {row['source_id']:<28} {row['error_streak']}x  "
                  f"{(row['last_error'] or '')[:60]}")

    if recent_runs:
        print()
        print("Letzte Laeufe")
        print("-" * 60)
        for row in recent_runs:
            mark = "ok " if row["ok"] else ("—  " if row["ok"] is None else "FEHL")
            print(f"  {mark} {row['kind']:<12} {row['started_at']}")
    return 0


def _usage(llm: LLM) -> dict[str, float | int]:
    return {
        "input_tokens": llm.usage.input_tokens,
        "output_tokens": llm.usage.output_tokens,
        "cost_usd": llm.usage.cost_usd,
    }


# -------------------------------------------------------------------- Parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mi",
        description="Market Intelligence — Marktbrief zum deutschen Gesundheitsmarkt",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("verify-sources", help="Bauschritt 1: Feed-URLs pruefen")
    p.add_argument("--only", nargs="*", help="nur diese source-ids")
    p.add_argument("--dry-run", action="store_true", help="Lockdatei nicht schreiben")
    p.set_defaults(func=cmd_verify_sources)

    p = sub.add_parser("collect", help="Bauschritt 2: sammeln und speichern")
    p.add_argument("--cadence", choices=["daily", "alerts", "weekly"], default="daily")
    p.add_argument("--only", nargs="*", help="nur diese source-ids")
    p.add_argument(
        "--allow-unverified",
        action="store_true",
        help="auch ungeprüfte Quellen abrufen (Notnagel, nicht der Normalfall)",
    )
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("prefilter", help="Bauschritt 3: Items bewerten")
    p.add_argument("--limit", type=int, default=500)
    p.set_defaults(func=cmd_prefilter)

    p = sub.add_parser("calibrate", help="Bauschritt 3: Schwellen einstellen")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--samples", type=int, default=3)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("digest", help="Bauschritt 4: Marktbrief verschicken")
    p.add_argument("--dry-run", action="store_true", help="nur ausgeben, nicht senden")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("alerts", help="Bauschritt 5: Trigger pruefen")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_alerts)

    p = sub.add_parser("ask", help="Bauschritt 6: Archiv befragen")
    p.add_argument("question", nargs="+")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("monthly", help="Bauschritt 6: Monat verdichten")
    p.add_argument("--month", help="YYYY-MM (Default: Vormonat)")
    p.set_defaults(func=cmd_monthly)

    p = sub.add_parser("competitors", help="Bauschritt 7: Wettbewerberseiten diffen")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_competitors)

    p = sub.add_parser(
        "preflight",
        help="ist das System startklar? (Exit 0 ja, 1 nein)",
    )
    p.add_argument(
        "--require",
        help="Kommaliste aus sources,anthropic,mail (Default: alle drei)",
    )
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("status", help="Systemzustand")
    p.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    config = Config.from_env()
    try:
        return int(args.func(args, config))
    except KeyboardInterrupt:
        return 130
    except SystemExit:
        raise
    except Exception as exc:
        log.error("%s: %s", type(exc).__name__, exc)
        if args.verbose:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
