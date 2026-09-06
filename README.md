# Kuva-arkisto

A local, private index over a home photo archive. Faces, scenes, places and events
made searchable, with nothing leaving the machine and no original file ever touched.

Full design: [spec.md](spec.md).

## Status

**M1 complete** — identity, metadata, and date resolution.

| Milestone | Scope | State |
|---|---|---|
| M1 | Scan, hash, EXIF, filename/folder dates, schema, `status`, `doctor` | **done** |
| M2 | Search vectors, thumbnails, search UI | next |
| M3 | Faces, clustering, people UI | |
| M4 | Events, places, review queue | |
| M5 | Captions | |
| M6 | Video | deferred |

## Setup

The environment lives outside OneDrive, because a SQLite WAL database and a sync
client eventually corrupt each other (spec §10.5).

```
uv python install 3.12
uv venv "C:\Users\traiv\AppData\Local\mediaidx\venv" --python 3.12
uv pip install --python "C:\Users\traiv\AppData\Local\mediaidx\venv\Scripts\python.exe" -e ".[dev]"
winget install OliverBetz.ExifTool
```

Config is read from `%LOCALAPPDATA%\mediaidx\config.toml`.

## Use

```
mediaidx doctor                       check the environment first
mediaidx add <folder>                 index exactly this folder, and no other
mediaidx add <folder> --dry-run       walk it and report, reading nothing
mediaidx add <folder> --person NAME   these are mostly of NAME; seeds face naming
mediaidx add <folder> --label TEXT    label folders whose names carry nothing
mediaidx scan                         re-check every folder given before
mediaidx roots                        list them; `roots forget <folder>` drops one
mediaidx status                       counts, date sources, coverage
mediaidx status --conflicts           where EXIF and filename disagree
```

Everything is safe to re-run. An unchanged file is skipped without being opened,
so a second pass over an indexed folder takes seconds rather than hours. An
interrupted run resumes from the last committed chunk.

## Two things it will not do

**It never writes to the archive.** Files are opened read-only; nothing is moved,
renamed or tagged in place. `tests/test_scope.py` asserts the tree is
byte-identical after a scan.

**It never wanders.** Only the folders passed to `add` are indexed — never a
parent, never a sibling, and never through a junction leading out of the subtree.

It also knows nothing about any particular archive. The shipped defaults describe
how *applications* name files (WhatsApp, Pixel, screenshots) because that is the
same everywhere. How *your* library is organised — its paths, its folder naming,
which folders are of whom — lives in your `config.toml` and in the arguments you
pass, never in the code. `tests/test_config.py` asserts this.

## Tests

```
pytest
```

34 tests covering date resolution, filename and folder parsing, the sanity bounds
that stop a serial number being read as a date, scope containment, the read-only
guarantee, and the separation between shipped defaults and library-specific
configuration.

## Known constraint

The NIC is an Intel I225-V (2.5 GbE capable) currently linked at **100 Mbps**,
which caps reads at ~11.8 MB/s regardless of thread count. A full pass over the
232 GB library takes about 5.5 hours instead of ~35 minutes. Fixing the cable or
switch port is worth roughly 10× and requires no code. See spec §1.2.
