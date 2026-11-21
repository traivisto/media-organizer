# Backlog

Ideas noted for later - not scheduled, not designed in detail yet.

## Per-camera timestamp offset correction - done (naming/sequencing only)

Implemented as "Fix time order in an already-organized folder" (link on the
start screen). Use case: photos already organized turn out to be visibly
out of order because one camera's clock was set to a different timezone.
The flow rescans the already-organized folder in place, shows the detected
cameras (make/model, file count, date range), and lets the user set a
±HH:MM offset per camera before re-sequencing/renaming/moving the files.
Only `effectiveCapturedAt` (naming/sequencing) is corrected - the original
file's EXIF timestamp is never rewritten.

Not done: rewriting the real EXIF timestamp via exiftool, and per-camera
date sub-ranges (a single offset applies to all of a camera's files across
the whole folder). Revisit if a real case needs either.

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
