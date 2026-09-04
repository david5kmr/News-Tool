-- Archiv-Schema. Absichtlich vollstaendig: auch Score-2-Rauschen bleibt
-- gespeichert, weil die Rueckschau Vollstaendigkeit braucht.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY,
    url           TEXT    NOT NULL,
    url_hash      TEXT    NOT NULL UNIQUE,
    title         TEXT    NOT NULL,
    title_norm    TEXT    NOT NULL DEFAULT '',
    source        TEXT    NOT NULL,
    published_at  TEXT,                     -- ISO 8601 UTC, kann fehlen
    fetched_at    TEXT    NOT NULL,         -- ISO 8601 UTC
    raw_text      TEXT,
    summary       TEXT,
    score         INTEGER,                  -- NULL = noch nicht geprefiltert
    reason        TEXT,                     -- Begruendung des Prefilters
    topics        TEXT,                     -- JSON-Array
    entities      TEXT,                     -- JSON-Array
    alerted       INTEGER NOT NULL DEFAULT 0,
    digested_at   TEXT,                     -- wann im Digest verschickt
    scored_at     TEXT,
    dedupe_of     INTEGER REFERENCES items(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_items_fetched   ON items(fetched_at);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_score     ON items(score);
CREATE INDEX IF NOT EXISTS idx_items_source    ON items(source);
CREATE INDEX IF NOT EXISTS idx_items_unscored  ON items(score) WHERE score IS NULL;
CREATE INDEX IF NOT EXISTS idx_items_undigested ON items(digested_at) WHERE digested_at IS NULL;

-- Eigennamen-Register. Fuettert die Watchlist-Statistik und `mi entities`.
CREATE TABLE IF NOT EXISTS entities (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    name_norm     TEXT NOT NULL UNIQUE,
    type          TEXT NOT NULL DEFAULT 'unknown',  -- company|clinic|org|person|law|unknown
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    mention_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS item_entities (
    item_id   INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, entity_id)
);

CREATE TABLE IF NOT EXISTS monthly_briefs (
    id         INTEGER PRIMARY KEY,
    month      TEXT NOT NULL,          -- 'YYYY-MM'
    topic      TEXT NOT NULL,          -- goae | klinikmarkt | wettbewerb | politik
    text       TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (month, topic)
);

-- Verschickte Alerts: Grundlage fuer das Tageslimit von 3.
CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY,
    item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    trigger    TEXT NOT NULL,          -- welcher Trigger ausgeloest hat
    sent_at    TEXT NOT NULL,
    UNIQUE (item_id)
);

CREATE INDEX IF NOT EXISTS idx_alerts_sent ON alerts(sent_at);

-- Zustand pro Quelle: ETag/Last-Modified fuer bedingte Requests,
-- Fehlerzaehler fuer stille Ausfaelle.
CREATE TABLE IF NOT EXISTS source_state (
    source_id      TEXT PRIMARY KEY,
    etag           TEXT,
    last_modified  TEXT,
    last_fetch_at  TEXT,
    last_success_at TEXT,
    last_error     TEXT,
    error_streak   INTEGER NOT NULL DEFAULT 0,
    items_seen     INTEGER NOT NULL DEFAULT 0
);

-- Change-Detection der Wettbewerber-Seiten (Bauschritt 7).
CREATE TABLE IF NOT EXISTS page_snapshots (
    id            INTEGER PRIMARY KEY,
    competitor_id TEXT NOT NULL,
    url           TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    text          TEXT NOT NULL,
    fetched_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_url ON page_snapshots(url, fetched_at DESC);

-- Lauf-Protokoll: was hat wann wie viel gekostet.
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,        -- collect | prefilter | digest | alerts | monthly
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    ok          INTEGER,
    detail      TEXT,                 -- JSON
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_runs_kind ON runs(kind, started_at DESC);

-- Volltextsuche fuer `mi ask`. External-content-Tabelle: der Index haelt
-- keine Kopie, die Trigger halten ihn synchron.
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    title, summary, raw_text,
    content='items',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS items_fts_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, title, summary, raw_text)
    VALUES (new.id, new.title, coalesce(new.summary, ''), coalesce(new.raw_text, ''));
END;

CREATE TRIGGER IF NOT EXISTS items_fts_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, summary, raw_text)
    VALUES ('delete', old.id, old.title, coalesce(old.summary, ''), coalesce(old.raw_text, ''));
END;

CREATE TRIGGER IF NOT EXISTS items_fts_au AFTER UPDATE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, summary, raw_text)
    VALUES ('delete', old.id, old.title, coalesce(old.summary, ''), coalesce(old.raw_text, ''));
    INSERT INTO items_fts(rowid, title, summary, raw_text)
    VALUES (new.id, new.title, coalesce(new.summary, ''), coalesce(new.raw_text, ''));
END;
