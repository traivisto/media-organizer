# Kuva-arkisto on the NAS

A viewing copy of the archive (spec §8.3), served from the Synology by Container Manager and reached
with a browser — at home, and away over Tailscale. It searches, shows and plays everything the PC's
copy does, described search included, and changes nothing: naming, corrections and indexing stay
on the PC, and `mediaidx publish` brings the NAS up to date (§8.4).

This file is published beside the project, so it is on the NAS as well as in the repository.

## What is where

On the NAS, in the shared folder `docker`:

```
kuva-arkisto/
  docker-compose.yml   the project Container Manager runs
  README.md            this file
  app/                 the application the image is built from
  data/                config.toml, photos.db, thumbs/, textmodel/
```

All of it is written by `mediaidx publish` on the PC; nothing in it is edited by hand. The
originals are not copied: the container reads the NAS's own `Media/Pictures`, read-only.

## Setting it up, once

Before starting, check one path. In File Station, right-click the shared folder **Media** →
**Properties**: **Location** should read `/volume1/Media`. If the volume is another one, change
`/volume1/Media/Pictures` in `docker-compose.yml` to match before creating the project — and in
`deploy/nas/docker-compose.yml` in the repository, or the next publish puts it back.

1. On the PC, publish once (it already has been on 2026-11-21):

   ```
   mediaidx publish
   ```

2. In DSM, open **Container Manager** → **Project** → **Create**.
   - **Project name:** `kuva-arkisto`
   - **Path:** choose `docker/kuva-arkisto`
   - **Source:** *Use existing docker-compose.yml* — Container Manager finds the file in the folder
     and shows it. Leave it as it is.
3. **Next.** If it offers **Web portal settings** (Web Station), leave it unticked: the application
   serves its own pages on port 8710.
4. **Next**, then **Done**, with *Start the project once it is created* ticked. Container Manager now
   builds the image: it downloads Python and the libraries the viewing copy uses, about 200 MB, and
   takes a few minutes the first time. When the project shows **Running**, it is up.
5. Open it in a browser — see *The address* below for which one.

## The address

`http://TRaDiskStation:8710` works from a Windows PC at home and from nothing else: the bare name is
answered by Windows' own name lookup, which phones do not have. Found 2026-11-21, when a phone found
the page only by its IP address, and an IP address is no use for sharing — WhatsApp does not turn
it into a link.

**The one to use and to share is the NAS's Tailscale name:**

```
http://tradiskstation.<tailnet>.ts.net:8710
```

This tailnet is `tail99a690.ts.net`, so the link is
`http://tradiskstation.tail99a690.ts.net:8710` — provided the NAS is called `tradiskstation` in
Tailscale too; the admin console's **Machines** page shows the name it really has, and the Tailscale
app on the phone shows the whole address under the NAS's entry. It is an ordinary web address, so
WhatsApp makes it a link, and the same link works at home and away for anybody whose phone has
Tailscale on — at home Tailscale carries the traffic straight across the house, not out and back.
It needs MagicDNS, which is on unless it has been turned off in the admin console's **DNS** page.

Without Tailscale, at home only: `http://TRaDiskStation.local:8710` on an iPhone, an iPad or a Mac,
which find the NAS by Bonjour; otherwise the IP address, `http://192.168.86.253:8710` on
2026-11-21 — and that one changes if the router ever hands the NAS another, unless it is reserved
for the NAS in the router's settings.

If DSM's firewall is on (**Control Panel** → **Security** → **Firewall**), add a rule allowing TCP
port 8710 from the home network and from Tailscale (`100.64.0.0/10`).

## Afterwards

**After indexing or naming on the PC**, run `mediaidx publish`. New photographs, names and
corrections are on the NAS as soon as it finishes; nothing needs doing in Container Manager.

**When publish says the application changed**, the container is still running the image it was
built from. In Container Manager → **Project** → `kuva-arkisto`: **Stop**, then **Build**. It
rebuilds the image and starts again; it takes a minute or two, quicker than the first time.

**To look at what it is doing:** Container Manager → **Container** → `kuva-arkisto` → **Log**. Memory
is about 50 MB at rest and about 1 GB for ten minutes after a described search, while the search
model is held; the project's limit is 1.8 GB.

## When something is wrong

| What happens | Why, and what to do |
|---|---|
| The page does not open | Is the project **Running**? Is port 8710 allowed by the firewall? Is the address one the device can look up (*The address*, above)? Try the IP address to tell the two apart. |
| Photographs show, but *See the original* says *not reachable* | The originals are not where the project mounts them: check `Media` is on `/volume1`, as above. The log says so at start-up too. |
| A described search says the search model does not match | The PC's index was rebuilt with another model. Run `mediaidx textmodel` and then `mediaidx publish` on the PC. |
| `publish` says *not allowed* | The account the PC uses for the NAS (Tommi) needs read/write on `docker/kuva-arkisto`, and only there: File Station → right-click the folder → **Properties** → **Permission**. |
| `publish` says the folder *holds files that publish did not put there* | Something else was put in `kuva-arkisto`. Move it out; publish removes what the archive no longer has, so it will not share a folder. |
| Port 8710 is taken on the NAS | Change the left-hand number in `"8710:8710"` in `deploy/nas/docker-compose.yml`, publish, and **Stop** → **Build**. |

## Taking it away

Container Manager → **Project** → `kuva-arkisto` → **Stop**, then **Delete**; in **Image**, delete
`kuva-arkisto:viewer`; then delete the folder `docker/kuva-arkisto` in File Station. The archive on
the PC and the photographs on the NAS are untouched by all of it.
