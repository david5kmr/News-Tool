"""Bauschritt 3: Der Filter wird an echten Daten eingestellt, nicht am Reissbrett.

`mi calibrate` zeigt die Score-Verteilung, die Quellen dahinter und je Score
ein paar Beispiele mit der Begruendung des Modells. Aus diesem Bericht ergibt
sich, wo die Digest-Schwelle wirklich liegen muss.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass
class CalibrationReport:
    total: int
    scored: int
    unscored: int
    distribution: dict[int, int]
    by_source: list[dict[str, Any]]
    samples: dict[int, list[dict[str, Any]]]
    thresholds: dict[int, int]
    days: int

    def render(self) -> str:
        lines: list[str] = []
        lines.append(f"Kalibrierbericht — letzte {self.days} Tage")
        lines.append("=" * 60)
        lines.append(
            f"{self.total} Items gesammelt, {self.scored} bewertet, "
            f"{self.unscored} offen"
        )
        if not self.scored:
            lines.append("")
            lines.append(
                "Noch nichts bewertet. Erst `mi collect`, dann `mi prefilter` — "
                "und laut Spec drei Tage Rohdaten abwarten, bevor die Schwellen "
                "angefasst werden."
            )
            return "\n".join(lines)

        lines.append("")
        lines.append("Score-Verteilung")
        lines.append("-" * 60)
        peak = max(self.distribution.values(), default=1)
        for score in range(10, -1, -1):
            count = self.distribution.get(score, 0)
            bar = "█" * int(round(40 * count / peak)) if count else ""
            share = 100 * count / self.scored
            lines.append(f"  {score:>2}  {count:>5}  {share:>5.1f}%  {bar}")

        lines.append("")
        lines.append("Was bei welcher Schwelle taeglich im Digest laendet")
        lines.append("-" * 60)
        for threshold in sorted(self.thresholds):
            per_day = self.thresholds[threshold] / max(1, self.days)
            marker = "  <- Spec-Vorgabe" if threshold == 4 else ""
            lines.append(
                f"  ab Score {threshold}: {self.thresholds[threshold]:>5} Items "
                f"= {per_day:>5.1f}/Tag{marker}"
            )
        lines.append("")
        lines.append(
            "  Faustregel: 5-8 Items/Tag sollen den Digest fuellen. Liegt der "
            "Wert deutlich darueber, ist die Schwelle zu niedrig."
        )

        lines.append("")
        lines.append("Nach Quelle")
        lines.append("-" * 60)
        lines.append(f"  {'Quelle':<32} {'n':>5} {'Ø':>5} {'>=7':>5}")
        for row in self.by_source:
            lines.append(
                f"  {row['source'][:32]:<32} {row['n']:>5} "
                f"{row['avg']:>5.1f} {row['high']:>5}"
            )
        lines.append("")
        lines.append(
            "  Eine Quelle mit vielen Items und Ø unter 2 kostet nur Token — "
            "Cadence senken oder rauswerfen."
        )

        lines.append("")
        lines.append("Stichproben je Score (stimmt die Vergabe?)")
        lines.append("-" * 60)
        for score in sorted(self.samples, reverse=True):
            lines.append(f"  Score {score}:")
            for sample in self.samples[score]:
                lines.append(f"    · [{sample['source']}] {sample['title'][:88]}")
                lines.append(f"      Begruendung: {sample['reason'] or '—'}")
            lines.append("")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "total": self.total,
            "scored": self.scored,
            "unscored": self.unscored,
            "distribution": {str(k): v for k, v in sorted(self.distribution.items())},
            "thresholds": {str(k): v for k, v in sorted(self.thresholds.items())},
            "by_source": self.by_source,
        }


def build_report(
    conn: sqlite3.Connection, *, days: int = 7, samples_per_score: int = 3
) -> CalibrationReport:
    window = f"-{int(days)} days"

    total = int(
        conn.execute(
            "SELECT count(*) AS n FROM items WHERE fetched_at >= datetime('now', ?)",
            (window,),
        ).fetchone()["n"]
    )
    rows = conn.execute(
        """
        SELECT score, count(*) AS n FROM items
        WHERE score IS NOT NULL AND fetched_at >= datetime('now', ?)
        GROUP BY score
        """,
        (window,),
    ).fetchall()
    distribution = {int(r["score"]): int(r["n"]) for r in rows}
    scored = sum(distribution.values())

    by_source = [
        {
            "source": r["source"],
            "n": int(r["n"]),
            "avg": float(r["avg"] or 0.0),
            "high": int(r["high"]),
        }
        for r in conn.execute(
            """
            SELECT source, count(*) AS n, avg(score) AS avg,
                   sum(CASE WHEN score >= 7 THEN 1 ELSE 0 END) AS high
            FROM items
            WHERE score IS NOT NULL AND fetched_at >= datetime('now', ?)
            GROUP BY source
            ORDER BY avg DESC, n DESC
            """,
            (window,),
        ).fetchall()
    ]

    samples: dict[int, list[dict[str, Any]]] = {}
    for score in sorted(distribution, reverse=True):
        samples[score] = [
            {"title": r["title"], "source": r["source"], "reason": r["reason"]}
            for r in conn.execute(
                """
                SELECT title, source, reason FROM items
                WHERE score = ? AND fetched_at >= datetime('now', ?)
                ORDER BY random() LIMIT ?
                """,
                (score, window, int(samples_per_score)),
            ).fetchall()
        ]

    thresholds = {
        t: sum(n for s, n in distribution.items() if s >= t) for t in range(2, 9)
    }

    return CalibrationReport(
        total=total,
        scored=scored,
        unscored=total - scored,
        distribution=distribution,
        by_source=by_source,
        samples=samples,
        thresholds=thresholds,
        days=days,
    )
