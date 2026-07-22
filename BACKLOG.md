# Backlog

Ideas noted for later - not scheduled, not designed in detail yet.

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

## Opt-in "delete from iCloud after download" step

`PickDirsView.tsx` now shows a note, whenever the source is from an iCloud
sync, that organizing only ever affects the local downloaded copy - iCloud
itself is never touched. The user may later actually want a real
"delete/move out of iCloud" after a successful download+organize, to free up
iCloud storage. icloudpd already supports this (`--delete-after-download`,
`--keep-icloud-recent-days`, see `icloudSyncEngine.ts`'s `buildArgs`), but
it's deliberately not wired up since deleting from iCloud is a genuinely
destructive, hard-to-reverse action that shouldn't ride along silently on the
existing organizeMode choice.

Not designed yet - the likely shape: a separate, explicitly-labeled, opt-in
step/checkbox for "also delete from iCloud after this completes", kept
clearly distinct from the organizeMode radios so it can't be picked by
accident.
