# Kuva-arkisto — Software Specification

**Version:** 0.1 (draft)
**Date:** 2026-09-06
**Status:** Design agreed, not yet implemented

A local, private index over a family photo archive stored on a NAS. Classifies and tags images
using locally-run AI models, groups them into events using capture metadata, and exposes the
result as a searchable database. No cloud services, no per-image cost, and no modification of the
original files.

---

## 1. Context

### 1.1 Reference library

The application is not built for one particular drive; it indexes whatever folders it is given
(§2, P7). The library below is the first intended target and the basis for every size and timing
estimate in this document, but nothing about its layout is assumed by the code.

Measured on 2026-09-06 at `\\TRaDiskStation\Media\Pictures` (mapped as `Z:`):

| Property | Value |
|---|---|
| Image files | 46,423 (40,884 JPG · 4,452 HEIC · 907 PNG · 180 JPEG) |
| Video files | 2,030 (1,592 MP4 · 329 MOV · 101 3GP · 31 MPG · 6 M4V) |
| Total size | 232.4 GB |
| Date span | 1998–2026 |
| Structure | 29 year folders, each containing `YYYY MM` month folders and named event folders |

Top level also contains `Anskun`, `Emmin`, `Lukan` (per-person folders), `Scans`, `Specials`,
`Unsorted`, `New Images`, `Saved Pictures`, and `_gsdata_` (GoodSync metadata).

Representative folder names: `2019 01`, `2019 05 Hennan rippijuhlat`, `2019 07 Alicante`. This
`YYYY MM Label` shape is a user convention the application can be taught (§5.1), not one it
presumes. None of these folders is indexed unless it is explicitly passed to `add`.

**Measured metadata coverage.** A stratified sample of 772 images, up to 30 per year folder,
2026-09-06:

| Signal | Coverage | Notes |
|---|---|---|
| EXIF `DateTimeOriginal` | **95%** | 100% in 1998–2001; the gap is concentrated in 2002 (40%) and 2003 (63%) |
| Filename `YYYYMMDD@HHMMSS-Make-Model-NN` | **95%** | Second-precision date *and* camera, in the filename itself |
| EXIF camera model | 93% | Present even in 1998 |
| EXIF GPS | 47% overall | 0% before 2010, ~90%+ from 2016 on, anomalous 37% in 2022 |
| **Neither EXIF date nor dated filename** | **4%** | ≈1,800 files, mostly 2002–2003 |

Two assumptions in earlier drafts were wrong and have been corrected throughout: the pre-2008
material is *not* short of EXIF, and this library carries a near-universal filename convention that
is more useful than the messaging-app patterns originally specified. No WhatsApp- or
camera-style filenames appeared in the sample at all — the rename has absorbed them.

### 1.2 Hardware

| Component | Specification |
|---|---|
| GPU | NVIDIA RTX 4080 SUPER, 16 GB VRAM |
| OS | Windows 11 Pro (26200) |
| NIC | Intel I225-V (2.5 GbE capable) — **currently linked at 100 Mbps** |
| Storage | NAS over that link |

**The network is the binding constraint on every fast-path stage, and it is ten times slower than
assumed.** Measured 2026-09-06 against `Z:\Pictures\2020`, 400 files, 1,177 MB, with each thread
count reading a different slice so the SMB client cache cannot flatter the result:

| Threads | Read only | Read + BLAKE3 |
|---|---|---|
| 1 | 10.8 MB/s | 11.4 MB/s |
| 4 | 11.6 MB/s | 11.8 MB/s |
| 8 | 11.8 MB/s | 11.9 MB/s |
| 16 | 11.8 MB/s | 11.9 MB/s |
| 32 | 11.8 MB/s | 11.8 MB/s |

Two conclusions, both of which correct earlier drafts:

1. **Parallel reads buy about 10%, not 10×.** The ceiling is reached by four threads and never
   moves again. Concurrency is not the lever this document previously claimed it was.
2. **Hashing is free.** BLAKE3 costs nothing measurable against the I/O, so there is no case for a
   partial-file hash.

11.8 MB/s is 94 Mbit — saturated Fast Ethernet, matching the negotiated link speed exactly. The
card supports 2.5 GbE, so the cause is external to this project: most likely a Cat5 or damaged
cable, a 100 Mbps switch port, or an I225-V negotiation issue. **Resolving it is the single
highest-value optimisation available to this system** — roughly a 10× reduction in every
network-bound figure below — and it requires no code.

### 1.3 Goals

1. Recognise and name recurring people by face.
2. Describe what is happening in each photograph.
3. Group photographs into events using time, location and folder name.
4. Make all of the above searchable from a single local interface.
5. Run unattended overnight; never require a human to sit and wait.

### 1.4 Non-goals (v1)

- Video indexing. Deferred; see §12.
- Writing tags, ratings or any data back to the original files or the NAS.
- Moving, renaming or reorganising any file.
- Cloud sync, multi-user accounts, or access from outside the local network.
- Photo editing.

---

## 2. Core principles

These are binding constraints, not preferences. Every design decision below follows from them.

**P1 — The archive is read-only.**
The application opens files for reading and never invokes a write, rename, delete or attribute-set
operation on any path under a scan root. This is enforced in code (a single guarded file-open
helper) and asserted in tests.

**P2 — The indexer never blocks on a human.**
No stage prompts, waits for input, or requires supervision. Work the application cannot complete
alone becomes a row in a review queue and the run continues.

**P3 — Every stage is resumable.**
Progress is committed per asset, per stage. A run killed at any moment resumes without redoing
completed work and without corrupting state.

**P4 — Read each file from the NAS once.**
A file is fetched and decoded a single time; all fast-path stages consume the same decoded buffer.

**P5 — Raw model outputs are kept, not just conclusions.**
Embeddings are stored. Re-clustering, merging identities, or changing search strategy must never
require re-reading the NAS.

**P6 — The index is disposable.**
Deleting the database and rebuilding must lose nothing except what a person supplied by hand —
names and corrections (§4.7) — which are exportable and re-importable, and which P8 requires be kept
through every lesser operation as well. Everything else is derived, and derived data is never the
record of a human decision.

**P7 — The application knows nothing about any particular drive.**
It indexes exactly the folders it is given, and no others. No host, share, folder name or naming
convention is hardcoded, and none is discovered by exploring around a given path. Everything
specific to a library — where it lives, how its folders are named, which folders belong to a
person — arrives as a command argument or a config entry supplied by the user. Pointing the
application at a second NAS, a local drive or an external disk requires no code change, and a
library that follows no convention at all still indexes correctly, just with less metadata.

**P8 — Nothing done by hand is ever lost.**
A name, a merge, a rejection, a correction — anything a person supplied — survives every operation
the application can perform on itself: re-scanning a folder, re-reading metadata, re-detecting faces,
re-clustering, rebuilding the derived tables, or deleting the database and building it again. Human
effort is the only input that cannot be recomputed, so it is anchored to the *content* of a
photograph and never to a row id, a path, or an ordering that a re-run may reassign. Where an
operation cannot preserve it, the operation is wrong and not the expectation. This is why corrections
are consulted where values are derived rather than stamped on afterwards (§4.7), and why a face is
re-identified by where it sits in the frame rather than by its position in a detector's output
(§5.2).

---

## 3. Architecture

### 3.1 Pipeline

```
   NAS (read-only)
        │
        │  read once, decode once
        ▼
 ┌─── FAST PATH ────────────────────┐
 │  1. Hash + EXIF + folder parse   │
 │  2. Face detect + embed          │
 │  3. Whole-image search vector    │      (~5.5 h measured at 100 Mbps;
 └──────────────┬───────────────────┘       ~40 min at gigabit)
                │  rows + vectors
                ▼
          photos.db  ◄──── 4. Captions (opt-in per folder, ~26 h full library)
                │
                │  queries only — no model inference
                ▼
          Search UI (local web)
```

### 3.2 Stages

| # | Stage | Model | Output | Est. full library |
|---|---|---|---|---|
| 1 | Identity & metadata | none | hash, EXIF, folder label, date | ~35 min |
| 2 | Faces | InsightFace `buffalo_l` | ~70,000 face rows + 512-d embeddings | ~45 min |
| 3 | Search vector | SigLIP / OpenCLIP | 1 vector per asset | ~30 min |
| 4 | Caption | vision LLM via Ollama | description + tags | ~26 h |

Stages 1–3 execute in a single pass over the file. Stage 4 runs as a separate, explicitly invoked
job. All timings are estimates; stages 1–3 are network-bound.

Derived work that runs after the stages, over the database only (no file access):

| Job | Input | Output |
|---|---|---|
| Face clustering | face embeddings | face clusters, seeded identities |
| Event detection | time, GPS, folder | event rows, event↔asset links |
| Queue building | unnamed events and clusters | review queue with suggestions |

### 3.3 Modules

```
mediaidx/
  cli.py              command surface
  config.py           TOML config load/validate
  db/
    schema.sql        DDL
    migrate.py        versioned migrations
    repo.py           typed data access
  scan/
    walker.py         directory traversal, exclusion rules
    identity.py       content hashing
    exif.py           EXIF extraction (exiftool)
    folders.py        folder-name parsing, date resolution
  vision/
    decode.py         image loading incl. HEIC, orientation, downscale
    faces.py          detection + embedding
    embed.py          whole-image search vectors
    caption.py        Ollama vision client
  derive/
    cluster.py        face clustering
    events.py         time/place/folder event grouping
    places.py         offline reverse geocoding
    queue.py          review queue construction + suggestions
  serve/
    app.py            FastAPI application
    search.py         query planning across FTS + vectors
    thumbs.py         thumbnail cache
    templates/        server-rendered HTML
  pipeline.py         orchestration, concurrency, checkpointing
```

### 3.4 Concurrency

```
[ N reader threads ] → [ decode pool ] → [ GPU batch queue ] → [ single DB writer ]
   network I/O          CPU, processes      batched inference     serialised commits
```

- Readers: 8 threads (configurable). Measured (§1.2), the read ceiling is reached at four threads
  and more do not help — the link, not concurrency, sets fast-path throughput. Eight is kept as a
  modest hedge for the day the link is fixed, when latency per file may start to matter again.
- Decode: process pool sized to physical cores. JPEG and HEIC decode is CPU-bound and releases
  no GIL benefit in-thread.
- Inference: single GPU worker consuming batches (default 32 for faces, 64 for embeddings).
- Writer: one thread owns the SQLite connection. All writes are serialised; WAL mode is enabled
  so the UI can read concurrently during an indexing run.

---

## 4. Data model

SQLite. WAL journal mode. `sqlite-vec` for vector similarity, FTS5 for text.

### 4.1 Identity: files vs assets

A photograph may exist at several paths (`Unsorted`, `New Images` and a year folder frequently
hold the same file). The logical photograph is an **asset**, keyed by content hash; each path is a
**file** pointing at it. All analysis attaches to the asset, so a duplicate is never processed
twice.

```sql
CREATE TABLE files (
  id            INTEGER PRIMARY KEY,
  path          TEXT NOT NULL UNIQUE,   -- UNC, e.g. \\TRaDiskStation\Media\Pictures\2019\...
  folder_id     INTEGER REFERENCES folders(id),
  asset_id      INTEGER REFERENCES assets(id),
  size_bytes    INTEGER NOT NULL,
  mtime         INTEGER NOT NULL,
  first_seen    INTEGER NOT NULL,
  last_seen     INTEGER NOT NULL,
  missing_since INTEGER                 -- set when a scan no longer finds it; never deleted
);

CREATE TABLE assets (
  id              INTEGER PRIMARY KEY,
  content_hash    TEXT NOT NULL UNIQUE, -- BLAKE3 of file bytes
  format          TEXT NOT NULL,        -- jpeg | heic | png
  width           INTEGER,
  height          INTEGER,
  orientation     INTEGER,
  taken_at        INTEGER,              -- unix seconds, local wall time
  taken_at_source TEXT,                 -- exif | filename | folder | mtime
  taken_at_precision TEXT,              -- second | day | month | year
  date_conflict   INTEGER DEFAULT 0,    -- filename and folder disagree; auditable, not fatal
  source_hint     TEXT,                 -- whatsapp | screenshot | telegram | camera | NULL
  gps_lat         REAL,
  gps_lon         REAL,
  place_id        INTEGER REFERENCES places(id),
  place_source    TEXT,                 -- gps | folder; never presented as equal
  camera_make     TEXT,
  camera_model    TEXT,
  lens            TEXT,
  iso             INTEGER,
  f_number        REAL,
  exposure_time   REAL,
  focal_length    REAL
);
```

### 4.2 Stage state — the resumability core

```sql
CREATE TABLE stage_state (
  asset_id   INTEGER NOT NULL REFERENCES assets(id),
  stage      TEXT NOT NULL,     -- meta | faces | embed | caption
  version    INTEGER NOT NULL,  -- bumped when the stage's model or logic changes
  status     TEXT NOT NULL,     -- done | failed | skipped
  updated_at INTEGER NOT NULL,
  error      TEXT,
  PRIMARY KEY (asset_id, stage)
);
```

A stage is pending for an asset when no row exists, or the row's `version` is below the stage's
current version. Changing the face model bumps its version and makes exactly that stage pending
again, for every asset, without touching the others.

### 4.3 Folders, places, events

```sql
CREATE TABLE folders (
  id            INTEGER PRIMARY KEY,
  path          TEXT NOT NULL UNIQUE,
  parent_id     INTEGER REFERENCES folders(id),
  parsed_year   INTEGER,   -- populated only when a configured folder pattern matches
  parsed_month  INTEGER,
  parsed_label  TEXT,      -- e.g. "Hennan rippijuhlat"; NULL when the name carries no label
  user_label    TEXT,      -- from `add --label`, overrides parsed_label
  person_hint   TEXT,      -- from `add --person`
  is_root       INTEGER NOT NULL DEFAULT 0,   -- passed to `add` directly, so `scan` revisits it
  root_added_at INTEGER
);

-- Places form a tree, so "photographs in Finland" can mean everything beneath
-- Finland without the user having to name its regions (§6.3).
CREATE TABLE places (
  id         INTEGER PRIMARY KEY,
  parent_id  INTEGER REFERENCES places(id),
  kind       TEXT NOT NULL,          -- country | admin1 | admin2 | locality
  name       TEXT NOT NULL,          -- "Helsinki"
  path       TEXT NOT NULL UNIQUE,   -- "Finland/Uusimaa/Helsinki"
  lat        REAL,                   -- representative point, for a map later
  lon        REAL,
  population INTEGER NOT NULL DEFAULT 0,  -- the gazetteer's own figure, or 0
  rank_pop   INTEGER NOT NULL DEFAULT 0,  -- own population, or the sum inside
  geonameid  INTEGER,
  UNIQUE (parent_id, name)
);

-- Every name a place answers to, normalised: casefolded, diacritics stripped.
-- A match index and nothing else -- no original spelling is kept, because the
-- display name is in `places.name` and what the user wrote is the folder
-- label. `own` separates a place's own name from an exonym, which is what
-- decides how much a match is trusted (§6.3).
CREATE TABLE place_names (
  place_id INTEGER NOT NULL REFERENCES places(id),
  norm     TEXT NOT NULL,
  own      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (place_id, norm)
);

-- What a folder label resolved to, one row per folder: a machine-made guess
-- from a single word, kept where it can be looked at and thrown out.
CREATE TABLE folder_places (
  folder_id  INTEGER PRIMARY KEY REFERENCES folders(id),
  place_id   INTEGER NOT NULL REFERENCES places(id),
  label      TEXT NOT NULL,
  candidates INTEGER NOT NULL,   -- how many places carried that name
  matched_at INTEGER NOT NULL
);

-- A rejected match, permanent and surviving every re-run. A NULL place_id
-- rejects the label outright rather than one reading of it.
CREATE TABLE place_exceptions (
  label_norm TEXT NOT NULL,
  place_id   INTEGER REFERENCES places(id),
  created_at INTEGER NOT NULL,
  PRIMARY KEY (label_norm, place_id)
);

-- Coordinates binned to ~1 km, so a coastline photographed fifty times is
-- geocoded once. This is a cache and may be deleted at any time.
CREATE TABLE geo_cache (
  lat_bin  REAL NOT NULL,
  lon_bin  REAL NOT NULL,
  place_id INTEGER REFERENCES places(id),
  PRIMARY KEY (lat_bin, lon_bin)
);

CREATE INDEX idx_places_path ON places(path);
CREATE INDEX idx_places_name ON places(name);

CREATE TABLE events (
  id          INTEGER PRIMARY KEY,
  name        TEXT,
  name_source TEXT,         -- folder | user
  start_at    INTEGER NOT NULL,
  end_at      INTEGER NOT NULL,
  place_id    INTEGER REFERENCES places(id),
  folder_id   INTEGER REFERENCES folders(id),
  anchor_id   INTEGER REFERENCES assets(id),   -- its first photograph
  asset_count INTEGER NOT NULL
);

CREATE TABLE event_assets (
  event_id INTEGER NOT NULL REFERENCES events(id),
  asset_id INTEGER NOT NULL REFERENCES assets(id),
  PRIMARY KEY (event_id, asset_id)
);

-- A name somebody typed, and a decision never to be asked again.
CREATE TABLE event_names (
  anchor_id  INTEGER PRIMARY KEY REFERENCES assets(id),
  name       TEXT,
  dismissed  INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);

-- "This is not one event": a photograph that begins a new one whatever the
-- clock says.
CREATE TABLE event_cuts (
  asset_id   INTEGER PRIMARY KEY REFERENCES assets(id),
  created_at INTEGER NOT NULL
);
```

**Every event is rebuilt from scratch on each `derive`, so no id here is
stable and nothing a person decided may be stored on one.** A typed name lives
in `event_names` and a split in `event_cuts`, both keyed by a *photograph* —
which is a fact about the archive and survives every rebuild. This is the same
reasoning that keys a rejected place by its path rather than by a gazetteer id
(§6.3): an id assigned during a rebuild comes back pointing at a different
fortnight.

The anchor is the event's earliest photograph. If the grouping later changes so
that photograph starts a shorter event, the name follows it there — the right
half of a split to keep it on, and visible either way.

### 4.4 Faces and people

```sql
CREATE TABLE persons (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  source     TEXT NOT NULL,   -- seeded | manual
  created_at INTEGER NOT NULL
);

CREATE TABLE face_clusters (
  id         INTEGER PRIMARY KEY,
  person_id  INTEGER REFERENCES persons(id),
  size       INTEGER NOT NULL,
  centroid   BLOB NOT NULL,
  status     TEXT NOT NULL,   -- unnamed | named | ignored
  updated_at INTEGER NOT NULL
);

CREATE TABLE faces (
  id         INTEGER PRIMARY KEY,
  asset_id   INTEGER NOT NULL REFERENCES assets(id),
  idx        INTEGER NOT NULL,  -- position within its photograph; names the crop on disk
  bbox       TEXT NOT NULL,     -- json [x, y, w, h] as fractions of the image, not pixels
  det_score  REAL NOT NULL,
  blur_score REAL,
  embedding  BLOB NOT NULL,   -- 512 × float16
  cluster_id INTEGER REFERENCES face_clusters(id),
  person_id  INTEGER REFERENCES persons(id),  -- denormalised for query speed
  UNIQUE (asset_id, idx)
);
```

Two details that were decided during implementation:

**`bbox` holds fractions, not pixels.** A pixel box is only meaningful against the exact
image size it was measured at — 1600 px here, but 320 or 1024 in the thumbnail cache and
anything at all in the original. Every consumer would have to know which. Fractions are
true against all of them.

**`idx` is the face's position within its photograph**, and is what names its cropped
thumbnail on disk. Re-running the stage hands out new row ids; the photograph and the
position of a face within it do not change, so a crop written today survives a rebuild.

`faces.person_id` is set directly only when a user confirms an individual face against its
cluster's assignment; otherwise it is derived from the cluster.

### 4.5 Search payloads

```sql
CREATE TABLE image_vectors (
  asset_id  INTEGER PRIMARY KEY REFERENCES assets(id),
  model     TEXT NOT NULL,
  embedding BLOB NOT NULL      -- float16
);

CREATE TABLE captions (
  asset_id   INTEGER PRIMARY KEY REFERENCES assets(id),
  model      TEXT NOT NULL,
  text       TEXT NOT NULL,
  tags       TEXT,             -- json array
  created_at INTEGER NOT NULL
);

-- vector indexes
CREATE VIRTUAL TABLE vec_images USING vec0(asset_id INTEGER PRIMARY KEY, embedding float[1152]);
CREATE VIRTUAL TABLE vec_faces  USING vec0(face_id  INTEGER PRIMARY KEY, embedding float[512]);

-- text index over everything human-readable
CREATE VIRTUAL TABLE fts_assets USING fts5(
  asset_id UNINDEXED, caption, folder_label, event_name, people, place, tokenize='unicode61'
);
```

`unicode61` handles Finnish diacritics correctly; `rippijuhlat` and `Hennan` tokenise as expected.

### 4.6 Review queue

```sql
CREATE TABLE review_queue (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,     -- event | face_cluster
  ref_id      INTEGER NOT NULL,  -- events.id or face_clusters.id
  priority    INTEGER NOT NULL,  -- affected asset count; queue is ordered by this desc
  suggestion  TEXT,              -- proposed name
  rationale   TEXT,              -- why it was proposed, shown to the user
  status      TEXT NOT NULL,     -- open | skipped
  skipped_at  INTEGER            -- skipped items return after the next derive run
);
```

The queue is rebuilt with the events and so holds no decisions either. There is
no `answered` or `dismissed` state to store, because an answered question has
produced a name and a dismissed one a row in `event_names`; both are read back
from there, and a row that is neither is simply gone. `skipped` is the one
status worth keeping, and only until the next `derive` — which is precisely
what skipping means.

### 4.7 Corrections

Everything in §4.3–§4.6 is derived, and `mediaidx derive` rebuilds it from nothing. So a correction
made by hand cannot be an edit of those tables — the next rebuild would silently revert it — but an
**input consulted while they are rebuilt**. This is the rule that already governs a rejected
folder-place match (§6.3), generalised to everything a person can put right by hand.

```sql
CREATE TABLE corrections (
  id           INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL,     -- date | place | person
  content_hash TEXT NOT NULL,     -- the photograph; never assets.id
  face_idx     INTEGER,           -- kind = person: which face within it; otherwise NULL
  face_bbox    TEXT,              -- kind = person: the durable anchor; see below
  value        TEXT,              -- the correct answer; NULL asserts that none is right
  note         TEXT,              -- optional, in the user's own words
  created_at   INTEGER NOT NULL,
  UNIQUE (kind, content_hash, face_idx)
);
```

**Keyed by content hash, never by row id.** `assets.id` and `faces.id` are regenerated whenever a
stage is re-run, so a correction stored by id would come back pointing at something unrelated — which
is exactly why place rejections are keyed by path. The content hash identifies the photograph itself
and survives a move, a rename, a re-scan, a re-add, and a database built from nothing.

**A face is anchored by its box, not by its index.** `faces.idx` is the detector's output order
(§5.2), and stage 2 replaces every face row for a photograph when it re-runs, so `idx = 2` after a
re-detection may be a different person entirely. The bounding box is stored as fractions of the
image rather than pixels (§4.4), which makes it stable across a re-detection and a resize alike, so
a correction re-attaches to whichever new face overlaps its recorded box most — and to none, if the
detector no longer finds a face there. `face_idx` is kept as the fast path for the common case where
nothing has been re-detected; `face_bbox` is what makes the correction survive when it has (P8).

**The boundary.** A correction is tied to the bytes of a photograph. Re-encoding, rotating or
re-saving the file produces a different content hash and therefore a different photograph, and the
correction does not follow it. This is a deliberate limit rather than an oversight: at that point the
application cannot tell a re-saved original from a different picture, and inventing a link between
them would be a guess presented as a record. Under P1 the archive is read-only, so nothing the
application does can trigger this — only an edit made elsewhere.

| Kind | Corrects | Consulted by |
|---|---|---|
| `date` | `assets.taken_at`, where EXIF, folder and filename disagree | stage 1, `events` |
| `place` | `assets.place_id` for one photograph | `places` |
| `person` | `faces.person_id` for one face | `faces cluster`, both build and assign |

**Consulted where the value is derived, never stamped on afterwards.** Each of these reads
`corrections` at the point it computes the column, in the same way `places.run` already reads its
rejection and block sets. The alternative — one pass that overwrites the derived tables after the
fact — was considered and rejected, because it is only correct if every path runs it. `mediaidx
places` recomputes `place_id` for the whole library outside `derive`, and `mediaidx meta` prints
advice to run exactly that; under a single pass, following the application's own instructions would
silently revert every place correction until the next `derive`. A correction that depends on the
order commands were run in is not a correction (P8).

**A person correction outranks the vote.** A rebuild re-derives each cluster's identity by majority
vote among its members' previous names (§6.1). A single corrected face is one vote inside a cluster
whose majority is the person it was wrongly grouped with, so it would lose that vote every time. An
overridden face therefore does not vote, and its correction is re-applied after the count. Incremental
`assign` skips corrected faces outright.

**What a person correction does not do.** It fixes that face, and with it the photograph and every
search that joins through `faces.person_id`. It teaches the clusterer nothing: the next rebuild groups
by appearance exactly as before and the correction is re-applied on top. Constrained clustering is out
of scope.

**Consulted when derived, and written through when given.** Consultation alone is not enough for
every kind: a date is decided once, when the photograph is first indexed, so a correction made
afterwards would sit in the table doing nothing until a re-index that never comes. A correction is
therefore also applied to the row it corrects the moment it is made. The two together are what
"corrected" means — right immediately, and right again after every rebuild. Withdrawing a correction
does not restore the machine's answer; that returns at the next derivation, because knowing an answer
is wrong is not the same as knowing the right one.

**Corrections are irreplaceable.** With names, they are the only content a rebuild cannot recreate
(P6), and they leave and re-enter the database by the same export. `mediaidx corrections` lists them
and `--export` writes them out, because something that must be backed up should be something you can
look at.

### 4.8 Storage budget

| Content | Size |
|---|---|
| Image vectors (46k × 1152 × fp16) | ~107 MB |
| Face embeddings (~70k × 512 × fp16) | ~72 MB |
| Rows, metadata, captions, FTS | ~120 MB |
| **Database total** | **~300–400 MB** |
| Thumbnail cache (local disk, separate) | ~1.5 GB |
| Face crops, 160 px (~40k × ~7 kB) | ~300 MB |

---

## 5. Stage specifications

### 5.1 Stage 1 — Identity and metadata

**Traversal.** Recursive walk of the folders passed to `add`, and nothing above or beside them.
The walk never ascends to a parent, never follows a symlink or junction out of the given subtree,
and never indexes a sibling folder because it looked relevant. A folder is indexed when, and only
when, the user names it.

Excluded by directory name: `_gsdata_`, `.git`, `@eaDir`, `#recycle`, `$RECYCLE.BIN`,
`System Volume Information`. Excluded by filename: `Thumbs.db`, `.DS_Store`, `desktop.ini`. These
are defaults covering artifacts common to sync tools, NAS firmware and desktop shells; they are
config entries (§9), not knowledge about a specific drive. Only an allowlist of extensions is
opened: `.jpg .jpeg .png .heic .heif` (v1).

**Roots.** A folder passed to `add` is recorded as a root so that `scan` can revisit it later.
Roots therefore accumulate from use rather than being declared in advance, and `mediaidx roots`
lists and forgets them. Forgetting a root stops re-scanning it; it does not remove already-indexed
photographs from the database.

**Hashing.** BLAKE3 over the full file. A file whose `path`, `size_bytes` and `mtime` all match
an existing row is skipped without reading its contents — this is what makes a re-scan cheap.

**EXIF.** Extracted with `exiftool` in `-stay_open` batch mode, which handles HEIC, maker notes
and malformed headers far better than pure-Python readers. Fields: `DateTimeOriginal`,
`OffsetTimeOriginal`, GPS, orientation, camera and exposure data.

**The exiftool bridge must be forced to UTF-8.** `pyexiftool` defaults to the process codepage,
which on this machine is cp1252, and decoding fails outright on the first file whose metadata
contains a byte outside that range — Finnish characters or binary maker-note data. This is not a
rare edge case; it was hit within the first 200 files sampled. Required construction:

```python
exiftool.ExifToolHelper(
    executable=EXIFTOOL,
    encoding="utf-8",
    common_args=["-G", "-n", "-charset", "filename=utf8"],
)
```

Verified against the reference library on 2026-09-06. `mediaidx doctor` asserts it by round-tripping
a filename containing `ä`.

**Date resolution.** First match wins; the winning rule is recorded in `taken_at_source` and the
precision it implies in `taken_at_precision`:

| Rule | Source | Precision |
|---|---|---|
| 1 | EXIF `DateTimeOriginal` | second |
| 2 | Filename pattern | second or day |
| 3 | Parsed folder year + month | month |
| 4 | Parsed folder year | year |
| 5 | File mtime | second, flagged unreliable |

This is what keeps the 1998–2008 scans on the timeline instead of in an unknown-date heap. The UI
must display low-precision dates as such (`June 2019`, not `1 June 2019 00:00`).

**Filename dates.** Messaging apps strip EXIF entirely — a WhatsApp image arrives with no metadata
at all — but they encode the date in the filename, and so do most phone cameras. A configurable,
ordered pattern list is tried against the filename, each yielding a date and an optional
`source_hint`:

| Pattern | Example | Hint | Precision |
|---|---|---|---|
| **`(\d{8})@(\d{6})(?:-(.+?)-(.+?))?-\d+`** | `20240101@151202-Apple-iPhone 15-03.HEIC` | archive | **second** |
| `IMG-(\d{8})-WA\d+` | `IMG-20190608-WA0012.jpg` | whatsapp | day |
| `(?:IMG\|VID)_(\d{8})_(\d{6})` | `IMG_20190608_134022.jpg` | camera | second |
| `PXL_(\d{8})_(\d{6})\d*` | `PXL_20190608_114022123.jpg` | camera | second |
| `Screenshot[ _-](\d{4}-\d{2}-\d{2})` | `Screenshot_2019-06-08-13-40-22.png` | screenshot | second |
| `photo_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})` | `photo_2019-06-08_13-40-22.jpg` | telegram | second |
| `(\d{8})_(\d{6})` | `20190608_134022.jpg` | camera | second |

**Only the second pattern onward ships with the application.** Those describe how *applications*
name files — a phone, a messaging app — and are identical on everyone's machine, so they are
reasonable defaults.

**The first pattern does not ship.** `YYYYMMDD@HHMMSS-Make-Model-NN` is how one library was
renamed; no other archive uses it, so under P7 it belongs to that library's owner and lives in
their `config.toml` alone. It is listed here because it matches 95% of the reference library
(§1.1) and carries the camera make and model as well as second precision — serving as a fallback
for `camera_make` / `camera_model` on the 7% of files whose EXIF lacks them — not because the
application knows anything about it.

Library-specific patterns are supplied through `filename_patterns_extra` and
`folder_patterns_extra`, which are tried *before* the shipped list. This means adding your own
convention never requires restating the universal ones, and the two sets stay visibly separate.
`tests/test_config.py` asserts that the archive pattern is absent from the shipped defaults, that
extras take precedence, and that supplying extras does not discard the defaults.

**Sanity bounds.** A parsed filename date is rejected unless it falls between 1990-01-01 and
tomorrow. This prevents resolution numbers, serial numbers and version strings from being read as
dates, which is the main failure mode of filename parsing.

**Precedence and conflicts.** Filename ranks above folder because it yields second precision
against the folder's month. Where a filename date and a folder date disagree by more than the
folder's precision window, the filename wins and `date_conflict` is set; these are listed by
`mediaidx status` for review rather than silently resolved.

**Cross-validation.** Because EXIF and a dated filename are *both* present in roughly 95% of the
reference library, the two can be checked against each other for free. Agreement is the normal
case and needs no action. Disagreement beyond a minute means one of them is wrong — a camera clock
never set, a timezone applied inconsistently, or a file renamed from a different source. EXIF still
wins, but `date_conflict` is set so the disagreements can be inspected as a group. On a 46,000-image
archive this is the only practical way to discover a systematically wrong camera clock, which would
otherwise scatter a whole year's photographs across the timeline.

**Source hints** are worth keeping beyond dating: `source_hint = 'screenshot'` lets the UI exclude
screenshots from search results by default, which materially cleans up a family archive.

**Folder parsing.** An optional, user-supplied list of named-capture regexes is tried in order
against each leaf folder name, yielding any of `year`, `month`, `day` and `label`. The default
list contains one pattern matching the reference library's convention:

```
^(?P<year>\d{4})(?:\s+(?P<month>\d{2}))?(?:\s+(?P<label>.+))?$
```

A folder matching no pattern simply contributes no parsed metadata, and indexing proceeds
normally — folder parsing is an enrichment, never a requirement. A library with a different
convention adds its own pattern; a library with no convention removes them all. Where a label is
found it is indexed for search immediately.

**Labelling a folder directly.** `add --label "Alicante 2019"` attaches a label to the folders
being indexed regardless of their names, which covers libraries whose structure carries no
information at all.

**Person hints.** `add --person "Anski"` marks the folders in that invocation as predominantly
depicting one person, producing a soft hint used later to propose names for face clusters (§6.1).
This is an argument to the command rather than a configured folder mapping: the application never
infers a person from a folder name, and a Finnish genitive such as `Anskun` is not reliably
invertible in any case. The hint is stored against the folder so it survives re-scans.

**Reverse geocoding.** Offline, from a bundled GeoNames extract. Coordinates are binned to ~1 km
before lookup so results are cached in `geo_cache` and repeated lookups cost nothing (§6.3).

**GPS is signed by a separate tag.** `EXIF:GPSLatitude` and `EXIF:GPSLongitude` are unsigned
magnitudes; the hemisphere is in `GPSLatitudeRef` and `GPSLongitudeRef`, and exiftool's
`Composite:GPS*` pair is the two combined. Reading the magnitude alone is silently wrong rather
than obviously wrong — an unsigned coordinate is still a valid coordinate — and it filed every
western photograph in this library at its eastern mirror image. Tenerife came out in the Libyan
desert, Massachusetts in Kyrgyzstan. The sign is not recoverable from what was stored, so the
repair is to re-read: `mediaidx meta` re-extracts metadata for every asset whose stage 1 row is
behind the current version, headers only, at ~37 files/s against the ~3.2 of a hashing pass.

### 5.2 Stage 2 — Faces

- **Model:** InsightFace `buffalo_l` (SCRFD detector + ArcFace R100 embedder) on
  `onnxruntime-gpu` with the CUDA execution provider. Only the detection and recognition
  models are loaded; `buffalo_l` also ships age, gender and landmark models, which nothing
  here uses.
- **Input:** the decoded, orientation-corrected image, longest side capped at 1600 px.
- **Filtering:** faces with `det_score < 0.5`, or a bounding box under 40 px on its shorter side,
  are discarded — too small to embed reliably and a major source of spurious clusters.
- **Detect, filter, then embed — in that order.** Measured on a random sample of this library,
  **46% of raw detections fail the filters** (72 detections, 39 kept, one photograph alone
  contributing 19 rejects from a crowd behind the subject). Running recognition over all of
  them and discarding the results afterwards is wasted work, and invites the mistake of
  storing them. Recognition therefore runs once per photograph, batched over the survivors.
- **Output:** one row per surviving face with a 512-d L2-normalised embedding stored as float16.
- **Face crops are written during this pass**, at 160 px, into the thumbnail cache. This is the
  pass that has the photograph in memory; clustering and the review UI that follow are
  database-only *provided* the pictures they must show are already local. Cropping them on
  demand instead would mean reading tens of thousands of originals back across the link to
  render one screen — the second pass the whole design exists to avoid (P4).
- **Measured volume, full pass 2026-09-07: 45,648 faces in 18,284 photographs**, from
  39,851 searched. That is 1.14 kept faces per photograph, against an original estimate of
  60,000–80,000. 33 photographs failed, all of them files truncated in the archive itself —
  the same ones stage 3 could not read.

**Measured, 2026-09-07, RTX 4080 SUPER:** 22 images/second on the GPU, against ~3.2 delivered
by the link. As with stage 3, the model is several times faster than the pipeline can feed it,
so inference runs on a single thread and reader threads are the only lever that matters.

**Re-detection must not lose an identity (P8).** A re-run replaces every face row for a photograph,
and `idx` is only the detector's output order, so nothing that matters may be keyed to it. Before the
old rows go, the name and any `person` correction on each of them are held with their bounding boxes;
after the new rows are written, each is re-attached to the new face whose box overlaps it most, above
a minimum overlap. A face the detector no longer finds keeps nothing — there is nothing left to keep
it on — and that is reported rather than passed over in silence.

This has not yet bitten, because stage 2's version has never been raised. It would take every one of
the **19,232 named faces** with it the first time it were, which is why it is stated here as a
requirement on the stage rather than left as a property of how it happens to be invoked today.

### 5.3 Stage 3 — Search vector

- **Model:** SigLIP (SO400M class) preferred; OpenCLIP ViT-L/14 (`laion2b_s32b_b82k`) as the
  fallback baseline. Configurable by name; changing it bumps the stage version.
- **Input:** decoded image, resized to the model's native input.
- **Output:** one L2-normalised vector per asset, float16.
- **Why it exists:** images and text share one vector space, so a typed phrase ranks photographs
  directly. This is the primary search mechanism, and it does not depend on stage 4.

### 5.4 Stage 4 — Captions

- **Runtime:** Ollama over its local HTTP API.
- **Model:** `gemma4:12b`, already installed. Verified vision-capable via `ollama show`:

  | Installed model | Size | Vision | Verdict |
  |---|---|---|---|
  | `gemma4:12b` | 7.6 GB | yes (+ audio) | **Default.** Largest that fits with headroom |
  | `qwen3.5:9b` | 6.6 GB | yes | First fallback if captions disappoint |
  | `qwen3.5:27b` | 17 GB | yes | Exceeds 16 GB VRAM — see below |
  | `qwen2.5:14b` | — | no | Text only, unusable here |

  Start with one model and only compare others if its output is poor. `qwen3.5:27b` is likely the
  highest quality of the three but its weights exceed available VRAM, so layers spill to system
  RAM and throughput collapses — unusable for 46,000 images, but perfectly reasonable via
  `caption --model qwen3.5:27b` on a few hundred photographs that matter, where 10 s each is
  irrelevant. The CLI already supports this, so no design change is needed to exploit it.

- **Thinking must be disabled.** All three vision models expose a `thinking` capability. Reasoning
  tokens before a one-sentence caption would multiply runtime for no benefit, so requests set
  `think: false` explicitly. This is the single largest throughput factor in this stage.
- **Input:** image downscaled to ≤1024 px on the long side, to avoid spending image tokens on
  detail the model discards anyway.
- **Prompt:** requests two things in one response — a single descriptive sentence, and 3–8 short
  tags — returned as JSON. Malformed responses are retried once, then recorded as failed.
- **Concurrency:** 1. Ollama does not batch vision requests usefully; parallel requests contend
  for the same VRAM.
- **Invocation:** never part of `add`. Always an explicit `caption` command against named folders.

---

## 6. Derived jobs

### 6.1 Face clustering

Runs over embeddings in the database; touches no files.

1. **Initial build:** HDBSCAN over all face embeddings (cosine), `min_cluster_size = 4`. Noise
   points are left unclustered rather than forced into a cluster.
2. **Auto-naming:** a cluster whose members are overwhelmingly drawn from folders carrying one
   person hint (§5.1) is proposed with that name. This is a proposal in the review queue, never a
   silent assignment. Where no hints were supplied, every cluster simply arrives unnamed.
3. **Incremental assignment:** new faces from later runs are matched both against existing cluster
   centroids and against **every face somebody has named**, each speaking for itself. Above the
   assign threshold they join; below it they accumulate as unclustered and are re-clustered when
   enough have gathered.

   A hand-named face is the surest evidence in the database — a person looked at it and said who it
   was — and until 2026-09-12 it counted for nothing here, because a centroid is computed from
   cluster members and a hand-named face usually belongs to no cluster. Measured on this library:
   91 such faces, and 46% of all faces unclustered. Against centroids alone, assignment reached
   3,455 of them; against the named faces as well, **5,749**, taking the named total from 19,512 to
   **25,084** in two seconds. Emmi, corrected by hand 41 times, went from 999 faces to 4,339.

   It is also the better shape: one centroid cannot describe somebody four years old in one
   photograph and seventeen in another — the very thing that makes one person arrive as several
   clusters.

   **A face may reach further when the ones around it agree.** Checked by eye on this library,
   2026-09-12: at a distance of 0.42 the ten furthest matches were all correct, so that bound is
   sound but conservative — 14,934 faces were still unnamed. Raised to 0.50 on its own, two of the
   twelve at the new edge were somebody else. Requiring the **five nearest named faces to name the
   same person** excluded exactly those two and kept the other ten, so the looser bound is allowed
   and the vote is what pays for it. Both bounds are in force: near enough is enough on its own, and
   a little further needs the agreement. A person with fewer than five named faces is reachable only
   by the strict bound, which is right — one example is not evidence enough to name a hundred more.

   Measured after: a further **2,045 faces**, taking the named total to **27,129** of 45,648.

   **A guess never becomes evidence.** Only a cluster member or a hand correction is ever an anchor.
   A face that `assign` itself named is given no cluster and speaks for nobody, because a name that
   spreads from one near-miss to the next walks away from whoever it started as.
4. **Full rebuild:** `mediaidx faces cluster --rebuild` recomputes from scratch, preserving
   `person` assignments by majority vote of each old cluster's members. Cheap, because embeddings
   were kept (P5). A face carrying a `person` correction (§4.7) is excluded from the vote and has
   its correction re-applied afterwards, because a hand-corrected face is by definition in the
   minority of the cluster it was wrongly placed in.

**Measured, 2026-09-07.** The full library: **45,648 faces into 737 clusters in 19 minutes**,
covering 54% of faces, with 20,774 left as noise rather than forced into a cluster. (An earlier
trial over 8,786 faces gave 153 clusters and 70% coverage; the full archive holds far more
one-off strangers, so a lower share clustering is the right direction.) Cost grows with the
square of the face count, and it is re-runnable whenever a threshold changes. Random unit vectors, the worst case for the tree search, take
about twice as long as real embeddings at the same count, which is the right direction: real
faces cluster, and clustered data prunes better.

**HDBSCAN needs contrast.** A dataset containing exactly one group is labelled noise in its
entirety, because there is nothing for the density estimate to stand against. This never arises
on a real library and is entirely an issue for tests, which must seed several groups.

**Merging is a first-class operation.** A person photographed across 28 years will produce several
clusters — children especially, whose embeddings drift substantially with age. The review UI must
make merging two clusters a single action, and the data model must allow many clusters per person.

### 6.2 Event detection (M4b)

Runs entirely over the database, like places and clustering: no file is read, no
model runs, no network is touched. **Measured: the whole of `derive` — places,
events and the queue over 39,884 photographs — is 2.3 seconds**, which is why it
is re-run rather than incrementally maintained.

Assets are grouped by:

1. **Folder first.** A folder with a parsed label (`2019 07 Alicante`) is authoritative — it
   becomes an event with `name_source = folder`, and no question is queued. The label is
   inherited down its subtree, so `2001 01 Thaimaa\skannatut` is part of the same trip —
   the same inheritance rule places uses (§6.3).
2. **Time and place otherwise.** Within a bare month folder, assets are split at gaps exceeding
   4 hours, then further split when GPS positions separate by more than 25 km. Both thresholds are
   configurable.
3. **Minimum size.** Groups below 5 assets are not promoted to events and never enter the queue.
   **A labelled folder is exempt**: the floor exists to keep specks out of the review queue, and
   a folder somebody named is never asked about, so a labelled trip of three photographs is still
   an event.

Assets without GPS group on time and folder alone. Measured availability (§1.1) makes the split
sharp: **no GPS at all before 2010**, patchy through 2015, near-universal from 2016. So place-based
grouping is effectively a feature of the last decade, and the first twelve years of the archive
rely entirely on time gaps and folder labels — which the 95% filename-date coverage makes reliable.

An asset filed in two folders belongs to the event its earliest copy was found in — the same
tie-break places uses, so a duplicate is never in two events.

**Where an event was.** Not the commonest place among its photographs: a week driving through
Spain is in Spain, not in whichever village produced the most pictures. Place paths are taken
commonest first until 80% of the *placed* photographs are covered, and what those share is the
answer — for a single afternoon the town itself, for a road trip the country. One stray
coordinate (an airport in another country) therefore cannot coarsen the whole event to nothing.

**Splitting.** "Not one event" cuts at the longest interruption inside the event, one press at a
time, so the result is predictable and the button can simply be pressed again. It is offered only
where a cut leaves a real event on **both** sides. Without that rule an event of evenly spaced
photographs is cut after its first one, and the orphan then falls below the minimum size and drops
out of the timeline — a button called *not one event* would be quietly deleting a photograph.

**Measured, 2026-09-08:** 1,374 events over 34,180 of 39,884 photographs (86%) — 165 from folder
labels and 1,209 from the clock; 5,704 photographs sit in runs shorter than five and are in no
event. **The estimate of 400–600 events below was wrong by a factor of two and a half**, and no
threshold fixes it: raising the time gap to 8 hours *increases* the count to 1,407, because
merging short runs pushes them over the minimum size. Twenty-eight years of ordinary photography
really is that many separate afternoons. What follows from that is in §6.4.

Two events span more than a year — `2012 11 Thanksgiving` reaches back to 2000 and
`2010 11 Caribbean Cruise` to 2005. Both are folder-named and correctly held together; the spans
are a camera clock that was never set, and they are already listed by `status --conflicts`.

### 6.3 Places (M4a)

Runs entirely over the database. No file is read, no model runs, no network is
touched. A full pass over the library takes about ten seconds and can be repeated
whenever the gazetteer or the matching rules change — which is why this milestone
is scheduled independently of the expensive indexing passes.

**Gazetteer.** A GeoNames extract — `cities500` plus the admin and country tables,
16 MB compressed and ~44 MB on disk — is loaded into `places` as a tree on first
run. **Measured:** 285,144 places (252 countries, 51,450 regions, 233,442
localities) and 1,147,379 names in 22 seconds, adding ~175 MB to the database.
2,260 localities are dropped as same-named siblings under one parent, the most
populous of each pair surviving.

Alternate names are retained, which matters here: `Lontoo` and `Tukholma` are how
this library's owner names London and Stockholm, and both arrive in the
`cities500` alternate-name column at no extra cost. Only the normalised form is
stored — the table is a match index, and keeping the original spelling of 1.1
million names as well cost 60 MB and bought nothing.

**Source 1 — coordinates.** GPS is binned to ~1 km and looked up against the
gazetteer by nearest neighbour (a haversine ball tree over the localities), then
cached in `geo_cache`. `place_source = 'gps'`. The query runs once per distinct
bin, not once per photograph, so a week in one town costs a handful of lookups.

*What a hit is allowed to claim.* The gazetteer holds points, not borders, so
distance is the only evidence about whether the nearest town is really where a
photograph was taken. Within **25 km** the locality is the answer; out to 100 km
only its region is; beyond that only the country; past 500 km nothing is claimed.
A photograph taken mid-Baltic belongs to no locality, and saying otherwise would
be inventing precision the data does not contain.

**Source 2 — folder labels.** A parsed folder label (§5.1) is matched against
gazetteer names as a whole-token, case- and diacritic-insensitive comparison.
`2019 07 Alicante` resolves to Alicante, Spain. `place_source = 'folder'`, and the
label is inherited by subfolders, so `2001 01 Thaimaa\skannatut` is placed by the
folder above it.

This is what extends place search to the first two decades of the archive, which
have no GPS at all (§1.1). It is also the riskier source, so it is constrained:

- Only labels matching a gazetteer entry **in full** are accepted; no substring or
  fuzzy matching. `Hennan rippijuhlat` matches nothing and is left alone.
- **A place's own name counts at any size; an exonym has to be a place people talk
  about.** Bad Ischl (1,914 people) is matched by its own name and accepted. A
  match that exists only in the alternate-name column must clear a population
  floor, default 50,000 — without it `Nina` resolves to Neenah, Wisconsin, and
  `TBD` to Timbiquí, Colombia. Measured on the reference library, the floor is
  what separates `Lontoo`, `Hampuri`, `Praha` and `Tallinna` from a village that
  happens to be spelled like a Finnish given name.
- Where a name is ambiguous the entry with the greater **`rank_pop`** wins, and
  the number of candidates is recorded so an ambiguous match can be reviewed.
  `rank_pop` is a place's own population or the sum of what is inside it, because
  GeoNames gives a region no population at all: without it Florida the state
  arrives as 0 and loses to Floridablanca, Colombia.
- A folder-derived place is **coarse by construction**: no coordinates are
  invented, and both the UI and the photograph's own page label it as coming from
  the folder name.
- Any match can be rejected in the Places view, which records a permanent
  exception and re-runs the folder source. Coordinates are not touched by a
  rejection — nothing about a wrong label changes what a receiver recorded.

**Precedence.** GPS always wins over a folder label for the same asset. A folder
label never overrides a coordinate, because the folder says where photographs were
filed and the coordinate says where one was taken.

**Reading it back.** `places.path` is a materialised path, so a place and
everything beneath it is one prefix query: `Finland` means the whole country
without anyone naming its regions (§7). Counts shown in the UI are counts of the
whole subtree.

**Expected coverage**, from the measured sample (§1.1):

| Source | Share of library | Era |
|---|---|---|
| GPS | **51%** — 20,402 of 39,884, all placed | 2010 onward, ~95% from 2016 |
| Folder label | **6%** — 2,428 more, from 35 of 165 labels | all years |
| Neither | 43% | bare month folders before 2010 |

**Measured, 2026-09-08:** 22,830 of 39,884 photographs placed (57%) across 39
countries, in 11 seconds over 2,669 distinct 1 km bins. Of the 35 folder labels
that matched, five are wrong and reviewable — `USA` lands on Concord, North
Carolina, and `West Coast` on New Zealand — which is the reason every one of
them is listed for rejection rather than merely counted.

### 6.4 Review queue construction (M4b)

For each unnamed event and unnamed face cluster **above a floor**, a queue row is created with
`priority` set to the number of photographs affected — **the queue is always ordered
largest-first**, so the earliest questions cover the most photographs.

**Why there is a floor at all.** Promotion to an event and promotion to a *question* are two
different decisions, and this is what the measurement in §6.2 forced. Five photographs is enough
to be an event — it belongs in the timeline, it is browsable, a place query finds it — but 1,209
unnamed ones is not a finite queue, it is a second job. So everything of five or more is an event,
and only the substantial ones are asked about:

| Floor | Config | Default | Questions on the reference library |
|---|---|---|---|
| Event | `queue_min_assets` | 20 | 158 of 1,209 unnamed events |
| Face cluster | `queue_min_faces` | 10 | 156 of 576 unnamed clusters |

Setting them to `min_event_assets` and `cluster_min_size` restores the literal behaviour above,
and the queue becomes 1,785 questions. **Measured, 2026-09-08: 314 questions, covering 7,726
photographs.** The tail is not lost — it is in the Events and People views, biggest first, whenever
somebody wants it.

**Suggestions.** Where the database already knows something, the application proposes an answer
and states its rationale, because correcting a proposal is far less work than composing one:

> Ansku in 22 of 30 photographs; 28 of 30 placed in Alicante by coordinate.
> Suggested: *Ansku in Alicante*

Two sources today — the people whose faces have been named, and the place. Captions are the third
and arrive with M5. Both sources are held to a share of the whole event, not of the subset that
happens to carry the evidence: a person must be in **40%** of its photographs and a place must
hold **40%** of them. The place rule matters more than it sounds. An event's place is decided
among the photographs that *have* a coordinate, which on this archive can be a fifth of them, and
"New York County" proposed on the strength of 27 photographs out of 124 is a guess wearing a
fact's clothes. The place is still shown among the event's facts; it is only kept out of the
proposed name. **Measured: 85 of 314 questions carry a suggestion**; the rule removed 23 that
did not deserve one. Where nothing is known, no suggestion is offered — a date is not a name, and
one would only have to be deleted.

**Skipping.** A skipped item returns to the queue after the next derive run. Questions get easier
as surrounding events and faces acquire names; "some party in Turku" on the first pass is often
obvious on the second.

### 6.5 The odd one out (M4c)

A trip is a few hundred photographs of largely one thing, so the ones that are
*not* of that thing stand out arithmetically. Each event's search vectors give a
centre, and a photograph's similarity to that centre says how much it belongs.
This is the machinery that groups faces, pointed at whole photographs.

**It needs no captions, and captions would be worse at it.** A caption is derived
*from* the vector, so comparing captions compares a lossy copy of what is
compared here directly — and finding one odd photograph among 379 by reading 379
sentences is not finding it. What a caption would add is the ability to say in
words *why* it is odd; locating it does not need them.

**How odd is odd.** Not a fixed similarity, for the same reason the search cut is
not one (§7.3): the scale differs per event. Measured over this library's 570
events of twelve photographs or more, the median similarity to the centre runs
from **0.71 to 0.86** depending on how varied the trip was, so one number would
flag everything in a varied event and nothing in a uniform one. The measure is a
photograph's distance below its *own* event's median, in units of that event's
median absolute deviation — the MAD rather than a standard deviation, because
the outliers being looked for would otherwise widen the very spread they are
measured against and hide behind it.

**Measured 2026-09-12.** At k = 4, **1,028 photographs across 373 events**, at
most six from any one; k = 2.5 gives 5.8 an event and starts flagging ordinary
variety. The whole computation is **0.3 s** over 39,854 vectors, so it is done on
request and stored nowhere. Two found by hand on the first look: a squat toilet
in an alpine hut among 379 photographs of Monte Rosa, and a wall of museum text
among 351 of Lanzarote.

**This is not a queue.** Most of these are good photographs that simply do not
look like the trip they came from, and the view says so. Three things can be
done with one: leave it, say **it belongs here** — kept against the photograph,
like `event_cuts`, because `derive` throws every event away — or open it and
correct the date that put it in the wrong event, which takes effect at the next
`derive`.

---

## 7. Search

One box. A person should be able to ask for *Ansku in Alicante in 2019 on a beach* without knowing
that four different mechanisms answer it, and without learning a syntax to say so.

### 7.1 One box, and what it understood

Each token of the query is resolved against what the database already knows — named people, place
names, event names, and date expressions — before anything reaches the encoder. What matched is shown
back as a row of **chips** above the results, and everything that matched nothing is the scene text:

> `Ansku ×` `Alicante ×` `2019 ×` — and *"beach"* as the description

This is the whole interface. It is preferred over the alternatives for specific reasons:

* **Against a field per aspect** — one box stays one box. A two-term question should not cost five
  tab stops, and most questions are two terms.
* **Against sigils** (`@Ansku`, `place:Alicante`) — nothing to memorise, and it does not require the
  user to know which *kind* of thing a word is. That is the application's job, not theirs.
* **Against parsing the query with a language model** — resolution is a table lookup: instant,
  deterministic, and identical every time. A model in the search box buys nothing here and costs
  latency and reproducibility on the control touched most often.

**When a word reads more than one way, weight of evidence decides — with one exception.** A person is
never outweighed: a name the user gave is an assertion, a gazetteer hit is a guess, and no number of
photographs makes the guess the better reading. Measured 2026-09-09, **22 of this library's 79 people
share a name with the gazetteer** — Marko, Helena, Reno, Anton, Kia, Luka among them — and several
hold very few pictures (Marko 4, Reno 15). This archive has driven the American west, so Reno, Nevada
could easily outnumber Reno the person, and counting would answer a question about a friend with a
question about a city.

An **event and a place** are weighed against each other, being the same kind of evidence: both are
what a folder label became. Where they are level the event stays on top, because a tie is not a
coincidence — it is the signature of a place derived from that very label, holding the same
photographs. Measured over every label in this library that reads two ways:

| Label | Event | Place | Taken as |
|---|---|---|---|
| West Coast | 1,200 | 624 | event — the place is New Zealand |
| USA | 256 | 8 | event |
| Alicante | 392 | 142 | event |
| Hartola | 3 | 1,286 | **place** |
| Tenerife | 0 | 162 | place |
| Kia / Loon / Vaja | 5 / 76 / 27 | 5 / 76 / 27 | event, on the tie |

Precedence alone read `Hartola` as an occasion of three photographs rather than as the municipality
of 1,286, so `hartola tommi ansku` returned nothing at all where the other reading returns twenty.

**An event name means every event of that name.** Names repeat: **50 of 168 named events share a name
with another** — three holidays called Alicante, two called West Coast, fifteen called TBD. Taking
whichever row the table yielded first made `Alicante` a search of one holiday out of three, silently,
and left its weight against the place a third of what it should be. The filter matches on the name
and the weight sums across the group; the chip links to a single event only where the name means one.

**One word of a name is a name.** Nobody types "Anne Raivisto" to look for Anne, so a person resolves
from any single word of their name as well as from the whole of it — but only where that word belongs
to exactly one person, since a chip that silently picks between two people is worse than no chip.
Where it is shared, the word resolves to nobody and the suggestion strip offers the candidates
instead. The whole name is always tried first, which is what makes `Reetta` resolve to Reetta rather
than to Reetta P. Measured 2026-09-09: of 79 named people, exactly one name-word is shared, and it is
a whole name as well, so the exact match settles it. A word shorter than three characters is not
indexed — otherwise "Reetta P" would make a stray `p` into a person.

**Ambiguity is the reason chips exist.** This library contains folder labels that are also people,
and place names that are also ordinary words — `Vaja`, `Kia`, `Loon` are all on record as wrong
matches (§6.3). A token that resolves two ways is shown as the reading the application chose, and
clicking it off picks the other. No other design makes a misreading *visible*, and a misreading the
user cannot see is one they cannot fix.

**Forcing a reading.** A prefix — `place:Vaja`, `person:Kia`, `text:Loon` — overrides resolution for
one token. This is an escape hatch for the ambiguous cases, not the way the box is meant to be used,
and it is never required.

**Offered, not applied.** The gazetteer holds 285,144 places and a great many of them are ordinary
words: `Sunset`, `Beach` and `On` are all towns carrying their own primary names. Nothing about the
*name* separates those from `Alicante` — only this library does, and where it can, the separation is
wide: measured 2026-09-09, Alicante holds 142 photographs, Helsinki 164 and Tenerife 162, while Beach
and On hold none. A place is therefore ranked by how much of *this* library sits beneath it rather
than by the gazetteer's own significance. Ranking the other way and then keeping the first candidate
with any photographs at all is worse than not filtering: for `sunset` it walks past the well-known
Sunsets and lands on the one obscure town the archive holds a single picture of.

One case survives that and cannot be settled by any rule over a name. `Sunset` resolves to Shandon,
California, where this archive genuinely holds **five** photographs — so the reading is not wrong,
merely unlikely, and somebody typing "sunset" almost always means the light. Rather than choose
silently, a place holding fewer than **25** photographs is *offered* beside the results instead of
being applied, and its words stay in the description until the offer is taken:

> "sunset" is also Shandon · California, where you have 5. Searching the words instead.

Below **5** it is not mentioned at all. An explicit `place:` prefix is never second-guessed. The
thresholds are calibrated against one library and are configuration, not architecture.

**Asking in a sentence** is a later layer, not part of this: once a language model is present for
captions (M5), a question in plain Finnish or English may *produce a chip set*, which the user then
edits exactly as if they had typed it. The chips remain the truth of what is being searched.

Each chip resolves by its own mechanism:

| Input | Mechanism |
|---|---|
| Free text (`kids on a beach at sunset`) | text→vector, cosine search over `vec_images` |
| Quoted or exact term (`"rippijuhlat"`) | FTS5 over captions, folder labels, event and place names |
| Person name | join through `faces.person_id` |
| Who else (`only`, `without Emmi`, `nobody`) | no other face / not that person's face / no face at all |
| Date expression (`kesä 2019`, `2019-06`) | range over `assets.taken_at` |
| Place name (`Helsinki`, `Finland`) | join through `places`, **including descendants** |

Where two or more mechanisms rank rather than filter, results merge by reciprocal rank fusion, so a
query naming both a person and a scene ranks photographs satisfying both above either alone.

**Place queries resolve down the tree.** Because `places` stores a materialised path
(§4.3), naming any level matches everything beneath it with one indexed comparison
and no recursion:

```sql
WHERE p.path = :place OR p.path LIKE :place || '/%'
```

So `Finland` returns Helsinki, Turku and everywhere else in the country, while
`Helsinki` returns only Helsinki. The user never has to know which level a name sits
at, and adding `Espoo` under Uusimaa later needs no change to existing rows.

**A city-state answers to its city's name.** The gazetteer files a city-state's districts as
*siblings* of a locality bearing the city's name, so nothing called Berlin is an ancestor of
Tiergarten. Measured 2026-09-09: this archive's 868 Berlin photographs sit under Mitte (297),
Tiergarten (240), Kreuzberg (64) and thirty more, while the locality called Berlin holds 40 — so
`Ansku in Berlin` found neither Tiergarten nor 95% of Berlin. An administrative area whose name
*wraps* a settlement — `State of Berlin`, `City of X` — therefore answers to the bare name as well.

Only leading wrappers count, and deliberately. A suffix names something larger than the place it is
named after: `Helsinki sub-region` reaches Espoo and Vantaa, `Berat County` is not the town of Berat,
`Provincia de Alicante` takes in Torrevieja. Folding those in would answer a question about a city
with a question about a region. Measured after the change: Berlin 868 rather than 40, while Helsinki
(164), Alicante and Tenerife are untouched.

### 7.2 Narrow, then rank

**Chips filter; the description ranks.** A query naming a known place and describing a scene —
`swimming pool in Tenerife` — splits: the place becomes a filter, the remainder ranks by vector
similarity *within it*. Place names are matched against `places.name` as whole tokens before the
query reaches the encoder, so ordinary searches are unaffected. If nothing resolves, the whole string
goes to the encoder as before.

**The order is not an optimisation, it is the correctness condition.** Vector search returns a global
top-*k*: ranking first and filtering afterwards would take the 120 best matches in the whole library
and then discard the ones outside Tenerife, commonly leaving nothing, and never leaving the *best*
matches in Tenerife. The candidate set must be narrowed by every chip before similarity is computed
over what remains. Where the narrowed set is small, this is also far cheaper than the global search.

### 7.3 Where the results stop

Two separate faults are corrected here: results are shown that plainly do not match, and results are
cut off at a fixed count with no way to see the rest.

**The cut is relative to the best match, not a threshold.** Cosine similarity is not calibrated
across queries — measured on this library, rank one scores 0.181 for *kids on a beach at sunset* and
0.132 for *a dog* — so one fixed cutoff over-trims some queries and under-trims others. What is
comparable within a query is how far a result has fallen behind that query's own best, so every
photograph scoring at least **90% of the top score** is an answer.

**The obvious rule was tried first, and the measurement overturned it.** It was to find the largest
fall between neighbouring scores and cut at the cliff, on the assumption that answers end at one.
They do not. Measured 2026-09-09, *snow* runs from 0.146 at rank 1 to 0.113 at rank 600, decaying
smoothly the whole way, and every query behaves alike: there is no gap anywhere to find, and the rule
cut nothing at all. It could be made to fire only by measuring each fall against its neighbour above,
which explodes wherever the scores pass through zero — putting the cut in the middle of the noise,
which is precisely where there is nothing to find.

**Measured at 90%, 2026-09-09.** *snow* 71 photographs, *birthday cake* 30, *rippijuhlat* 23, *a dog*
14, *underwater* 8 — the right shape for an archive that has seen a great deal of snow and very
little diving.

* Never fewer than **12** results, whatever the scores say. A near-empty page for a reasonable
  question reads as a broken application, not as an honest answer.
* Never a discard: everything below the cut stays one **show more** away, and the count is stated.
* A query resolving only to chips, with no description, has no scores and no cut — a filter is exact,
  and every photograph matching it is a result.

**Paging.** Results page at a fixed size with a **next page** control, and the total is always the
total, never the length of one page. The vector search takes `k = offset + limit`; at this library's
scale that is cheap, and the brute-force path computes every similarity regardless.

### 7.4 Filters

Independent of the query, and collapsed by default: date range, camera, date-source (useful for
auditing the 1998–2008 material), place-source (coordinates versus folder name). Person, place and
event are chips rather than filters, being what the box is for — and so are who else may be in the
photograph and a date picked from a list, below.

**Who else is in it.** The chips say who is in a photograph; one reading more says who else may be.
Ordinarily anybody may. **Only them** (`only`, `vain`, `just`) means no other face is in it, and
**nobody** (`nobody`, `no people`, `ei ihmisiä`) means no face was found at all. A name after
**without** (`ilman`, `not`, `ei`) leaves that person out. These replace a count of faces (*just two*)
and a has-faces switch, which asked the same questions less directly: *only Tommi and Ansku* is what
*just two* was used for, and it needs no number kept in step with the names. Measured 2026-09-13,
Tommi and Ansku are together in 919 photographs and the only people in 270 — the same 270 the face
count gave — and 21,600 of 39,884 photographs have nobody in them.

Two consequences are deliberate. A face nobody has named is somebody else, so a stranger in the
background takes a photograph out of *only them*; the alternative would count every unnamed face as
one of the people asked for. And it counts detected faces, so somebody turned away from the camera
is not among them. `only` means nothing without a name beside it — *only child* stays a description
— and `without` binds only to a name that follows it.

**Picked, and written as words.** Who else, and a date — a year from a list with its count, then a
month and a day only if wanted — can be chosen in the filter form, and choosing writes into the query
the words typing would have left: `2016-07`, `only`. A criterion therefore lives in one place, and a
picked one is the same chip as a typed one, removed the same way and carried by the same link. A
date the lists cannot show, `kesä 2016` or a range, is kept as typed when the form is used for
something else.

These are deliberately *not* chips. A chip answers "what am I looking for"; these answer "what do I
want to check", and most are auditing tools rather than searches — nobody types *place-source is a
folder name* as a question. They belong on a row that stays out of the way until wanted.

**Set, a filter is a chip all the same.** Found in use, 2026-09-13: folded away, nothing on the page
said which filters were on, and a result narrowed by a forgotten *just two* reads as an archive with
photographs missing. So a filter is set in its own place but shown in the one row with everything
typed, carrying the × that takes it back. The argument above is about where a filter is set, not
about whether it can be seen.

**Views:** grid, timeline by year and month, people, events, places (browsable as the
tree: country → region → locality, with counts).

### 7.5 Thumbnails

Generated on first access into a local cache directory — never the NAS — at
320 px and 1024 px, WebP, keyed by content hash. Browsing must never pull full-size files across
the network; a full-size original is fetched only when a person explicitly asks for one (§8.2).
Cache is disposable and rebuildable.

---

## 8. Interfaces

### 8.1 Command line

```
mediaidx add <path>...            Index exactly these folders. Recursive by default.
    --no-recurse                  Only the named folder
    --label TEXT                  Attach a label to these folders regardless of their names
    --person NAME                 These folders mostly depict NAME; seeds face-cluster naming
    --dry-run                     Report what would be indexed, touch nothing
    --workers N                   Override reader thread count

mediaidx scan [<path>...]         Re-scan for new, changed and missing files.
                                  With no argument, revisits every known root.

mediaidx roots                    List folders previously given to `add`
mediaidx roots forget <path>      Stop re-scanning it; indexed photos are retained

mediaidx embed                    Search vectors and thumbnails for what has none
    --limit N                     Stop after N photographs
    --workers N                   Reader thread count
    --batch N                     GPU batch size

mediaidx meta                     Re-read metadata whose stage version is behind the code.
                                  Headers only; nothing is hashed, no image is decoded
    --limit N                     Stop after N photographs

mediaidx caption <path>...        Run the slow captioning stage over named folders (M5)
    --model NAME                  Override the configured vision model
    --limit N                     Stop after N images
    --redo                        Re-caption assets that already have one

mediaidx faces                    Run stage 2 over photographs that have no faces yet
    --limit N                     Stop after N photographs
    --workers N                   Reader thread count
    --cpu                         Run without the GPU; correct, but ~7x slower

mediaidx faces cluster            Build or update face clusters
    --rebuild                     Recompute from scratch, preserving names

mediaidx places                   Put photographs on the map, by coordinate and by folder name
    --load                        Rebuild the gazetteer from the extract first
    --rejected                    List the folder-label matches thrown out by hand
    --limit N                     How many to list

mediaidx derive                   Rebuild places, events and the review queue
    --no-places                   Leave the places alone; rebuild only events and the queue

mediaidx people                   Who has been named, and how much of the library they cover

mediaidx events                   What the archive says happened, and when
    --unnamed                     Only the ones with no name
    --limit N                     How many to list

mediaidx review                   Open the review queue in the browser
    --no-open                     Print the address instead of opening a browser
mediaidx serve                    Start the local web UI  (default http://localhost:8710)

mediaidx search <phrase>          Search from the terminal
    --limit N                     How many results

mediaidx status                   Per-stage counts: done, pending, failed
    --conflicts                   Where EXIF and the filename disagree
    --errors                      Files that failed, and why
    --limit N                     How many to list

mediaidx doctor                   Check GPU, models, exiftool, NAS reachability, DB integrity
mediaidx export [people] [file]   Write out everything decided by hand, as JSON
mediaidx import [people] [file]   Read one back, into a rebuilt database if need be
mediaidx corrections              What has been put right by hand, and where it goes
    --export FILE                 Write just those out as JSON
```

`add` is idempotent and safe to re-run over the whole library at any time.

**The export is broader than its name.** `export people` was specified when names were the only
thing a person supplied. They are not any more: a correction (§4.7), a rejected place (§6.3), a cut
between two events, a photograph said to belong where it is (§6.5) — each is an answer somebody gave
and none can be recomputed, so all of it goes in the one file. A backup covering most of somebody's
work is one they cannot rely on.

**Including what was typed at `add` time.** A folder's `--label`, its `--person` hint, and the plain
fact that it was handed to `add` at all are all decisions a person made, and version 2 of the format
carries them. Only folders carrying one of those three appear; the rest of the tree is recomputed by
walking it. Restoring them into a fresh database inserts the rows as stubs if nothing has been
scanned yet, which means a rebuild is `import` followed by `scan` rather than thirty paths retyped,
and a later `add` fills in the parsed year and label without disturbing what was restored. Version 1
files remain readable: they simply carry no folders.

**Written to every configured destination.** `names_export_extra` (§9) names further copies, and one
`export` writes them all from a single dump, so each is byte-identical to the first rather than
merely close to it. A destination that cannot be written is reported on stderr and makes the command
exit non-zero even though the others succeeded — a NAS that was asleep otherwise looks exactly like
a backup that happened, which is the one way a backup fails silently for years. A path given on the
command line is a one-off and fans out to nothing.

Everything in it is keyed by content hash, never by a row id, because the file has to be readable by
a database built from nothing where every id differs or is absent. A face is found again by its index
within its photograph and, where a re-detection has reordered them, by where it sits in the frame —
the same anchor §5.2 uses.

**Taken without being asked, too.** `faces cluster` attaches thousands of faces to people in one
pass and no single action undoes it — only a full rebuild, or a correction at a time. So it writes a
dated copy of everything handmade beside the database first, keeping the last ten. That is an undo
buffer for the minutes afterwards; the archive is `names_export`, which is synced and versioned.

**Measured, 2026-09-12.** 94 people, 19,512 named faces, 7 event names and 103 corrections write out
as 3.9 MB in under a second. Restoring into a copy of the library with every name, person and
correction deleted brought back all 19,512 and all 94, with the per-person counts unchanged.

### 8.2 Web UI

Local FastAPI application, server-rendered HTML with minimal JavaScript. No build step, no SPA
framework — the UI is a small part of the work and should stay that way.

| Screen | Purpose |
|---|---|
| Search | Query box, a chip for every criterion typed or picked, filters behind one button, grid headed by month |
| Photo | Everything known about one photograph, and how to correct it |
| People | Person list, cluster review, merge and split |
| Events | Event list and timeline |
| Odd ones | The photograph in each series least like the rest (§6.5) |
| Review | The queue: one question at a time, largest first |
| Status | Indexing progress, per-stage counts, recent failures |

**Review screen requirements.** This screen determines whether the application is pleasant to use;
it deserves more design attention than the pipeline does.

- Shows a representative sample of the cluster (8–12 images), not one image.
- Displays date range, place, folder, and already-known people present.
- Shows the suggestion and its rationale, pre-filled into an editable field.
- Actions: **name all**, **not one event** (split), **skip**, **ignore permanently**.
- Fully keyboard-driven: type, Enter to accept, Esc to skip.
- Answering advances immediately to the next item; the count of remaining questions is visible.

**Photo screen requirements.** This is where the archive is checked against memory, so it must say
everything it knows and let a person put it right.

- **What matters sits beside the picture; how it is known is one click away.** The date and time,
  who is in it, where, the event and the camera are beside the photograph without scrolling. Where
  each came from, the corrections, the hash and the paths are under *Edit & details*. Asked for
  2026-09-13: the editing and the debugging read as the page, and what a person looks for was below
  the fold.
- **Every fact still names its source**, there. The place row already did — "from the coordinate"
  against "from the folder name" — and the rule extends to all of them, the date most of all: 1,294
  photographs carry a date conflict, and a date is not worth reading without knowing where it came
  from. A conflict is therefore also flagged under the date itself.
- **The way through the result is on the photograph**: arrows at its sides, brighter when pointed
  at, ← → on the keyboard and Esc back to the result; its position in the result; and a map pin
  where there is a coordinate, which still loads nothing until pressed (§10.4).
- Shows date, camera, dimensions, coordinates, place, event, caption (M5), people, source hint,
  conflict note, and every known path the file lives at.
- **Corrections in place** (§4.7): the date, the place, and the person on any one face, each beside
  the line naming its source under *Edit & details*, not on a separate screen. The section comes back
  open after a correction, so several can be made in a row.
- **Open questions about this photograph** link through to the review queue — a photograph belongs
  to an event and its faces to clusters, and any of them may be waiting on an answer.
- **The original**, at full resolution, behind an explicit link (see below), plus its path as
  copyable text.

**The full-size rule.** Browsing never pulls a full-size file across the network (§7.5); a deliberate
click is not browsing. The original is served by the application rather than linked as `file://`,
which browsers refuse to follow from a served page. An asset has several paths, so the route serves
the first that is reachable and says so plainly when none is.

**Downloads.** The same route serves the original as a download. A whole result set may be taken as a
zip, with a guard rail nothing else in the UI needs: it is the only feature that reads many originals
from the NAS on demand, over the link that is already the slowest thing here (§1.2). So the count and
total size are stated before it starts, the number of files is capped at **500**, and the archive is
streamed rather than assembled in memory.

The estimate costs nothing to produce: sizes are already in the index, so what a download will weigh
is known without touching a single file. Following the link therefore never begins a transfer — it
opens a page saying what the transfer would be, and a second click starts it.

Streaming falls out of the format. Given a stream it cannot seek in, `zipfile` writes each entry's
size and checksum *after* its data, in a descriptor; those descriptors are both what makes the
archive streamable and the evidence that nothing was built in memory first. Entries are stored, not
compressed, because a JPEG does not compress twice.

**Measured, 2026-09-09.** A 20-photograph result came back as 62 MB in 1.3 seconds — around 48 MB/s,
which is several times what §1.2's 100 Mbps assumption predicts, and worth re-measuring before that
number is relied on again. A copy that has gone missing is skipped rather than failing the archive,
and two files of one name in different folders both survive, the later numbered.

---

## 9. Configuration

TOML, at `~\.mediaidx\config.toml` — written there by `setup.ps1`, and found through the
`MEDIAIDX_CONFIG` user environment variable, which that script sets. `--config` overrides both.
§10.5 says why the runtime is in the profile root rather than `%LOCALAPPDATA%`; a process that
does not inherit `MEDIAIDX_CONFIG` reads a different file, so pass `--config` explicitly when it
matters whose configuration is being read.

```toml
[paths]
# Runtime data — MUST NOT be inside OneDrive or any syncing folder. See §10.5.
database   = "C:/Users/traiv/.mediaidx/photos.db"
thumbnails = "C:/Users/traiv/.mediaidx/thumbs"
logs       = "C:/Users/traiv/.mediaidx/logs"

# Irreplaceable, tiny, and worth backing up — so this one lives with the project.
names_export = "C:/Users/traiv/OneDrive/My Digital Solutions/Media classifier/names.json"

# The GeoNames extract behind place search (§6.3). Regenerable; reloaded with
# `mediaidx places --load`.
gazetteer  = "C:/Users/traiv/.mediaidx/gazetteer"

# Further copies of that same export, written by every `mediaidx export`.
# Storage with its own backup schedule belongs here. Nothing under a scan root
# does: P1 permits no write there, backup or not.
names_export_extra = [
  "//TRaDiskStation/Media/Backups/Kuva-arkisto/names.json",
]

# No roots are configured. Folders are indexed by passing them to `add`,
# and recorded from that; `mediaidx roots` lists what has accumulated.

[scan]
extensions     = [".jpg", ".jpeg", ".png", ".heic", ".heif"]
exclude_dirs   = ["_gsdata_", "@eaDir", "#recycle", "$RECYCLE.BIN", "System Volume Information"]
exclude_files  = ["Thumbs.db", ".DS_Store", "desktop.ini"]
reader_threads = 12

# Optional. Tried in order against each leaf folder name; named groups may be
# year, month, day, label. Delete the list entirely for a library with no
# naming convention — indexing works without it, with less metadata.
folder_patterns = [
  '^(?P<year>\d{4})(?:\s+(?P<month>\d{2}))?(?:\s+(?P<label>.+))?$',
]

# Filename date rescue, for files that carry no EXIF at all (WhatsApp and
# similar). Tried in order; see §5.1 for the full default list and the
# 1990-to-tomorrow sanity bound applied to every match.
# Shipped defaults describe how applications name files, and are the same on
# every machine: WhatsApp, Android, Pixel, screenshots, Telegram. They are
# listed in §5.1 and rarely need touching.
#
# Conventions belonging to THIS library go here instead, and are tried first.
# Adding your own never means restating the defaults.
filename_patterns_extra = [
  { re = '^(?P<ymd>\d{8})@(?P<hms>\d{6})(?:-(?P<make>.+?)-(?P<model>.+?))?-\d+$', hint = "archive" },
]
# folder_patterns_extra = ['^(?P<label>Trip .+)$']

[models]
face           = "buffalo_l"
embedding      = "ViT-L-14/laion2b_s32b_b82k"
caption        = "gemma4:12b"       # installed, vision-capable, fits 16 GB with headroom
caption_think  = false              # never let it reason before a one-line caption
ollama         = "http://localhost:11434"

[faces]
min_det_score      = 0.5
min_face_px        = 40
det_size           = 640            # SCRFD input; faces are filtered at 1600 px scale
face_thumbnails    = true           # crops written during the pass, for the review UI
cluster_min_size   = 4
# Two bounds, both in force (§6.1). Near enough is enough on its own; a little
# further is allowed only when the five nearest named faces name the same person.
assign_threshold   = 0.42           # cosine distance for incremental assignment
assign_agreed      = 0.50           # ...and how far it may reach when they agree

[events]
time_gap_hours     = 4
place_gap_km       = 25
min_event_assets   = 5              # below this a run is not an event at all
# ...and these are the floors for being *asked* about, which is a different
# question. See 6.4: five would put 1,785 questions in the queue.
queue_min_assets   = 20
queue_min_faces    = 10

[places]
# A folder label matching a place's own name is accepted whatever its size; one
# matching only an exonym must clear this, or `Nina` resolves to Neenah,
# Wisconsin (§6.3).
exonym_min_population = 50000

[tools]
exiftool = ""                       # empty means: find it on PATH
```

Note what is absent: no host, share or library path, and no person-to-folder mapping. Person
hints are given per invocation with `add --person`, and the set of indexed folders is whatever
has been passed to `add`. A fresh install with this config indexes nothing until told what to
index (P7).

---

## 10. Non-functional requirements

### 10.1 Performance targets

Revised against measurement (§1.2). The network-bound rows are given at both the current link
speed and the speed the hardware is capable of, because the difference is the dominant fact about
this system's runtime.

| Operation | At 100 Mbps (now) | At 1 GbE | Measured |
|---|---|---|---|
| Stage 1 over 1,337 files (`2019`) | 7.2 min | ~45 s | **434 s, 10.6 MB/s** |
| Full fast-path index, cold, 232 GB | **~5.5 h** | ~35 min | extrapolated |
| Re-scan with no changes | ≤ 5 min | ≤ 5 min | stat-only, no reads |
| Captioning throughput | ≥ 25 images/min | — | GPU-bound, unaffected |
| Search query response | ≤ 300 ms at 46k assets | — | not yet built |
| Thumbnail grid, 100 cached items | ≤ 500 ms | — | local cache, unaffected |

A full first pass is an overnight job either way, which the design already assumes. But the gap is
large enough that fixing the link before the first full run is worth an evening's attention.

### 10.2 Reliability

- A run interrupted at any point resumes with no repeated and no lost work (P3).
- A single unreadable, truncated or malformed file never aborts a run. It is recorded with its
  error in `stage_state` and reported by `mediaidx status`.
- A file failing the same stage three times is marked `skipped` and excluded from further attempts
  until explicitly retried.
- The database is opened in WAL mode so the UI remains readable and responsive during indexing.
- `mediaidx doctor` runs `PRAGMA integrity_check` and reports orphaned rows.

### 10.3 Windows and NAS specifics

- **Paths are stored as UNC** (`\\TRaDiskStation\Media\Pictures\...`), never as `Z:\`. Drive
  letters are reassigned; the index must not care. A mapped-drive path given on the command line
  is resolved to UNC before storage.
- **Long paths.** UNC plus deep folders will exceed 260 characters. All file access uses the
  `\\?\UNC\` extended-length prefix.
- **Unicode.** Finnish characters (`ä`, `ö`, `Å`) appear throughout folder names. All path handling
  is UTF-8 end to end; the FTS tokeniser is `unicode61`.
- **Offline NAS.** If a root is unreachable, the run exits cleanly with a clear message rather
  than marking thousands of files missing.
- **Missing files are never deleted from the index.** `missing_since` is set, and the UI shows
  them as unavailable — protecting against a half-mounted share wiping useful history.

### 10.4 Privacy

**No photograph, thumbnail, filename, embedding, caption or item of metadata is
ever transmitted anywhere by the application.** All inference runs on the local GPU. This is the
point of the system.

**One qualification, added deliberately and visibly.** The photograph page can show a map, because
half this library carries a coordinate and a coordinate on its own tells you nothing.

**The deciding property is *when* anything is fetched, not where it is drawn.** A photograph opened
normally reaches nowhere at all: the page carries no remote image, script, stylesheet or frame, and a
test asserts every one of those. A map frame is written into the HTML only on the request that asks
for one — `?map=1`, reached by clicking *see it on a map* — and that click puts that one coordinate
in the map service's logs. The link says so where it sits, rather than leaving it to be discovered.

Drawing the map *in the page* rather than in another tab was chosen once the timing was settled,
because it costs nothing further and saves the reader's place: the photograph is still there behind
the map, and going back is a link rather than a browser button. `[ui] map_url` and
`[ui] map_embed_url` point both elsewhere; the frame is told `referrerpolicy="no-referrer"`, so the
map learns the coordinate and not where it came from.

This is §13's option 1 with its rendering changed, and specifically **not** option 2: embedded tiles
loaded with the photograph would send a coordinate every time one was opened, wanted or not, and
would make the paragraph above false as written.

Network access is limited to two things, and both are one-time downloads of software:

| Destination | When | What moves |
|---|---|---|
| PyPI, download.pytorch.org | setup | packages, inbound |
| huggingface.co | first `embed` run | model weights, ~1.7 GB inbound |
| insightface's CDN | first `faces` run | `buffalo_l` weights, ~280 MB inbound |
| `localhost` | captioning (M5) | images to Ollama, in-process |
| The NAS | indexing | photographs are read, never written |

An earlier draft claimed "no network egress except to localhost and the NAS", which
was wrong: model weights are fetched from Hugging Face on first use. Nothing about a
photograph is sent in either direction, but the claim as written was inaccurate.

**Enforceable, not merely promised.** Once the weights are cached, setting
`HF_HUB_OFFLINE=1` makes the library incapable of any network call — a later
dependency change cannot quietly reintroduce one. `mediaidx doctor` reports whether
offline mode is active.

- The UI binds to `127.0.0.1` by default. Binding to the LAN is opt-in via config.
- No telemetry, no analytics, no crash reporting, no account.

### 10.5 Where the application's own files live

The project folder is inside OneDrive
(`C:\Users\traiv\OneDrive\My Digital Solutions\Media classifier`), which makes it right for some
of these files and actively dangerous for others.

| File | Location | Why |
|---|---|---|
| Source, `spec.md` | Project folder (OneDrive) | Small, versionable, worth syncing |
| `names.json` export | Project folder (OneDrive) | The only irreplaceable data (P6, P8). Backed up for free, and versioned |
| `config.toml` | `~\.mediaidx\` | Machine-specific: it names paths on *this* machine, so it does not travel with the source. Written by `setup.ps1`, found through `MEDIAIDX_CONFIG` |
| `photos.db` | `~\.mediaidx\` | **Never in OneDrive** |
| Thumbnail cache | `~\.mediaidx\` | 1.5 GB of regenerable files; syncing it is pure waste |
| Logs | `~\.mediaidx\` | Churns constantly |
| Pre-run snapshots of the handwork | `~\.mediaidx\backups\` | Written before anything that names thousands of faces at once; last ten kept. They undo a bad run, not a dead disk — `names.json` is the backup |

**And the profile root rather than `%LOCALAPPDATA%`,** which would be conventional. Two reasons,
both found the hard way. The Claude desktop app is an MSIX package, so processes it launches have
their `AppData\Local` writes redirected into a package-private store under
`%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Local\`, where the files are invisible to Explorer
and to the user's own shells — a silent, thoroughly confusing failure. And `C:\mediaidx`, which an
earlier version used, is worse still: root-level folders grant `Authenticated Users` Modify, and
this index holds face embeddings and the names of a family. The profile root is neither
virtualised nor world-readable.

**A SQLite database must not live in a syncing folder.** Three independent failure modes: OneDrive
can lock or replace a file mid-transaction, corrupting the database; WAL and shared-memory
sidecar files (`-wal`, `-shm`) are meaningless once synced and actively harmful if restored out of
step with the main file; and Files On-Demand can dehydrate the database to a cloud placeholder,
so an indexing run stalls or fails on a file it holds open.

The split above still delivers what matters: everything you would grieve losing is in OneDrive and
backed up, and everything large or rebuildable is not. C: has 252 GB free, so the ~2 GB of local
data is inconsequential. `mediaidx doctor` warns if the configured database path resolves inside a
known sync root.

---

## 11. Implementation notes

### 11.1 Environment

**Python 3.11 or 3.12 in a dedicated virtual environment.** The system interpreter is 3.14.3;
`onnxruntime-gpu`, `insightface` and the PyTorch stack are unlikely to have wheels for it, and
building them from source on Windows is not a reasonable prerequisite. This should be settled
before any other work.

External dependencies:

| Dependency | Purpose | Note |
|---|---|---|
| `exiftool` | metadata extraction | external binary, bundled or on PATH |
| `onnxruntime-gpu` | face models | see the two traps below |
| `insightface` | detection + recognition | wheels exist for 3.12; no compiler needed |
| `pillow-heif` | HEIC decoding | 4,452 files depend on it |
| `sqlite-vec` | vector search | loadable SQLite extension |
| Ollama | captioning | installed; `gemma4:12b` already present and vision-capable |

**Two ways the face stage silently loses its GPU.** Both were hit during M3, both fail the
same way — no error, no warning, just a pass running roughly seven times slower than it
should. Neither is discoverable from the output, which is why `doctor` now builds a real
inference session and reports the provider it actually got, rather than the providers
onnxruntime merely lists.

1. **`insightface` depends on `onnxruntime`, the CPU build.** It installs into the same
   import name and over the same files as `onnxruntime-gpu`; whichever lands last wins, and
   the loser leaves no trace of having been replaced. Resolved with a `[tool.uv]`
   `override-dependencies` entry dropping the CPU requirement — `onnxruntime-gpu` satisfies
   every import insightface makes.
2. **`onnxruntime-gpu` 1.29 is built against CUDA 13; the PyTorch here carries CUDA 12.8.**
   Left alone, onnxruntime searches PyTorch's lib directory first, fails to resolve
   `cublasLt64_13.dll`, and falls back to the CPU. The fix is `ort.preload_dlls(directory="")`
   before the first session, which selects the `nvidia-*` CUDA 13 wheels that
   `onnxruntime-gpu[cuda,cudnn]` installs. Those extras are not optional in practice, and
   the two CUDA runtimes coexist without conflict — the DLL names differ by major version.

### 11.2 Testing

A fixture set of ~60 images, committed to the repository, covering:

- JPEG with full EXIF and GPS; JPEG with no EXIF at all
- `IMG-20190608-WA0012.jpg` with no EXIF — filename is the only date available
- A filename containing a date-like number that is not a date (`IMG_99999999_001.jpg`), which must
  fall through to the next rule rather than produce a nonsense date
- HEIC from an iPhone; PNG screenshot
- Truncated and zero-byte files
- EXIF orientation values 1 through 8
- Byte-identical duplicates at two paths
- Finnish characters in folder and file names
- A path exceeding 260 characters
- Images with 0, 1, 2 and 12+ faces

Required tests:

- **Read-only enforcement:** the scan-root filesystem is asserted byte-identical before and after
  a full indexing run (P1).
- **Resumability:** a run killed mid-stage, then resumed, produces a database identical to an
  uninterrupted run (P3).
- **Idempotency:** `add` run twice performs no work the second time.
- **Scope containment:** `add` on a subfolder indexes nothing outside that subtree — not its
  parent, not its siblings — and a junction pointing out of the subtree is not followed (P7).
- **Convention independence:** a fixture library with no recognisable folder naming, and an empty
  `folder_patterns`, indexes completely and searches correctly.
- **Date resolution:** each rule in §5.1 fires on the right fixture, with the right precision.
- **Deduplication:** two paths, one asset, one set of stage results.

### 11.3 Delivery phases

| Phase | Scope | Result |
|---|---|---|
| **M1** | Scan, hash, EXIF, folder parse, schema, `status`, `doctor` | Whole library catalogued and browsable by date, folder, camera. ~35 min to run. |
| **M2** | Stage 3, thumbnails, search UI | **Free-text search over everything.** The first genuinely useful build. |
| **M3a** | Stage 2: detection, embeddings, face crops | Every face in the library found and vectorised. **The only part of M3 that reads files** — the long pass. |
| **M3b** | Clustering, people UI, naming | Faces named, searchable by person. **Database and local crops only; no NAS access, so it is re-runnable whenever a threshold changes.** |
| **M4a** | Places: gazetteer, geocoding, hierarchy, UI, search | Location shown and searchable at any level. **Database-only — no file access; 22 s to load the gazetteer, 11 s to place the library.** |
| **M4b** | Events, review queue, review UI | Unattended indexing plus a finite question queue. **Database-only; the whole `derive` is 2.3 s. 1,374 events over 86% of the library, and 314 questions covering 7,726 photographs.** |
| **M4c** | Unified search (§7), the photo screen, corrections (§4.7), and the identity re-attachment P8 requires of stage 2 (§5.2) | One box answering across people, places, dates and scene; every photograph correctable, and no handmade answer lost to a re-run. **Database-only.** |
| **M5** | Stage 4 captions | Descriptions and tags. |
| **M6** | Video | See §12. |

M1 and M2 together are the recommended first slice: a couple of hours of indexing and free-text
search works across all 46,423 images, with no face or caption work done at all.

**M4c comes before M5, deliberately.** The queue is not a backlog that empties — new photographs
arrive, `derive` re-runs, and skipped questions return by design (§6.4) — so the interface for
answering it is a tool in permanent use, and every improvement to it is repaid on every future
question. Captions then make the queue *busier*, being its third suggestion source (§6.4), and M5 is
the long pass; the tool should be worth using before the volume goes up. M4c needs no file access and
no FTS: chips resolve names already in the database, and FTS adds the exact-term mechanism to §7.1
whenever it is built.

---

## 12. Deferred: video

2,030 files, about 4% of the archive, but a disproportionate share of the moments worth finding.
The intended approach when it is taken up:

- Extract container metadata (creation time, GPS where present, duration, codec) — same treatment
  as stage 1.
- Sample keyframes (one every N seconds, plus first, middle and last), then run stages 2 and 3
  over those frames unchanged.
- Videos then appear in search results and events alongside photographs, with matched frames as
  the preview.
- Audio transcription and temporal captioning are out of scope for that phase too, though worth
  noting that `gemma4:12b` reports an `audio` capability — so if video is ever taken up, the
  captioning runtime already in place can handle a clip's soundtrack without new infrastructure.

The schema should accommodate this without migration: `assets.format` already carries the type,
and a `frames` table linking sampled frames to a parent asset is the only structural addition.

### 12.1 Access from a phone

Also deferred. The UI is a local web page, so serving it to the LAN is a config change
(§10.4) rather than construction work. What it would additionally need is a responsive layout and
some thought about authentication, neither of which should shape the v1 design. Until then the UI
is desktop-only and bound to `127.0.0.1`.

---

## 13. Open questions

None blocking.

### Open

- **A map on the photograph page.** Asked for 2026-09-09 and **resolved the same day** (§10.4):
  option 1's timing — nothing fetched until asked — with option 2's rendering, the map drawn in the
  page rather than in another tab. The two turned out to be separable, and only the timing was ever
  the privacy question. What follows is the reasoning as it stood when it was decided.

- ~~**A map on the photograph page.**~~ It would show where a
  photograph was taken, and it would apply to the **20,427 of 39,884** that carry a coordinate —
  just over half the library. The data is already there, and §4.3 stores a representative `lat`/`lon`
  on every place against exactly this.

  What makes it a question rather than a task is §10.4, which says no item of metadata is ever
  transmitted anywhere, *without qualification*. A coordinate is metadata, and it is the most
  sensitive item in the index: taken together these points are where this family lives, where the
  children went to school, and when the house was empty. Any map drawn by somebody else's service
  means sending it to them. Four ways to go, cheapest first:

  1. **Link out, on a click.** Nothing is sent until the user asks for it, one photograph at a time,
     and the choice of service is theirs. §10.4 stays true of the application and gains a precise
     sentence about a link the user may follow.
  2. **Embedded tiles.** A coordinate goes out every time a photograph is opened, wanted or not.
     This would make §10.4 false as written, and the section already records one earlier claim that
     had to be corrected for less than this.
  3. **Offline tiles.** Keeps the principle whole. A world tileset is far too large, but a bounded
     extract covering the regions this archive actually visits is tractable — and is a project in
     its own right, not a feature of the photograph page.
  4. **No map.** The coordinate is shown and copyable, which is what happens today.

  Recommendation is (1): it is a few lines, it keeps the decision with the person looking at the
  photograph, and it is the only option whose privacy cost is visible at the moment it is paid.
  Whichever is chosen, §10.4 is amended in the same commit rather than quietly outgrown.

### Resolved

- **Vision model** — start with `gemma4:12b` and change only if its captions disappoint (§5.4).
  It is installed, vision-capable, and the largest of the three that fits in VRAM with headroom.
  `qwen3.5:9b` is the first fallback; `qwen3.5:27b` is reserved for small, important folders where
  its speed penalty does not matter.
- **Storage location** — split (§10.5). Source, config and the names export sit in the project
  folder and sync; the database, thumbnails and logs go to `%LOCALAPPDATA%\mediaidx\` and must not.
- **Phone access** — deferred (§12.1). Does not shape the v1 design.
- **Photos with no metadata at all** — filename patterns become date rule 2, ahead of the folder
  name (§5.1). WhatsApp images, which carry no EXIF whatsoever, are dated from
  `IMG-20190608-WA0012.jpg` and tagged with a source hint.

- **Which folders get processed** — only those passed to `add`, never inferred (P7). This removes
  the earlier question about how to treat `Scans` and `Specials`: they are indexed if and when
  they are handed over, and otherwise do not exist as far as the application is concerned.
- **Person-folder genitive mapping** — removed rather than answered. The application no longer
  reads person names out of folder names; `add --person` states the name explicitly, so
  `Anskun → Anski` never needs inverting.
- **Whether a name may be typed inflected** — no. Decided 2026-09-09: a name, a place and an event
  are matched exactly, and Finnish case endings are not stripped. `Anskun` does not find Ansku.

  What *is* still exact, and stays: case and diacritics fold, so `AAKKONEN` and `aakkonen` both find
  Ääkkönen; one word of a name resolves where it names only one person; and an administrative area
  answers to the settlement it wraps. Each of those is an exact match on a normalised form or on a
  whole name-word. Stripping an inflection is not — it is a guess about morphology, and a cheap one
  goes wrong immediately: accepting up to three trailing letters would make a person called `Kai`
  match `kaikki`, an ordinary Finnish word.

  **The cost is that being one letter wrong is silent.** The suggestion strip looks for the query
  *inside* a name, so a longer query matches nothing at all: `Anskun` produces no chip, no
  suggestion and no explanation, and the word goes to the encoder as a description. Exactness is a
  reasonable rule only if the interface says when a word came close, so the strip should also match
  the other way round — a suggestion, not a fuzzy resolution, and the chips stay exact.

- **How mixed criteria are entered** — resolved chips (§7.1), over a field per aspect, a sigil
  syntax, or a language model parsing the query. Chips need nothing learned and are the only one
  of the four that makes a *misreading* visible and correctable, which this library needs: it
  contains folder labels that are also people and place names that are also ordinary words.
- **How a correction reaches the derived tables** — consulted at each point the value is computed,
  not applied as a pass afterwards (§4.7). A single pass is tidier and is wrong the moment a
  subcommand recomputes a column on its own, which `mediaidx places` does and `mediaidx meta`
  recommends. This generalised into P8.
- **Correcting a wrongly identified face** — in scope, as a `person` correction (§4.7). It was
  nearly deferred as hard, and is not: the write is one row. What makes it non-trivial is that a
  rebuild would vote it away, which the same durable-key mechanism as `date` and `place`
  corrections solves. Teaching the clusterer from corrections — constrained clustering — stays
  out of scope; a correction is re-applied over each rebuild rather than changing it.
