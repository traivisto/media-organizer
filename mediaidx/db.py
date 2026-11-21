"""Schema and data access.

The identity model (spec §4.1): a photograph is an *asset*, keyed by content
hash; each path holding it is a *file*. Analysis attaches to the asset, so a
duplicate is never processed twice.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote

SCHEMA_VERSION = 7

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
  place_id           INTEGER REFERENCES places(id),
  place_source       TEXT,               -- gps | folder; never presented as equal
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

CREATE TABLE IF NOT EXISTS image_vectors (
  asset_id  INTEGER PRIMARY KEY REFERENCES assets(id),
  model     TEXT NOT NULL,
  embedding BLOB NOT NULL      -- float16; sqlite-vec holds the searchable copy
);

CREATE TABLE IF NOT EXISTS persons (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  source     TEXT NOT NULL,        -- seeded | manual
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS face_clusters (
  id         INTEGER PRIMARY KEY,
  person_id  INTEGER REFERENCES persons(id),
  size       INTEGER NOT NULL,
  centroid   BLOB NOT NULL,
  status     TEXT NOT NULL,        -- unnamed | named | ignored
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS faces (
  id         INTEGER PRIMARY KEY,
  asset_id   INTEGER NOT NULL REFERENCES assets(id),
  idx        INTEGER NOT NULL,     -- position within its photograph; names the crop on disk
  bbox       TEXT NOT NULL,        -- json [x, y, w, h], fractions of the image, not pixels
  det_score  REAL NOT NULL,
  blur_score REAL,
  embedding  BLOB NOT NULL,        -- 512 x float16
  cluster_id INTEGER REFERENCES face_clusters(id),
  person_id  INTEGER REFERENCES persons(id),   -- denormalised for query speed
  UNIQUE (asset_id, idx)
);

-- Places form a tree, so "photographs in Finland" can mean everything beneath
-- Finland without the user having to name its regions (spec 6.3).
CREATE TABLE IF NOT EXISTS places (
  id         INTEGER PRIMARY KEY,
  parent_id  INTEGER REFERENCES places(id),
  kind       TEXT NOT NULL,          -- country | admin1 | admin2 | locality
  name       TEXT NOT NULL,
  path       TEXT NOT NULL UNIQUE,   -- "Finland/Uusimaa/Helsinki"
  lat        REAL,                   -- representative point, for a map later
  lon        REAL,
  population INTEGER NOT NULL DEFAULT 0,   -- the gazetteer's own figure, or 0
  rank_pop   INTEGER NOT NULL DEFAULT 0,   -- see below; how ambiguity is resolved
  geonameid  INTEGER,
  UNIQUE (parent_id, name)
);

-- Every name a place answers to, which is the point of using a gazetteer at
-- all: this library's owner writes `Lontoo` and `Tukholma`, not London and
-- Stockholm. This is a match index and holds only the match key -- casefolded,
-- diacritics stripped. Nothing displays from here: the place's own name is in
-- `places.name`, and what the user wrote is in the folder label. Storing the
-- original spelling of all 1.1 million alternate names as well cost 60 MB and
-- bought nothing.
CREATE TABLE IF NOT EXISTS place_names (
  place_id INTEGER NOT NULL REFERENCES places(id),
  norm     TEXT NOT NULL,
  own      INTEGER NOT NULL DEFAULT 0,  -- the place's own name, not an exonym
  PRIMARY KEY (place_id, norm)
);

-- What a folder label resolved to, one row per folder, kept so every
-- machine-made guess can be looked at and thrown out. `candidates` is how many
-- places carried the name, so a 1 is a certainty and a 40 is a coin toss.
CREATE TABLE IF NOT EXISTS folder_places (
  folder_id  INTEGER PRIMARY KEY REFERENCES folders(id),
  place_id   INTEGER NOT NULL REFERENCES places(id),
  label      TEXT NOT NULL,
  candidates INTEGER NOT NULL,
  matched_at INTEGER NOT NULL
);

-- Coordinates binned to ~1 km, so a coastline photographed fifty times is
-- geocoded once. This is a cache and may be deleted at any time.
CREATE TABLE IF NOT EXISTS geo_cache (
  lat_bin  REAL NOT NULL,
  lon_bin  REAL NOT NULL,
  place_id INTEGER REFERENCES places(id),
  PRIMARY KEY (lat_bin, lon_bin)
);

-- A rejected folder-label match, kept so the same wrong answer is never
-- proposed twice. An empty place_path rejects the label outright rather than
-- one reading of it.
--
-- Keyed by path and not by place_id, because a place's id is assigned while
-- the gazetteer is being loaded and every id changes when it is reloaded. A
-- rejection stored by id would come back pointing at an unrelated town.
CREATE TABLE IF NOT EXISTS place_exceptions (
  label_norm TEXT NOT NULL,
  place_path TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (label_norm, place_path)
);

-- What the loaded gazetteer is, so a changed extract or changed loading rules
-- can be told from a merely absent one.
CREATE TABLE IF NOT EXISTS gazetteer_state (
  version   INTEGER NOT NULL,
  loaded_at INTEGER NOT NULL,
  places    INTEGER NOT NULL,
  names     INTEGER NOT NULL,
  source    TEXT NOT NULL
);

-- An event is a stretch of one day, or one trip, that the photographs
-- themselves attest to: a folder somebody labelled, or a run of pictures with
-- no long gap and no long journey inside it (spec 6.2).
--
-- Rebuilt from scratch on every `derive`, so no id here is stable and nothing
-- a person did may be stored against one. What a person did lives in
-- `event_names` and `event_cuts`, keyed by a photograph.
CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY,
  name        TEXT,
  name_source TEXT,            -- folder | user
  start_at    INTEGER NOT NULL,
  end_at      INTEGER NOT NULL,
  place_id    INTEGER REFERENCES places(id),
  folder_id   INTEGER REFERENCES folders(id),
  anchor_id   INTEGER REFERENCES assets(id),   -- its first photograph; see below
  asset_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS event_assets (
  event_id INTEGER NOT NULL REFERENCES events(id),
  asset_id INTEGER NOT NULL REFERENCES assets(id),
  PRIMARY KEY (event_id, asset_id)
);

-- A name somebody typed, and a decision never to be asked again. Keyed by the
-- event's *first photograph* rather than by its id, for the same reason a
-- rejected place is keyed by path: the ids are rebuilt and would come back
-- pointing at a different fortnight. A photograph is a fact about the archive
-- and survives every re-run.
--
-- If the grouping later changes so that the first photograph starts a shorter
-- event, the name follows it there -- which is the right half of a split to
-- keep it on, and visible either way.
CREATE TABLE IF NOT EXISTS event_names (
  anchor_id  INTEGER PRIMARY KEY REFERENCES assets(id),
  name       TEXT,
  dismissed  INTEGER NOT NULL DEFAULT 0,   -- asked once, answered "don't ask"
  created_at INTEGER NOT NULL
);

-- "This is not one event": a photograph that begins a new one whatever the
-- clock says. Also permanent, also keyed by a photograph, and the only way a
-- labelled folder is ever broken into more than one event.
CREATE TABLE IF NOT EXISTS event_cuts (
  asset_id   INTEGER PRIMARY KEY REFERENCES assets(id),
  created_at INTEGER NOT NULL
);

-- "This one belongs here after all": a photograph that sits far from its
-- event's centre and has been looked at anyway (spec 6.5). Keyed by the
-- photograph rather than by the event, because `derive` throws every event away
-- and builds them again -- the same reason `event_cuts` is.
CREATE TABLE IF NOT EXISTS odd_dismissed (
  asset_id   INTEGER PRIMARY KEY REFERENCES assets(id),
  created_at INTEGER NOT NULL
);

-- The questions worth asking, largest first. Rebuilt with the events, so it
-- holds no decisions either -- an answered event has a name, a dismissed one
-- has a row in `event_names`, and both are read back from there.
CREATE TABLE IF NOT EXISTS review_queue (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,      -- event | face_cluster
  ref_id      INTEGER NOT NULL,   -- events.id or face_clusters.id
  priority    INTEGER NOT NULL,   -- affected photographs; the queue order
  suggestion  TEXT,
  rationale   TEXT,
  status      TEXT NOT NULL,      -- open | skipped
  skipped_at  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_event_assets_asset ON event_assets(asset_id);
CREATE INDEX IF NOT EXISTS idx_events_start       ON events(start_at);
CREATE INDEX IF NOT EXISTS idx_queue_priority     ON review_queue(status, priority DESC);

CREATE INDEX IF NOT EXISTS idx_places_path   ON places(path);
CREATE INDEX IF NOT EXISTS idx_places_name   ON places(name);
CREATE INDEX IF NOT EXISTS idx_places_parent ON places(parent_id);
CREATE INDEX IF NOT EXISTS idx_place_names   ON place_names(norm);

-- What a person put right by hand (spec 4.7). Everything else in this schema
-- is derived and `derive` rebuilds it from nothing, so a correction cannot be
-- an edit of those tables -- the next rebuild would revert it -- but an input
-- consulted while they are rebuilt.
--
-- Keyed by content hash, never by row id: assets.id and faces.id are both
-- regenerated when a stage re-runs, and a correction stored by id would come
-- back pointing at something unrelated. `face_bbox` is the durable anchor for
-- a face, since `idx` is only the detector's output order and stage 2 hands
-- out new ones every time it runs (5.2).
--
-- `face_idx` is -1 rather than NULL for the kinds that are not about a face,
-- because SQLite counts NULLs as distinct in a UNIQUE constraint and would
-- happily store the same date correction twice.
CREATE TABLE IF NOT EXISTS corrections (
  id           INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL,             -- date | place | person
  content_hash TEXT NOT NULL,
  face_idx     INTEGER NOT NULL DEFAULT -1,
  face_bbox    TEXT,                      -- json [x, y, w, h], fractions
  value        TEXT,                      -- NULL asserts that no value is right
  note         TEXT,
  created_at   INTEGER NOT NULL,
  UNIQUE (kind, content_hash, face_idx)
);

CREATE INDEX IF NOT EXISTS idx_corrections_hash ON corrections(content_hash);
CREATE INDEX IF NOT EXISTS idx_faces_asset   ON faces(asset_id);
CREATE INDEX IF NOT EXISTS idx_faces_cluster ON faces(cluster_id);
CREATE INDEX IF NOT EXISTS idx_faces_person  ON faces(person_id);
CREATE INDEX IF NOT EXISTS idx_files_asset   ON files(asset_id);
CREATE INDEX IF NOT EXISTS idx_files_folder  ON files(folder_id);
CREATE INDEX IF NOT EXISTS idx_files_missing ON files(missing_since);
CREATE INDEX IF NOT EXISTS idx_assets_taken  ON assets(taken_at);
CREATE INDEX IF NOT EXISTS idx_assets_conf   ON assets(date_conflict);
CREATE INDEX IF NOT EXISTS idx_stage         ON stage_state(stage, status, version);
"""


def read_only_uri(db_path: str | Path) -> str:
    """The URI that opens a database so SQLite itself refuses a write.

    Built by hand because `Path.as_uri()` turns a network path into
    `file://server/share/...`, whose `server` SQLite reads as a URI authority
    and refuses -- found publishing to the NAS, where the database that had
    just arrived could not be opened to check it. With the authority left
    empty, `file:////server/share/...`, the path is the whole of it.
    """
    path = str(Path(db_path).resolve()).replace("\\", "/")
    if path.startswith("//"):
        path = "//" + path
    elif not path.startswith("/"):
        path = "/" + path                    # C:/x -> /C:/x, as SQLite expects
    return "file:" + quote(path, safe="/:") + "?mode=ro"


def connect_read_only(db_path: str | Path, **kw) -> sqlite3.Connection:
    """A connection that cannot write, to a database that must exist."""
    if not Path(db_path).exists():
        raise FileNotFoundError(f"no database at {db_path}")
    return sqlite3.connect(read_only_uri(db_path), uri=True, **kw)


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


# Indexes over columns that arrive by ALTER TABLE, so they cannot be created
# until the columns exist on an already-populated database.
LATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_assets_place ON assets(place_id);
"""


def migrate(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    _add_missing_columns(con)
    con.executescript(LATE_INDEXES)
    row = con.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        con.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif row["version"] < SCHEMA_VERSION:
        con.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))


# New tables arrive by `CREATE TABLE IF NOT EXISTS` above, but a new *column*
# on a table that already exists does not -- and the reference library's
# database is 40,000 assets and many hours of GPU work, so it is migrated in
# place rather than rebuilt. Each entry is checked against the live table, so
# running this on a fresh database is a no-op.
ADDED_COLUMNS = {
    "assets": [
        ("place_id", "INTEGER REFERENCES places(id)"),
        ("place_source", "TEXT"),
    ],
}


def _add_missing_columns(con: sqlite3.Connection) -> None:
    for table, columns in ADDED_COLUMNS.items():
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


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
