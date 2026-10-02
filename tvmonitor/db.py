"""SQLite storage for settings, scan locations, shows and missing episodes."""
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DEFAULT_SETTINGS = {
    "tvdb_api_key": "",
    "tvdb_pin": "",
    "language": "eng",
    "season_type": "default",
    "include_specials": "0",
    "min_season": "1",
    "grace_days": "1",
    "video_extensions": "mkv,mp4,avi,m4v,ts,m2ts,wmv,mov,mpg,mpeg,webm,flv",
    "schedule_mode": "daily",
    "schedule_time": "03:00",
    "schedule_interval_hours": "24",
    "cache_hours": "12",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS scan_paths (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    path     TEXT UNIQUE NOT NULL,
    enabled  INTEGER NOT NULL DEFAULT 1,
    added_at TEXT
);
CREATE TABLE IF NOT EXISTS shows (
    tvdb_id          INTEGER PRIMARY KEY,
    folder_name      TEXT,
    name             TEXT,
    slug             TEXT,
    status           TEXT,
    folders          TEXT,
    episodes_on_disk INTEGER NOT NULL DEFAULT 0,
    missing_count    INTEGER NOT NULL DEFAULT 0,
    min_season       INTEGER,
    ignored          INTEGER NOT NULL DEFAULT 0,
    present          INTEGER NOT NULL DEFAULT 1,
    last_scanned     TEXT,
    last_error       TEXT
);
CREATE TABLE IF NOT EXISTS missing_episodes (
    episode_id     INTEGER PRIMARY KEY,
    tvdb_id        INTEGER NOT NULL,
    season         INTEGER,
    episode        INTEGER,
    name           TEXT,
    aired          TEXT,
    overview       TEXT,
    first_detected TEXT
);
CREATE INDEX IF NOT EXISTS ix_missing_show ON missing_episodes(tvdb_id);
CREATE INDEX IF NOT EXISTS ix_missing_aired ON missing_episodes(aired);
-- Kept separate from missing_episodes so a tick survives a show temporarily
-- dropping out of the list (e.g. a drive in the pool being offline).
CREATE TABLE IF NOT EXISTS retrieval_flags (
    episode_id INTEGER PRIMARY KEY,
    flagged_at TEXT
);
CREATE TABLE IF NOT EXISTS tvdb_cache (
    series_id     INTEGER NOT NULL,
    season_type   TEXT NOT NULL,
    language      TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    series_json   TEXT NOT NULL,
    episodes_json TEXT NOT NULL,
    PRIMARY KEY (series_id, season_type, language)
);
CREATE TABLE IF NOT EXISTS scan_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT,
    finished_at   TEXT,
    trigger       TEXT,
    status        TEXT,
    shows_scanned INTEGER DEFAULT 0,
    missing_found INTEGER DEFAULT 0,
    errors        INTEGER DEFAULT 0,
    message       TEXT
);
"""


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self.tx() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def tx(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def query(self, sql, params=()):
        with self.tx() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # -- settings -----------------------------------------------------------

    def get_settings(self):
        values = dict(DEFAULT_SETTINGS)
        for row in self.query("SELECT key, value FROM settings"):
            values[row["key"]] = row["value"]
        return values

    def set_settings(self, values):
        with self.tx() as conn:
            conn.executemany(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(k, str(v)) for k, v in values.items()],
            )

    def secret_key(self):
        key = self.get_settings().get("secret_key")
        if not key:
            key = secrets.token_hex(32)
            self.set_settings({"secret_key": key})
        return key

    # -- scan locations -----------------------------------------------------

    def scan_paths(self, enabled_only=False):
        sql = "SELECT * FROM scan_paths"
        if enabled_only:
            sql += " WHERE enabled = 1"
        return self.query(sql + " ORDER BY path COLLATE NOCASE")

    # -- scan runs ----------------------------------------------------------

    def last_run(self):
        return self.query_one("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1")

    def recent_runs(self, limit=15):
        return self.query("SELECT * FROM scan_runs ORDER BY id DESC LIMIT ?", (limit,))
