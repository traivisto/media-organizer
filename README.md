# Kuva-arkisto

A local, private index over a home photo archive. Faces, scenes, places and events
made searchable, with nothing leaving the machine and no original file ever touched.

Full design: [spec.md](spec.md).

## Everyday use

| I want to… | Where | How |
|---|---|---|
| Look at and search photographs | Any browser: phone, tablet, PC | <https://tradiskst.synology.me:8443> (away from home with Tailscale on) |
| Bring in new photographs | The PC | Media Organizer, a look in Explorer, then **Update photo archive** |
| Name faces, fix mistakes, name events | The PC only | `mediaidx serve`, then **Update photo archive** (or `mediaidx update`) |

The commands are spelled `mediaidx`; if the alias is not on your path, see
[Use](#use) for the full path to the executable.

### Who can change what

- **The NAS viewer only shows.** Anyone on the home network, or on your
  Tailscale, can browse and search it, but it has no forms and refuses every
  change. A guest cannot break anything.
- **Changes are made on the PC**, in `mediaidx serve` at
  <http://localhost:8710>. It answers only on that machine, not the network,
  and the index sits in your Windows profile, so other accounts on the PC cannot
  open it either.
- **Changes live in the index, never in the photographs.** A name given to a
  face is not written into the file, so it does not show in iCloud, Photos or
  any other program — only here.
- **The NAS sees a change after the next publish.** `mediaidx update` (or
  Media Organizer's button) ends with one.

### New photographs

1. **Fetch them** with Media Organizer (the `media-organizer` project beside
   this one):
   - Source **iCloud**, your Apple ID.
   - **From**: the day of the newest photograph already in the archive — look
     at the last file in the newest month folder. **To**: today.
   - **Rename and move into Year/Month folders in a destination**. This moves
     only the copy downloaded to the PC; nothing is ever deleted from iCloud.
   - Destination `Z:\Pictures`, then **Scan**, check the preview, and run it.
   - **The two-factor code:** on the iPhone, *Settings → your name → Sign-In &
     Security → Two-Factor Authentication → Get Verification Code*, and type it
     into the sign-in page that opened in the browser. This works even when the
     phone shows no prompt.
2. **Look through them.** The *Done* screen lists the folders they went into,
   each with an **Open** button. Delete in Explorer what you don't want. The
   first day of the range overlaps the last fetch: photographs from it that are
   already in the archive come again with new numbers (`…-001`, `…-002`) and can
   be deleted too.
3. **Update the archive**: **Update photo archive…** in Media Organizer (on the
   *Done* screen, or later from its start screen), or here:

   ```
   mediaidx update
   ```

   It finds the new and removed files, builds search vectors and thumbnails,
   finds faces and gives them the names of people already known, works out
   places and events, and publishes to the NAS — then says how many photographs
   and faces were new. A minute when there is little to do. A new year folder,
   `Pictures\2027` in January, is added by itself.

### Naming faces and correcting things

1. On the PC: `mediaidx serve`, then <http://localhost:8710>. Name faces
   under *People*, correct a wrong name on a photograph's page, name events,
   answer the *Review* questions, reject a wrongly guessed place under *Places*.
   Each change is saved as you make it.
2. Afterwards, **Update photo archive** in Media Organizer, or:

   ```
   mediaidx update
   ```

   That lets the new names spread — every unnamed face that looks like a named
   one takes the name; corrections made by hand have produced about 75 further
   named faces each — then writes `names.json` and publishes to the NAS.
3. Commit `names.json` when it has changed.

`names.json` is the one thing here that cannot be rebuilt from the archive:
every name, correction, event name, rejected place, and the folders passed to
`add`. `update` writes it (through `mediaidx export`) only when something in it
changed, and a copy to every path in `names_export_extra` — on this machine,
the NAS backup folder. If a copy cannot be written, the update stops there and
says so, rather than letting a sleeping disk look like a backup.

The dated snapshots under `~\.mediaidx\backups\` are a different thing: written
automatically before anything that names thousands of faces at once, local, and
only the last ten kept. They protect you from a bad run, not from a dead disk.

### Less often

| When | What to run |
|---|---|
| A folder other than a year was added to `Pictures` | `mediaidx add "<folder>"` once (see below), then `mediaidx update` |
| Photographs were moved or renamed on the NAS | `mediaidx update`. Identity is the content hash, so names and corrections follow the file; anything gone is marked missing, never deleted |
| The code was updated | `mediaidx meta` re-reads what a new stage version made stale, and `update` does the rest. Schema migrations run on their own. If `publish` says the application changed, rebuild the project on the NAS ([deploy/nas/README.md](deploy/nas/README.md#afterwards)) |
| A threshold in `config.toml` changed | `faces cluster --rebuild` recomputes from scratch — about 19 minutes, names preserved by majority vote |
| Something looks wrong | `doctor` for the environment and the GPU, `status --errors` for files that failed, `status --conflicts` where EXIF and the filename disagree |
| A folder should no longer be watched | `roots forget <folder>`. Photographs already indexed are kept |
| **The machine or its disk was replaced** | The index lives outside OneDrive by design, so it is not backed up. `setup.ps1`, `add` each folder, `embed`, `faces`, `faces cluster`, then **`import`** to put every name and correction back from `names.json`, and `derive`. Hours of machine time, and no handwork lost |

That last row is the reason `names.json` exists. Its one delicate step has been
tested — restoring into a copy of the library with every name and person deleted
brought all of them back, counts unchanged (spec §8.1) — but the path as a whole,
from an empty machine, has never been walked.

### The stages one at a time

`update` runs these in order, stopping at the first that fails; run one alone
when it needs looking at. Every stage is incremental and does only what is
outstanding, so each is safe to run whenever:

```
mediaidx scan            new, changed and missing files
mediaidx embed           search vectors and thumbnails for the new photographs
mediaidx faces           detect and vectorise their faces (wants the GPU)
mediaidx faces cluster   attach the new faces to people already named
mediaidx derive          places, events and the review queue
mediaidx export          names.json and its copies (update skips it if unchanged)
mediaidx publish         bring the NAS viewer up to date
```

**`scan` revisits only the folders that were passed to `add`.** On this library
those are the year folders, `1998` through `2026` — not `Pictures` itself.
Photographs dropped into a year that is already indexed are found. A **new**
top-level folder is not, and nothing will look wrong: `scan` reports no changes,
because as far as it knows there is nothing there to look at. `mediaidx roots`
lists what is actually being watched. `update` adds a new *year* folder by
itself; any other new folder is indexed once, by hand, and is a root from then
on:

```
mediaidx add "\\TRaDiskStation\Media\Pictures\Kesäleiri"
```

If the folder is mostly of one person, `add --person Emmi` seeds the face
naming; if its name carries nothing a reader would recognise, `add --label
"..."` gives it one.

## Status

**M4c complete, and a viewing copy on the NAS** — one search box answers across
people, places, dates and what a photograph shows, all at once; every
photograph says what is known about it and on what evidence; anything the
application got wrong can be put right in a way no rebuild can undo; and a
read-only copy is served from the NAS to any browser, phones included (spec
§8.3–8.6).

| Milestone | Scope | State |
|---|---|---|
| M1 | Scan, hash, EXIF, filename/folder dates, schema, `status`, `doctor` | **done** |
| M2 | Search vectors, thumbnails, search UI | **done** |
| M3a | Faces: detection, embeddings, face crops | **done** |
| M3b | Clustering, naming, people UI | **done** |
| M4a | Places: gazetteer, geocoding, hierarchy, UI | **done** |
| M4b | Events, review queue, review UI | **done** |
| M4c | Unified search, photo screen, corrections | **done** |
| NAS | Read-only viewing copy, publish, phone layout, `update` | **done** |
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

## Events

`mediaidx derive` works out what happened, from three kinds of evidence and in
this order.

A **folder somebody labelled** has the last word. `2019 07 Alicante` is one
holiday, not eleven separate afternoons, however long the gaps inside it — and
the label reaches down into subfolders, so scans filed under
`2001 01 Thaimaa\skannatut` are part of the same trip.

Otherwise the **clock**: inside a bare month folder a gap longer than four hours
ends the event. This is what covers the twelve years of the archive with no GPS
at all. And where two photographs both carry a coordinate, the **map** refines
it — a jump of more than 25 km ends the event too, so an afternoon in Helsinki
and an evening in Tampere are not one thing.

An event's place is not simply the commonest one among its photographs. A week
driving through Spain is in Spain, not in whichever village produced the most
pictures, so the answer is the deepest place that most of them share — and one
stray coordinate at an airport cannot drag the whole event down to nothing.

Everything is rebuilt from scratch each time, so no event id is stable and
nothing you decide is stored on one. A name you type and a split you make are
keyed to a *photograph* instead, which survives every rebuild. Measured, the
whole of `derive` — places, events and the queue over 39,884 photographs — is
**2.3 seconds**.

## The review queue

`mediaidx review` asks the questions worth asking, largest first, so the first
one covers the most photographs. Each shows twelve pictures spread across the
event, the dates, the place, the folder, and who is already known to be in it.
Where the database knows enough it proposes a name and says exactly what the
proposal rests on — *Ansku in 22 of 30 photographs; 28 of 30 placed in Alicante
by coordinate* — because correcting a proposal is far less work than composing
one. Where it does not, it offers nothing rather than a date dressed up as a
name. Type, Enter to accept; Esc skips, and a skipped question comes back after
the next `derive`, when the neighbouring events have names and it is easier.

**Being an event and being a question are two different things.** Measured on
this archive the grouping finds 1,374 events, 1,209 of them with no folder
label — twenty-eight years of ordinary photography really is that many separate
afternoons, and no threshold makes it fewer. Asking 1,209 questions is not a
finite queue, it is a second job. So everything of five photographs or more is
an event and appears in the timeline, and only the substantial ones are asked
about: **314 questions, covering 7,726 photographs**. The floors are
`queue_min_assets` and `queue_min_faces` in your config, and the rest is never
lost — it is in the Events and People views whenever you want it.

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
mediaidx derive                       rebuild places, events and the queue
mediaidx derive --no-places           only the events and the queue
mediaidx events                       what happened, and when
mediaidx events --unnamed             only the ones still without a name
mediaidx review                       work through the questions, largest first
mediaidx meta                         re-read metadata whose stage version is behind
mediaidx search "a phrase"            free-text search from the terminal
mediaidx serve                        the local UI, http://localhost:8710
mediaidx update                       new photographs or names end to end, then publish
mediaidx update --no-publish          the same, leaving the NAS alone
mediaidx publish                      bring the NAS viewer up to date
mediaidx export                       write names.json, and its copies
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

Some 580 tests covering date resolution, filename and folder parsing, the sanity bounds
that stop a serial number being read as a date, GPS hemispheres, scope
containment, the read-only guarantee, face storage and crop geometry, clustering,
naming, merging and searching by person, the gazetteer and both ways into it, event
grouping and every boundary in it, what survives a rebuild and what deliberately
does not, the review queue and its screen, and the separation between shipped
defaults and library-specific configuration.

The face *model* is not exercised there — it needs a GPU, 300 MB of weights and a
photograph of a real person. `mediaidx doctor` covers that end instead, and it
checks the one thing that fails silently: whether inference actually got the GPU,
by building a session and asking, rather than trusting the provider list.

## Known constraint

The NIC is an Intel I225-V (2.5 GbE capable) currently linked at **100 Mbps**,
which caps reads at ~11.8 MB/s regardless of thread count. A full pass over the
232 GB library takes about 5.5 hours instead of ~35 minutes. Fixing the cable or
switch port is worth roughly 10× and requires no code. See spec §1.2.
