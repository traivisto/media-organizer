# Backlog

Ideas noted for later - not scheduled, not designed in detail yet.

## Persist window size and Preview table column widths

The app currently always opens at a fixed default window size, and the
Preview table's resizable columns (see `PreviewTable.tsx`) reset to their
default proportions on every launch. Should persist both across restarts
(likely via `electron-store`, the same mechanism already used for
`lastSourceDir`/`lastDestDir` in `appConfig.ts`) so the app remembers how you
last left it.

## Native date inputs don't follow the Windows-registry date format

`windowsRegionalFormat.ts` reads this PC's actual short-date/short-time
pattern from the registry and uses it for the Preview table and the
resolution screen's "Use file date (...)" label - but the *native* HTML
inputs still render in whatever format Chromium's own locale resolution
picks, not the Windows-registry pattern:

- `PickDirsView.tsx`'s "From"/"To" date-range filter (`<input type="date">`)
- `ResolutionRow.tsx`'s manual date entry (`<input type="datetime-local">`)

Native date/datetime-local inputs don't accept a custom display format via
CSS/JS in the way plain text does - their calendar-picker rendering is
controlled by the browser's own locale, not something we can just pass a
date-fns pattern into. Worth an audit of every date-related UI element for
consistency; likely fix is either forcing Chromium's locale at the Electron
level (e.g. `app.commandLine.appendSwitch('lang', ...)` matching the detected
Windows format) or replacing the native inputs with custom-formatted text
inputs plus validation.

## Per-camera timestamp offset correction

Use case: combining photos taken with multiple cameras whose clocks weren't
in sync - one might be 5 minutes slow, another 2 hours off because it was
set to a different timezone. Right now the app trusts each file's EXIF
timestamp as-is, so photos taken at the same real-world moment on different
cameras can end up out of chronological order (and thus mis-sequenced /
placed in the wrong month folder near a boundary).

Not designed yet - options to weigh later:
- Let the user specify a per-camera (or per-batch) offset applied only to
  naming/sequencing, without touching the original file's metadata.
- Actually rewrite the EXIF timestamp via exiftool, so the corrected date
  is reflected everywhere (including outside this app).
- Some combination, or a per-file manual override in the preview screen.

Revisit once there's a concrete batch of mixed-camera photos to design
against.

## Visual polish

Current UI (`styles.css` + inline styles throughout the components) is
functional but plain - default form controls, no real layout/typography
system, minimal color. Worth a proper design pass: consistent spacing,
color palette (light/dark aware, matching the OS theme), better button/input
styling, clearer visual hierarchy between the phase screens.

Note: "Claude Design" (claude.ai/design, currently Beta) is a separate
Anthropic web app for generating UI mockups, wireframes, and design systems
visually - it's not something drivable from within Claude Code. The likely
workflow: use Claude Design in the browser to mock up the app's screens
(folder picker, preview table, resolution screen, results), then bring the
resulting mockups/design system back here for Claude Code to implement in
the actual React components/CSS.

## Unclear to the user what "move" deletes from when the source is an iCloud sync

`PickDirsView.tsx`'s organizeMode labels ("Rename and move...", "Rename only,
keep files...", "Rename and copy...") describe what happens to *the source
folder*, worded as if source is always a folder the user picked by hand. When
the source is instead an iCloud-synced local cache folder
(`icloud-photos-cache` under userData, from `icloudSyncEngine.ts`), it's not
obvious from the UI that "move" only ever moves the local downloaded copy -
iCloud itself is never touched by the current implementation, regardless of
organizeMode. A user could reasonably read "move" as "move out of iCloud."

Also: the user may later actually want that - a real "delete/move out of
iCloud" after a successful download+organize, freeing up iCloud storage.
icloudpd already supports this (`--delete-after-download`,
`--keep-icloud-recent-days`, see `icloudSyncEngine.ts`'s `buildArgs`), but
it's deliberately not wired up since deleting from iCloud is a genuinely
destructive, hard-to-reverse action that shouldn't ride along silently on the
existing organizeMode choice.

Not designed yet - options to weigh later:
- Add UI copy clarifying, whenever the source came from an iCloud sync, that
  organizing only ever affects the local downloaded copy.
- Add a separate, explicitly-labeled, opt-in step/checkbox for "also delete
  from iCloud after this completes" - kept clearly distinct from the
  organizeMode radios so it can't be picked by accident, since it's a
  fundamentally different (and riskier) action than local move/copy/rename.

## "Sync from iCloud" conflates picking a source with starting work

Everywhere else in `PickDirsView.tsx`, picking a source is instant and inert:
"Choose source folder" just opens a dialog and sets `sourceDir` - nothing
happens until the user separately presses "Scan". "Sync from iCloud" breaks
that pattern: one click both selects iCloud as the source *and* immediately
kicks off the real work (spawns icloudpd, opens a sign-in page in the
browser, starts downloading). A user clicking it expecting the same inert
"just picking a source" behavior as the folder button gets an unexpected,
harder-to-back-out-of action instead.

Not designed yet - the likely fix: stop treating "Sync from iCloud" as its
own immediate-action button. Instead make it just *select* iCloud as the
source type (e.g. alongside "Choose source folder", not a separate flow), and
have the existing "Scan" button be the single, consistent place all real work
starts - it would run the iCloud sync first (if that's the selected source
type) and then proceed into the normal scan, same as it does today for a
locally-picked folder.
