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
