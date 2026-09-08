# Kuva-arkisto

A local, private index over a home photo archive. Faces, scenes, places and events
made searchable, with nothing leaving the machine and no original file ever touched.

Full design: [spec.md](spec.md).

## Status

**M4a complete** — the library is searchable by free text, by who is in the
photograph, and by where it was taken.

| Milestone | Scope | State |
|---|---|---|
| M1 | Scan, hash, EXIF, filename/folder dates, schema, `status`, `doctor` | **done** |
| M2 | Search vectors, thumbnails, search UI | **done** |
| M3a | Faces: detection, embeddings, face crops | **done** |
| M3b | Clustering, naming, people UI | **done** |
| M4a | Places: gazetteer, geocoding, hierarchy, UI | **done** |
| M4b | Events, review queue | |
| M5 | Captions | |
| M6 | Video | deferred |

M3 is split because only M3a reads files. Clustering and the people UI work
entirely from the database and the local face crops, so they can be re-run as
often as a threshold needs changing without touching the NAS again.

## Places

`mediaidx places` puts photographs on the map from two sources, and never
pretends they are the same kind of evidence.

A **coordinate** is looked up against an offline GeoNames extract — no request
leaves the machine — binned to about a kilometre so a week in one town costs a
handful of lookups. How much a hit may claim depends on how far away the nearest
town is: the town itself within 25 km, only the region out to 100, only the
country beyond that, and nothing at all past 500. The gazetteer holds points, not
borders, and a photograph taken mid-Baltic is in no municipality.

A **folder name** covers the two decades of the archive that have no GPS at all.
`2019 07 Alicante` places its photographs in Alicante; `Hennan rippijuhlat`
matches nothing and is left alone, because only a label matching a gazetteer name
in full is ever accepted. Alternate names are what make this work in Finnish:
`Lontoo` and `Tukholma` are in the gazetteer as names of London and Stockholm.

Those matches are guesses made from a single word, so the Places view lists every
one of them, most ambiguous first, with the number of places that carried the
name — and one click rejects it for good. A guess is never allowed to look like a
measurement: the photograph's own page says *from the coordinate* or *from the
folder name*, every time.

A place means everything inside it. `Finland` finds photographs filed under
Helsinki without anyone naming a single region.

Measured on the reference library: 22,830 of 39,884 photographs placed across 39
countries in 11 seconds — 20,402 by coordinate and 2,428 more from 35 folder
labels, of which five are wrong and waiting to be rejected.

## People

`mediaidx faces` finds faces; `mediaidx faces cluster` groups them; naming
happens in the UI under **People**, biggest cluster first, because naming the
largest one is worth the most. Nothing is ever named automatically. Where a
folder was indexed with `--person`, the UI offers that name as a suggestion and
says why, but it stays a suggestion until you accept it.

Two things the review screen expects you to do, because the data genuinely
requires them:

- **Merge.** One person across 28 years produces several clusters — a child at 4
  and at 17 are further apart than two unrelated adults, and no threshold fixes
  that. Merging is one action, and a person may hold any number of clusters.
- **Not a person.** Sculpture, portraits on a wall, mannequins. On this library
  the largest such cluster held 175 faces from a single art installation, all
  correctly grouped and none of them anybody.

## Setup

Run once per machine, in a **non-elevated** PowerShell:

```
cd "C:\Users\traiv\OneDrive\My Digital Solutions\Media classifier"
.\setup.ps1
```

If PowerShell refuses to run it, allow scripts for that window only with
`Set-ExecutionPolicy -Scope Process Bypass`.

It installs uv, a private Python 3.12 (your system Python is untouched), PyTorch
with CUDA, ExifTool, the GeoNames extract behind place search, and writes
`~\.mediaidx\config.toml`. Budget a few minutes: the CUDA wheels are about 2.5 GB.

The gazetteer is the only reference data downloaded, ~16 MB, and the only reason
the application ever reaches the network after setup. Geocoding itself is
entirely offline: no coordinate from your archive is ever sent anywhere.

Everything lives in `C:\Users\<you>\.mediaidx` — database, thumbnail cache, logs
and the virtual environment.

**Why not `%LOCALAPPDATA%`,** which would be the conventional choice: the Claude
desktop app is an MSIX package, so processes it launches get their `AppData\Local`
writes redirected into a package-private store under
`%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Local\`. Files written there are
invisible to Explorer and to your own shells — a confusing, silent failure. The
profile root is not virtualised.

**And why not `C:\mediaidx`,** which an earlier version used: root-level folders
grant `Authenticated Users` Modify, so every account on the machine could read the
index. That index comes to hold face embeddings and the names of the people in your
photographs. The profile root inherits per-user ACLs instead.

Everything it creates lives **outside OneDrive**, because a SQLite WAL database
and a sync client eventually corrupt each other (spec §10.5). That also means the
environment does not travel between machines — the source code syncs, the runtime
does not. Run `setup.ps1` on each machine that will do indexing.

Do not run elevated: an administrator shell does not inherit your mapped network
drives, so `Z:\` would not exist.

## Use

`setup.ps1` adds a `mediaidx` alias to your PowerShell profile. It only takes
effect in a **new** window, and only if your execution policy permits profiles to
load — `Restricted` silently skips them. To allow it once:

```
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Otherwise call the executable directly, which always works:

```
C:\Users\<you>\.mediaidx\venv\Scripts\mediaidx.exe status
```

(`%USERPROFILE%` is cmd syntax and does not expand in PowerShell; there it is
`$env:USERPROFILE`, and invoking a path built from it needs the call operator:
`& "$env:USERPROFILE\.mediaidx\venv\Scripts\mediaidx.exe" status`.)

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
mediaidx embed                        search vectors and thumbnails
mediaidx faces                        find and vectorise every face
mediaidx faces --limit 200            try it on a slice first
mediaidx faces cluster                group the faces into people
mediaidx faces cluster --rebuild      recompute from scratch, keeping names
mediaidx people                       who is named, and how much of the library
mediaidx places                       put photographs on the map
mediaidx places --load                rebuild the gazetteer from the extract first
mediaidx places --rejected            the folder matches you threw out
mediaidx meta                         re-read metadata whose stage version is behind
mediaidx search "a phrase"            free-text search from the terminal
mediaidx serve                        the local UI, http://localhost:8710
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

180 tests covering date resolution, filename and folder parsing, the sanity bounds
that stop a serial number being read as a date, GPS hemispheres, scope
containment, the read-only guarantee, face storage and crop geometry, clustering,
naming, merging and searching by person, the gazetteer and both ways into it, and
the separation between shipped defaults and library-specific configuration.

The face *model* is not exercised there — it needs a GPU, 300 MB of weights and a
photograph of a real person. `mediaidx doctor` covers that end instead, and it
checks the one thing that fails silently: whether inference actually got the GPU,
by building a session and asking, rather than trusting the provider list.

## Known constraint

The NIC is an Intel I225-V (2.5 GbE capable) currently linked at **100 Mbps**,
which caps reads at ~11.8 MB/s regardless of thread count. A full pass over the
232 GB library takes about 5.5 hours instead of ~35 minutes. Fixing the cable or
switch port is worth roughly 10× and requires no code. See spec §1.2.
