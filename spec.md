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
Deleting the database and rebuilding must lose nothing except user-supplied names, which are
exportable and re-importable.

**P7 — The application knows nothing about any particular drive.**
It indexes exactly the folders it is given, and no others. No host, share, folder name or naming
convention is hardcoded, and none is discovered by exploring around a given path. Everything
specific to a library — where it lives, how its folders are named, which folders belong to a
person — arrives as a command argument or a config entry supplied by the user. Pointing the
application at a second NAS, a local drive or an external disk requires no code change, and a
library that follows no convention at all still indexes correctly, just with less metadata.

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
  id        INTEGER PRIMARY KEY,
  parent_id INTEGER REFERENCES places(id),
  kind      TEXT NOT NULL,          -- country | admin1 | admin2 | locality
  name      TEXT NOT NULL,          -- "Helsinki"
  path      TEXT NOT NULL UNIQUE,   -- "Finland/Uusimaa/Helsinki"
  lat       REAL,                   -- representative point, for a map later
  lon       REAL,
  UNIQUE (parent_id, name)
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
  name_source TEXT,         -- folder | user | auto
  start_at    INTEGER NOT NULL,
  end_at      INTEGER NOT NULL,
  place_id    INTEGER REFERENCES places(id),
  asset_count INTEGER NOT NULL
);

CREATE TABLE event_assets (
  event_id INTEGER NOT NULL REFERENCES events(id),
  asset_id INTEGER NOT NULL REFERENCES assets(id),
  PRIMARY KEY (event_id, asset_id)
);
```

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
  status      TEXT NOT NULL,     -- open | answered | skipped | dismissed
  skipped_at  INTEGER,           -- skipped items return after the next derive run
  answered_at INTEGER
);
```

### 4.7 Storage budget

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
before lookup so results are cached in `places` and repeated lookups cost nothing.

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
- **Expected volume:** roughly 60,000–80,000 faces across the archive. The sample above
  suggests closer to **1.0 kept faces per photograph, so ~40,000** — lower than estimated,
  and to be confirmed by the full pass.

**Measured, 2026-09-07, RTX 4080 SUPER:** 22 images/second on the GPU, against ~3.2 delivered
by the link. As with stage 3, the model is several times faster than the pipeline can feed it,
so inference runs on a single thread and reader threads are the only lever that matters.

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
3. **Incremental assignment:** new faces from later runs are matched against existing cluster
   centroids by cosine similarity. Above the assign threshold they join; below it they accumulate
   as unclustered and are re-clustered when enough have gathered.
4. **Full rebuild:** `mediaidx faces cluster --rebuild` recomputes from scratch, preserving
   `person` assignments by majority vote of each old cluster's members. Cheap, because embeddings
   were kept (P5).

**Measured, 2026-09-07.** 8,786 real face embeddings clustered in 59 s, producing 153 clusters
covering 70% of faces; the remaining 30% stayed noise. Cost grows with the square of the face
count, so the full library at ~40,000 faces is roughly 20 minutes -- a one-off, and re-runnable
whenever a threshold changes. Random unit vectors, the worst case for the tree search, take
about twice as long as real embeddings at the same count, which is the right direction: real
faces cluster, and clustered data prunes better.

**HDBSCAN needs contrast.** A dataset containing exactly one group is labelled noise in its
entirety, because there is nothing for the density estimate to stand against. This never arises
on a real library and is entirely an issue for tests, which must seed several groups.

**Merging is a first-class operation.** A person photographed across 28 years will produce several
clusters — children especially, whose embeddings drift substantially with age. The review UI must
make merging two clusters a single action, and the data model must allow many clusters per person.

### 6.2 Event detection

Assets are grouped by:

1. **Folder first.** A folder with a parsed label (`2019 07 Alicante`) is authoritative — it
   becomes an event with `name_source = folder`, and no question is queued.
2. **Time and place otherwise.** Within a bare month folder, assets are split at gaps exceeding
   4 hours, then further split when GPS positions separate by more than 25 km. Both thresholds are
   configurable.
3. **Minimum size.** Groups below 5 assets are not promoted to events and never enter the queue.

Assets without GPS group on time and folder alone. Measured availability (§1.1) makes the split
sharp: **no GPS at all before 2010**, patchy through 2015, near-universal from 2016. So place-based
grouping is effectively a feature of the last decade, and the first twelve years of the archive
rely entirely on time gaps and folder labels — which the 95% filename-date coverage makes reliable.

### 6.3 Places (M4a)

Runs entirely over the database. No file is read, no model runs, no network is
touched. A full pass over 46,000 assets takes about a minute and can be repeated
whenever the gazetteer or the matching rules change — which is why this milestone
is scheduled independently of the expensive indexing passes.

**Gazetteer.** A bundled GeoNames extract (`cities500` plus admin and country
tables, ~50 MB) is loaded into `places` as a tree on first run. Alternate names are
retained, which matters here: `Thaimaa` and `Lontoo` are how this library's owner
names Thailand and London.

**Source 1 — coordinates.** GPS is binned to ~1 km and looked up against the
gazetteer by nearest neighbour, then cached in `geo_cache`. `place_source = 'gps'`.

**Source 2 — folder labels.** A parsed folder label (§5.1) is matched against
gazetteer names as a whole-token, case- and diacritic-insensitive comparison.
`2019 07 Alicante` resolves to Alicante, Spain; `2001 01 Thaimaa` to Thailand.
`place_source = 'folder'`.

This is what extends place search to the first two decades of the archive, which
have no GPS at all (§1.1). It is also the riskier source, so it is constrained:

- Only labels matching a gazetteer entry **in full** are accepted; no substring or
  fuzzy matching. `Hennan rippijuhlat` matches nothing and is left alone.
- Where a name is ambiguous (`Alicante` is both a city and a province) the more
  populous entry wins, and the choice is recorded so it can be reviewed.
- A folder-derived place is **coarse by construction**: no coordinates are invented,
  and the UI labels it as coming from the folder name.
- Any match can be rejected, which records a permanent exception.

**Precedence.** GPS always wins over a folder label for the same asset. A folder
label never overrides a coordinate, because the folder says where photographs were
filed and the coordinate says where one was taken.

**Expected coverage**, from the measured sample (§1.1):

| Source | Share of library | Era |
|---|---|---|
| GPS | ~47% | 2010 onward, ~95% from 2016 |
| Folder label | unmeasured; most named event folders | all years |
| Neither | remainder | bare month folders before 2010 |

### 6.4 Review queue construction

For each unnamed event and unnamed face cluster, a queue row is created with `priority` set to
the number of assets affected — **the queue is always ordered largest-first**, so the earliest
questions cover the most photographs.

**Suggestions.** Where captions and faces exist, the application proposes an answer and states its
rationale:

> Captions mention white robes, a decorated cake and a formal gathering.
> Suggested: *Sini's confirmation party*

Correcting a proposal is far less work than composing an answer, which is the point.

**Skipping.** A skipped item returns to the queue after the next derive run. Questions get easier
as surrounding events and faces acquire names; "some party in Turku" on the first pass is often
obvious on the second.

**Expected volume:** roughly 400–600 real events across the archive, most already named by their
folder. Perhaps 150–250 genuine questions reach the queue.

---

## 7. Search

A single query box accepting mixed criteria, resolved as follows:

| Input | Mechanism |
|---|---|
| Free text (`kids on a beach at sunset`) | text→vector, cosine search over `vec_images` |
| Quoted or exact term (`"rippijuhlat"`) | FTS5 over captions, folder labels, event and place names |
| Person name | join through `faces.person_id` |
| Date expression (`kesä 2019`, `2019-06`) | range over `assets.taken_at` |
| Place name (`Helsinki`, `Finland`) | join through `places`, **including descendants** |

Results merge by reciprocal rank fusion across the mechanisms that matched, so a query naming both
a person and a scene ranks photographs satisfying both above either alone.

**Place queries resolve down the tree.** Because `places` stores a materialised path
(§4.3), naming any level matches everything beneath it with one indexed comparison
and no recursion:

```sql
WHERE p.path = :place OR p.path LIKE :place || '/%'
```

So `Finland` returns Helsinki, Turku and everywhere else in the country, while
`Helsinki` returns only Helsinki. The user never has to know which level a name sits
at, and adding `Espoo` under Uusimaa later needs no change to existing rows.

**Mixed place and scene queries.** A query naming a known place and describing a
scene — `swimming pool in Tenerife` — splits: the place term becomes a filter, the
remainder ranks by vector similarity within it. Place names are matched against
`places.name` as whole tokens before the query reaches the encoder, so ordinary
searches are unaffected. If no place matches, the whole string goes to the encoder
as before.

**Filters** (independent of the query): date range, person, place, event, camera, has-caption,
has-faces, date-source (useful for auditing the 1998–2008 material), place-source
(coordinates versus folder name).

**Views:** grid, timeline by year and month, people, events, places (browsable as the
tree: country → region → locality, with counts).

**Thumbnails.** Generated on first access into a local cache directory — never the NAS — at
320 px and 1024 px, WebP, keyed by content hash. Browsing must never pull full-size files across
the network. Cache is disposable and rebuildable.

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

mediaidx caption <path>...        Run the slow captioning stage over named folders
    --model NAME                  Override the configured vision model
    --limit N                     Stop after N images
    --redo                        Re-caption assets that already have one

mediaidx faces                    Run stage 2 over photographs that have no faces yet
    --limit N                     Stop after N photographs
    --workers N                   Reader thread count
    --cpu                         Run without the GPU; correct, but ~7x slower

mediaidx faces cluster            Build or update face clusters
    --rebuild                     Recompute from scratch, preserving names

mediaidx derive                   Rebuild events, places and the review queue

mediaidx review                   Open the review queue in the browser
mediaidx serve                    Start the local web UI  (default http://localhost:8710)

mediaidx status                   Per-stage counts: done, pending, failed
mediaidx doctor                   Check GPU, models, exiftool, NAS reachability, DB integrity
mediaidx export people <file>     Export person and event names as JSON
mediaidx import people <file>     Restore them into a rebuilt database
```

`add` is idempotent and safe to re-run over the whole library at any time.

### 8.2 Web UI

Local FastAPI application, server-rendered HTML with minimal JavaScript. No build step, no SPA
framework — the UI is a small part of the work and should stay that way.

| Screen | Purpose |
|---|---|
| Search | Query box, filters, result grid |
| Photo | Full view, metadata, faces, caption, event, all known paths |
| People | Person list, cluster review, merge and split |
| Events | Event list and timeline |
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

---

## 9. Configuration

TOML, at `%APPDATA%\mediaidx\config.toml`, overridable with `--config`.

```toml
[paths]
# Runtime data — MUST NOT be inside OneDrive or any syncing folder. See §10.5.
database   = "C:/Users/traiv/AppData/Local/mediaidx/photos.db"
thumbnails = "C:/Users/traiv/AppData/Local/mediaidx/thumbs"
logs       = "C:/Users/traiv/AppData/Local/mediaidx/logs"

# Irreplaceable, tiny, and worth backing up — so this one lives with the project.
names_export = "C:/Users/traiv/OneDrive/My Digital Solutions/Media classifier/names.json"

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
assign_threshold   = 0.42           # cosine distance for incremental assignment

[events]
time_gap_hours     = 4
place_gap_km       = 25
min_event_assets   = 5
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
ever transmitted anywhere.** All inference runs on the local GPU. This is the point of
the system, and it holds without qualification.

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
| Source, `spec.md`, `config.toml` | Project folder (OneDrive) | Small, versionable, worth syncing |
| `names.json` export | Project folder (OneDrive) | The only irreplaceable data (P6). Backed up for free |
| `photos.db` | `%LOCALAPPDATA%\mediaidx\` | **Never in OneDrive** |
| Thumbnail cache | `%LOCALAPPDATA%\mediaidx\` | 1.5 GB of regenerable files; syncing it is pure waste |
| Logs | `%LOCALAPPDATA%\mediaidx\` | Churns constantly |

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
| **M4a** | Places: gazetteer, geocoding, hierarchy, UI, search | Location shown and searchable at any level. **Database-only — no file access, ~1 min over the whole library.** |
| **M4b** | Events, review queue | Unattended indexing plus a finite question queue. |
| **M5** | Stage 4 captions | Descriptions and tags. |
| **M6** | Video | See §12. |

M1 and M2 together are the recommended first slice: a couple of hours of indexing and free-text
search works across all 46,423 images, with no face or caption work done at all.

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

None blocking. Work can begin on M1.

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
