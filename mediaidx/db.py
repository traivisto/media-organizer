"""Schema and data access.

The identity model (spec §4.1): a photograph is an *asset*, keyed by content
hash; each path holding it is a *file*. Analysis attaches to the asset, so a
duplicate is never processed twice.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS folders (
  id            INTEGER PRIMARY KEY,
  path          TEXT NOT NULL UNIQUE,
  parent_id     INTEGER REFERENCES folders(id),
  parsed_year   INTEGER,
  parsed_month  INTEGER,
  parsed_label  TEXT,
  user_label    TEXT,
  person_hint   TEXT,
  is_root       INTEGER NOT NULL DEFAULT 0,
  root_added_at INTEGER
);

CREATE TABLE IF NOT EXISTS assets (
  id                 INTEGER PRIMARY KEY,
  content_hash       TEXT NOT NULL UNIQUE,
  format             TEXT,
  width              INTEGER,
  height             INTEGER,
  orientation        INTEGER,
  taken_at           INTEGER,
  taken_at_source    TEXT,
  taken_at_precision TEXT,
  date_conflict      INTEGER NOT NULL DEFAULT 0,
  conflict_note      TEXT,
  source_hint        TEXT,
  gps_lat            REAL,
  gps_lon            REAL,
  camera_make        TEXT,
  camera_model       TEXT,
  lens               TEXT,
  iso                INTEGER,
  f_number           REAL,
  exposure_time      REAL,
  focal_length       REAL
);

CREATE TABLE IF NOT EXISTS files (
  id            INTEGER PRIMARY KEY,
  path          TEXT NOT NULL UNIQUE,
  folder_id     INTEGER REFERENCES folders(id),
  asset_id      INTEGER REFERENCES assets(id),
  size_bytes    INTEGER NOT NULL,
  mtime         INTEGER NOT NULL,
  first_seen    INTEGER NOT NULL,
  last_seen     INTEGER NOT NULL,
  missing_since INTEGER
);

CREATE TABLE IF NOT EXISTS stage_state (
  asset_id   INTEGER NOT NULL REFERENCES assets(id),
  stage      TEXT NOT NULL,
  version    INTEGER NOT NULL,
  status     TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  error      TEXT,
  PRIMARY KEY (asset_id, stage)
);

CREATE TABLE IF NOT EXISTS scan_errors (
  id         INTEGER PRIMARY KEY,
  path       TEXT NOT NULL,
  stage      TEXT NOT NULL,
  error      TEXT NOT NULL,
  attempts   INTEGER NOT NULL DEFAULT 1,
  updated_at INTEGER NOT NULL,
  UNIQUE (path, stage)
);

CREATE INDEX IF NOT EXISTS idx_files_asset   ON files(asset_id);
CREATE INDEX IF NOT EXISTS idx_files_folder  ON files(folder_id);
CREATE INDEX IF NOT EXISTS idx_files_missing ON files(missing_since);
CREATE INDEX IF NOT EXISTS idx_assets_taken  ON assets(taken_at);
CREATE INDEX IF NOT EXISTS idx_assets_conf   ON assets(date_conflict);
CREATE INDEX IF NOT EXISTS idx_stage         ON stage_state(stage, status, version);
"""


def connect(db_path: str | Path, *, create: bool = True) -> sqlite3.Connection:
    db_path = Path(db_path)
    if create:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    elif not db_path.exists():
        raise FileNotFoundError(f"no database at {db_path} -- run `mediaidx add` first")

    con = sqlite3.connect(db_path, timeout=30.0, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def migrate(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    row = con.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        con.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif row["version"] < SCHEMA_VERSION:
        con.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))


# --------------------------------------------------------------------------
# Folders
# --------------------------------------------------------------------------

def upsert_folder(con, path: str, parsed, *, is_root: bool = False,
                  user_label: str | None = None, person_hint: str | None = None,
                  now: int = 0) -> int:
    row = con.execute("SELECT id FROM folders WHERE path = ?", (path,)).fetchone()
    if row:
        fid = row["id"]
        sets, args = ["parsed_year=?", "parsed_month=?", "parsed_label=?"], [
            parsed.year, parsed.month, parsed.label]
        if is_root:
            sets += ["is_root=1", "root_added_at=COALESCE(root_added_at, ?)"]
            args.append(now)
        if user_label is not None:
            sets.append("user_label=?"); args.append(user_label)
        if person_hint is not None:
            sets.append("person_hint=?"); args.append(person_hint)
        args.append(fid)
        con.execute(f"UPDATE folders SET {','.join(sets)} WHERE id = ?", args)
        return fid

    cur = con.execute(
        """INSERT INTO folders
           (path, parsed_year, parsed_month, parsed_label, user_label,
            person_hint, is_root, root_added_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (path, parsed.year, parsed.month, parsed.label, user_label,
         person_hint, 1 if is_root else 0, now if is_root else None),
    )
    return cur.lastrowid


# --------------------------------------------------------------------------
# Assets and files
# --------------------------------------------------------------------------

def get_asset_id(con, content_hash: str) -> int | None:
    row = con.execute(
        "SELECT id FROM assets WHERE content_hash = ?", (content_hash,)).fetchone()
    return row["id"] if row else None


def insert_asset(con, content_hash: str, fields: dict) -> int:
    cols = ["content_hash"] + list(fields)
    vals = [content_hash] + [fields[k] for k in fields]
    placeholders = ",".join("?" * len(cols))
    cur = con.execute(
        f"INSERT INTO assets ({','.join(cols)}) VALUES ({placeholders})", vals)
    return cur.lastrowid


def upsert_file(con, path: str, folder_id: int, asset_id: int,
                size: int, mtime: int, now: int) -> None:
    con.execute(
        """INSERT INTO files
             (path, folder_id, asset_id, size_bytes, mtime, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(path) DO UPDATE SET
             folder_id=excluded.folder_id,
             asset_id=excluded.asset_id,
             size_bytes=excluded.size_bytes,
             mtime=excluded.mtime,
             last_seen=excluded.last_seen,
             missing_since=NULL""",
        (path, folder_id, asset_id, size, mtime, now, now),
    )


def known_files(con, folder_prefix: str) -> dict[str, tuple[int, int]]:
    """path -> (size, mtime) for everything already indexed beneath a prefix.

    This is what makes a re-scan cheap: an unchanged file is skipped without
    ever being opened.
    """
    rows = con.execute(
        "SELECT path, size_bytes, mtime FROM files WHERE path LIKE ? ESCAPE '\\'",
        (folder_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",),
    ).fetchall()
    return {r["path"]: (r["size_bytes"], r["mtime"]) for r in rows}


def set_stage(con, asset_id: int, stage: str, version: int,
              status: str, now: int, error: str | None = None) -> None:
    con.execute(
        """INSERT INTO stage_state (asset_id, stage, version, status, updated_at, error)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(asset_id, stage) DO UPDATE SET
             version=excluded.version, status=excluded.status,
             updated_at=excluded.updated_at, error=excluded.error""",
        (asset_id, stage, version, status, now, error),
    )


def record_error(con, path: str, stage: str, error: str, now: int) -> None:
    con.execute(
        """INSERT INTO scan_errors (path, stage, error, attempts, updated_at)
           VALUES (?,?,?,1,?)
           ON CONFLICT(path, stage) DO UPDATE SET
             error=excluded.error, attempts=scan_errors.attempts+1,
             updated_at=excluded.updated_at""",
        (path, stage, error, now),
    )


def mark_missing(con, folder_prefix: str, seen_paths: set[str], now: int) -> int:
    """Files under the prefix we did not see this run are marked, never deleted."""
    rows = con.execute(
        "SELECT id, path FROM files WHERE missing_since IS NULL AND path LIKE ? ESCAPE '\\'",
        (folder_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",),
    ).fetchall()
    gone = [r["id"] for r in rows if r["path"] not in seen_paths]
    for i in range(0, len(gone), 500):
        batch = gone[i:i + 500]
        con.execute(
            f"UPDATE files SET missing_since=? WHERE id IN ({','.join('?' * len(batch))})",
            [now, *batch],
        )
    return len(gone)
