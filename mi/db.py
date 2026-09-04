"""SQLite-Zugriff. Eine Datei, kein Server — bewusst so gewaehlt."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def utcnow() -> str:
    """ISO-8601-Zeitstempel in UTC, sekundengenau."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(db_path: Path) -> sqlite3.Connection:
    """Legt die DB an bzw. bringt ein bestehendes Schema auf Stand."""
    conn = connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


@contextmanager
def session(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = init(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------- items

def insert_item(conn: sqlite3.Connection, item: dict[str, Any]) -> int | None:
    """Speichert ein Item. Gibt None zurueck, wenn der url_hash schon da ist."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO items
            (url, url_hash, title, title_norm, source, published_at, fetched_at,
             raw_text, summary, score, reason, topics, entities, dedupe_of)
        VALUES (:url, :url_hash, :title, :title_norm, :source, :published_at,
                :fetched_at, :raw_text, :summary, :score, :reason, :topics,
                :entities, :dedupe_of)
        """,
        {
            "url": item["url"],
            "url_hash": item["url_hash"],
            "title": item["title"],
            "title_norm": item.get("title_norm", ""),
            "source": item["source"],
            "published_at": item.get("published_at"),
            "fetched_at": item.get("fetched_at") or utcnow(),
            "raw_text": item.get("raw_text"),
            "summary": item.get("summary"),
            "score": item.get("score"),
            "reason": item.get("reason"),
            "topics": _as_json(item.get("topics")),
            "entities": _as_json(item.get("entities")),
            "dedupe_of": item.get("dedupe_of"),
        },
    )
    if cur.rowcount == 0:
        return None
    return int(cur.lastrowid)


def _as_json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def url_hash_exists(conn: sqlite3.Connection, url_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM items WHERE url_hash = ? LIMIT 1", (url_hash,)
    ).fetchone()
    return row is not None


def recent_titles(
    conn: sqlite3.Connection, days: int = 14, limit: int = 4000
) -> list[tuple[int, str]]:
    """(id, title_norm) der letzten Tage — Grundlage der Titelaehnlichkeit."""
    rows = conn.execute(
        """
        SELECT id, title_norm FROM items
        WHERE fetched_at >= datetime('now', ?)
        ORDER BY id DESC LIMIT ?
        """,
        (f"-{int(days)} days", int(limit)),
    ).fetchall()
    return [(int(r["id"]), r["title_norm"] or "") for r in rows]


def unscored_items(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, url, title, source, published_at, raw_text, summary
        FROM items
        WHERE score IS NULL AND dedupe_of IS NULL
        ORDER BY id ASC LIMIT ?
        """,
        (int(limit),),
    ).fetchall()


def set_score(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    score: int,
    summary: str | None,
    reason: str | None,
    topics: Sequence[str] | None,
    entities: Sequence[str] | None,
) -> None:
    conn.execute(
        """
        UPDATE items
        SET score = ?, summary = ?, reason = ?, topics = ?, entities = ?,
            scored_at = ?
        WHERE id = ?
        """,
        (
            int(score),
            summary,
            reason,
            _as_json(list(topics or [])),
            _as_json(list(entities or [])),
            utcnow(),
            int(item_id),
        ),
    )


def digest_candidates(
    conn: sqlite3.Connection, min_score: int, limit: int = 60
) -> list[sqlite3.Row]:
    """Bewertete, noch nicht verschickte Items ab Mindest-Score."""
    return conn.execute(
        """
        SELECT id, url, title, source, published_at, summary, reason, score,
               topics, entities
        FROM items
        WHERE score >= ? AND digested_at IS NULL AND dedupe_of IS NULL
        ORDER BY score DESC, coalesce(published_at, fetched_at) DESC
        LIMIT ?
        """,
        (int(min_score), int(limit)),
    ).fetchall()


def mark_digested(conn: sqlite3.Connection, item_ids: Iterable[int]) -> None:
    now = utcnow()
    conn.executemany(
        "UPDATE items SET digested_at = ? WHERE id = ?",
        [(now, int(i)) for i in item_ids],
    )


def alert_candidates(
    conn: sqlite3.Connection, min_score: int, limit: int = 50
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT i.id, i.url, i.title, i.source, i.published_at, i.summary,
               i.reason, i.score, i.topics, i.entities, i.raw_text
        FROM items i
        LEFT JOIN alerts a ON a.item_id = i.id
        WHERE i.score >= ? AND i.alerted = 0 AND a.item_id IS NULL
              AND i.dedupe_of IS NULL
        ORDER BY i.score DESC, i.id DESC
        LIMIT ?
        """,
        (int(min_score), int(limit)),
    ).fetchall()


def alerts_sent_today(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT count(*) AS n FROM alerts WHERE date(sent_at) = date('now')"
    ).fetchone()
    return int(row["n"])


def record_alert(conn: sqlite3.Connection, item_id: int, trigger: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO alerts (item_id, trigger, sent_at) VALUES (?, ?, ?)",
        (int(item_id), trigger, utcnow()),
    )
    conn.execute("UPDATE items SET alerted = 1 WHERE id = ?", (int(item_id),))


# ------------------------------------------------------------------ entities

def touch_entity(conn: sqlite3.Connection, item_id: int, name: str, type_: str = "unknown") -> None:
    name = name.strip()
    if not name:
        return
    norm = name.casefold()
    now = utcnow()
    conn.execute(
        """
        INSERT INTO entities (name, name_norm, type, first_seen, last_seen, mention_count)
        VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(name_norm) DO UPDATE SET
            last_seen = excluded.last_seen,
            mention_count = entities.mention_count + 1,
            type = CASE WHEN entities.type = 'unknown' THEN excluded.type ELSE entities.type END
        """,
        (name, norm, type_, now, now),
    )
    row = conn.execute(
        "SELECT id FROM entities WHERE name_norm = ?", (norm,)
    ).fetchone()
    if row:
        conn.execute(
            "INSERT OR IGNORE INTO item_entities (item_id, entity_id) VALUES (?, ?)",
            (int(item_id), int(row["id"])),
        )


# --------------------------------------------------------------- source state

def get_source_state(conn: sqlite3.Connection, source_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM source_state WHERE source_id = ?", (source_id,)
    ).fetchone()


def record_source_success(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    etag: str | None,
    last_modified: str | None,
    items_seen: int,
) -> None:
    now = utcnow()
    conn.execute(
        """
        INSERT INTO source_state
            (source_id, etag, last_modified, last_fetch_at, last_success_at,
             last_error, error_streak, items_seen)
        VALUES (?, ?, ?, ?, ?, NULL, 0, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            etag = excluded.etag,
            last_modified = excluded.last_modified,
            last_fetch_at = excluded.last_fetch_at,
            last_success_at = excluded.last_success_at,
            last_error = NULL,
            error_streak = 0,
            items_seen = source_state.items_seen + excluded.items_seen
        """,
        (source_id, etag, last_modified, now, now, int(items_seen)),
    )


def record_source_error(conn: sqlite3.Connection, source_id: str, error: str) -> int:
    now = utcnow()
    conn.execute(
        """
        INSERT INTO source_state (source_id, last_fetch_at, last_error, error_streak)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(source_id) DO UPDATE SET
            last_fetch_at = excluded.last_fetch_at,
            last_error = excluded.last_error,
            error_streak = source_state.error_streak + 1
        """,
        (source_id, now, error[:500]),
    )
    row = conn.execute(
        "SELECT error_streak FROM source_state WHERE source_id = ?", (source_id,)
    ).fetchone()
    return int(row["error_streak"]) if row else 1


# ------------------------------------------------------------------ run log

def start_run(conn: sqlite3.Connection, kind: str) -> int:
    cur = conn.execute(
        "INSERT INTO runs (kind, started_at) VALUES (?, ?)", (kind, utcnow())
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    ok: bool,
    detail: dict[str, Any] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
) -> None:
    conn.execute(
        """
        UPDATE runs
        SET finished_at = ?, ok = ?, detail = ?, input_tokens = ?,
            output_tokens = ?, cost_usd = ?
        WHERE id = ?
        """,
        (
            utcnow(),
            1 if ok else 0,
            json.dumps(detail or {}, ensure_ascii=False, default=str),
            int(input_tokens),
            int(output_tokens),
            float(cost_usd),
            int(run_id),
        ),
    )
    conn.commit()


# ------------------------------------------------------------ monthly briefs

def upsert_monthly_brief(
    conn: sqlite3.Connection, month: str, topic: str, text: str, item_count: int
) -> None:
    conn.execute(
        """
        INSERT INTO monthly_briefs (month, topic, text, item_count, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(month, topic) DO UPDATE SET
            text = excluded.text,
            item_count = excluded.item_count,
            created_at = excluded.created_at
        """,
        (month, topic, text, int(item_count), utcnow()),
    )


def recent_briefs(conn: sqlite3.Connection, limit: int = 12) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT month, topic, text FROM monthly_briefs ORDER BY month DESC, topic LIMIT ?",
        (int(limit),),
    ).fetchall()
