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

## Make the iCloud choices need no thinking

Noted 2026-11-21, when fetching the autumn's photos took a discussion to get
the options right. For an iCloud source the screen asks questions whose
answer is always the same, and words them as if it were a local folder:

- **Move vs copy.** The downloaded files sit in
  `userData/icloud-photos-cache`; the radios only decide whether that cache
  copy is moved or copied into the archive. iCloud is never touched either
  way (the note says so, but it has to be read every time). For iCloud the
  answer is always *move* - copying only leaves a second copy filling C:.
  Likely shape: with iCloud selected, preselect move, hide or grey out the
  other modes, and say in one line what happens to the cache.
- **"Last synced into: Z:\Pictures\Unsorted"** shows the last *local*
  source folder, not where the sync downloads. It should show the cache
  folder, or nothing.
- **The From date.** It has to be worked out by hand from the newest photo
  already in the archive. Suggest it instead: the newest capture date
  already in the destination (from the filenames, `YYYYMMDD@HHMMSS-...`), or
  the last successful sync.
- **Photos already in the archive come again.** Nothing checks whether a
  photo is already in the destination: collisions are detected only by exact
  filename, and every batch numbers from 1, so the overlap day of a date
  range lands as second copies with new numbers. Kuva-arkisto recognizes
  them by content and shows them once, but they take space and have to be
  pruned by hand. Likely fix: before executing, compare each file's capture
  time + camera (or a content hash) against what the destination month
  folder already holds and mark matches *skipped - already in archive* in
  the preview.

## Say how to get the two-factor code

The 2FA step is where a sync stalls. icloudpd's sign-in page asks for the
code, and it is not obvious where it comes from; on 2026-08-29 it took about
five minutes from "Two-factor authentication is required" to a working code.

**What works (confirmed 2026-11-21):** on the iPhone, Settings -> [your name]
-> Sign-In & Security -> Two-Factor Authentication -> **Get Verification
Code**, and type that code into the sign-in page. It works even when the
iPhone does not show the "Allow" prompt, and offline. (Apple's trusted-device
route in the log, `verify/trusteddevice/securitycode`, not SMS.)

Wanted: say this at the moment it is needed, so it never has to be
remembered - when an iCloud sync starts (the note under the Apple ID field
on the start screen) and while ICloudSyncView is in `awaitingAuth`, a short
"Where do I get the code?" with the steps above.

## Merge with Kuva-arkisto into one (DONE 2026-11-22)

Noted 2026-11-21: this app and Kuva-arkisto (the photo archive, `mediaidx`,
in the `Media classifier` project beside this one) are one workflow - fetch
and organize here, look through in Explorer, then index and publish to the
NAS viewer there. Today they are linked only by the *Update photo archive*
screen, which runs `mediaidx update --events`.

**Resolved 2026-11-22: one repository, two applications.** Kuva-arkisto
moved in as `archive/` via `git subtree add --prefix=archive`, keeping its
full commit history. This repo's identity (name, GitHub remote, git
identity) stays as the home. The Electron/TypeScript vs Python split stays
exactly as it is -- this app still calls `mediaidx update` as a subprocess
and reads its JSON-lines stdout, unchanged. No Python backend, no rewrite.
