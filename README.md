# Media Organizer

A Windows desktop app that renames photos and videos based on their embedded
metadata (capture date, camera, GPS-derived location) and either sorts them
into `YYYY/YYYY MM` folders or renames them in place, leaving them where they
are.

Built with Electron + React + TypeScript.

## Features

- **Recursive folder scan** (optional) with a media-type filter (photos
  and/or videos) and an optional date range, so you can process just a subset
  of a large source folder.
- **Metadata extraction** via [exiftool-vendored](https://github.com/photostructure/exiftool-vendored.js),
  covering both photo EXIF and video container metadata (capture date,
  camera make/model, GPS coordinates).
- **Reverse geocoding** of GPS coordinates to a place name via
  [Nominatim](https://nominatim.org/) (OpenStreetMap), rate-limited to its
  usage policy and cached locally so repeat/nearby coordinates don't re-hit
  the network.
- **Two organize modes**:
  - *Move to folders* — renames and moves files into `dest/YYYY/YYYY MM/`
    (or `dest/Unsorted/` for files you route there manually).
  - *Rename in place* — renames files but leaves them in their current
    folder; nothing moves.
- **Interactive resolution** for files with no usable date in their
  metadata: you choose per file (or in bulk) whether to fall back to the
  filesystem's modified date, enter a date manually, or exclude the file
  from the batch entirely.
- **Collision-safe execution** — never overwrites an existing file (appends
  `-dupN` instead), copies across drives when needed (with a fallback for
  cross-volume moves), and keeps a full JSONL log of every move for
  auditing.
- **Windows-native date/time formatting** — reads your PC's actual
  short-date/short-time format from the registry, so on-screen dates match
  what you see in File Explorer.

- **Photo archive update** - after a batch is moved, the Done screen lists the
  folders it went into (each with an *Open* button, to look through and prune
  the photos in Explorer) and an *Update photo archive* button, also on the
  start screen. It runs `mediaidx update --events` from the Media classifier
  project (Kuva-arkisto): indexing, faces, places and events, the names backup,
  then publishing to the NAS viewer, with the stages shown as they run. Use it
  after naming faces in Kuva-arkisto too. The program is looked
  for at `~\.mediaidx\venv\Scripts\mediaidx.exe`; set `mediaidxPath` in
  `media-organizer-config.json` if it is elsewhere.

## Filename pattern

```
YYYYMMDD@HHMMSS-[Camera]-[Location]-[kuvaus]-[N].ext
```

- `Camera` and `Location` are omitted (along with their dash) when not
  available.
- `kuvaus` is a fixed placeholder added only to video filenames — a spot to
  manually add a short description later.
- `N` is a sequence number, unique and zero-padded across the whole batch,
  assigned by sorting all files chronologically by their effective capture
  date.

## Getting started

Requires Node.js 22+ and Windows.

```
npm install
npm run dev
```

## Building

```
npm run build        # typecheck + bundle
npm run build:win     # also packages a Windows installer via electron-builder
```

`build:win` needs a Windows privilege (symlink creation) that isn't granted
by default on all accounts — if it fails while downloading a code-signing
helper package, either enable *Developer Mode*
(Settings → Privacy & security → For developers) and retry, or just run the
app straight out of `dist/win-unpacked/` (a plain `npm run build` is enough
to produce that folder).

## Testing

```
npm test
```

Unit tests cover the naming/sequencing logic in isolation; integration tests
exercise the real `exiftool` binary and real filesystem operations (in
temp directories) for the scan pipeline and move engine.

## Known limitations

- Re-running "rename in place" over a folder it already organized will
  recompute the sequence number for every file from scratch (based on the
  full current set), so files that didn't otherwise change can still get a
  new `-N` suffix if the total count changed.
- Dates before 1990 in a photo's metadata are treated as implausible and
  routed to the resolution screen rather than trusted automatically — fine
  for camera-native photos, but worth knowing if you're organizing digitized
  older photos with a genuinely older EXIF date.
- The operations log (`userData/operations-logs/`) accumulates one file per
  run indefinitely; there's no automatic cleanup.
- There's no in-app undo — the operations log is the audit trail if you need
  to manually reverse a move.

## Project layout

```
src/
  main/            Electron main process: scanning, metadata, geocoding,
                    naming, execution, IPC handlers
  preload/          contextBridge API exposed to the renderer
  renderer/         React UI
  shared/types/     Types shared between main and renderer
```
