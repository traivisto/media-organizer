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
  .env                 the version of the application, which names its image
  README.md            this file
  app/                 the application the image is built from
  data/                config.toml, photos.db, thumbs/, textmodel/
  logs/                searches.jsonl: what was searched for (`mediaidx searches` on the PC)
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
5. Open it in a browser: `https://tradiskst.synology.me:8443` at home — see *The address* below.

## The address

**At home and away, the one to use and to share:**

```
https://tradiskst.synology.me:8443
```

It works in every browser on every device on the home network, Tailscale or not; away from home it
works with Tailscale on (below); and WhatsApp makes it a link. Set up 2026-11-21, after two other addresses failed on a phone:

- `http://TRaDiskStation:8710` is answered by Windows' own name lookup, which phones do not have.
- Chrome on an iPhone refuses a page that is not encrypted, so no `http://` address will do there —
  not the IP address, not `.local`, not the Tailscale name. An encrypted page needs a certificate,
  and a certificate needs a name in public DNS.

How it is made, all in DSM, with no port opened to the internet:

1. **Control Panel → External Access → DDNS**: the Synology name `tradiskst.synology.me`, with its
   **External address (IPv4)** set by hand to the NAS's address on the home network,
   `192.168.86.253`, instead of *Auto*. So the name leads to the NAS from inside the house only;
   from outside it leads to an address nobody there can reach.
2. With it, DSM fetched a Let's Encrypt certificate for `tradiskst.synology.me` and
   `*.tradiskst.synology.me`, and renews it itself.
3. **Control Panel → Login Portal → Advanced → Reverse Proxy**: source **HTTPS**,
   `tradiskst.synology.me`, port **8443** → destination **HTTP**, `192.168.86.253`, port **8710**.
   Port 443 is refused as reserved for DSM. The destination must be the NAS's own address:
   `localhost` and `127.0.0.1` answer *502 Bad Gateway*, because Container Manager's published ports
   do not listen there.
4. **Control Panel → Security → Certificate → Settings**: the rule `*:8443` uses the synology.me
   certificate.

The NAS's address is fixed in the router, which the name depends on. If it ever changes, change
the DDNS entry and the reverse proxy's destination with it.

**Away from home** the same link works with Tailscale on, and nothing works without it — by design,
since nothing is open to the internet. Set up 2026-11-21 and tried over mobile data:

- The NAS is a Tailscale **subnet router** for `192.168.86.253/32` — its own address and nothing
  else, approved in the admin console under **Machines** → the NAS → **Edit route settings**.
  The name `tradiskst.synology.me` already answers `192.168.86.253` everywhere; with the route, a
  phone away from home reaches that address through Tailscale.
- Only the NAS, not the whole `192.168.86.0/24`: that is Google Wifi's default range and common in
  other homes, where a phone would take the address for one on the local network. The narrower
  route wins over a local network's, and it gives Tailscale no way into the rest of the house.
- On the phone, the Tailscale app's **Use Tailscale subnets** has to be on, which it is by default.

The NAS's own Tailscale name, `tradiskstation.tail99a690.ts.net`, serves `http://` on port 8710,
which Safari opens and Chrome on an iPhone does not; it is not needed for anything above.

If DSM's firewall is on (**Control Panel** → **Security** → **Firewall**), allow TCP port 8443 from
the home network, and 8710 from the home network and from Tailscale (`100.64.0.0/10`).

## Afterwards

**After indexing or naming on the PC**, run `mediaidx publish`. New photographs, names and
corrections are on the NAS as soon as it finishes; nothing needs doing in Container Manager.

**When publish says the application changed**, the container is still running the image it was
built from. In Container Manager → **Project** → `kuva-arkisto`: **Stop**, then **Build**. It
builds the new image and starts again; it takes a minute or two, quicker than the first time.
Each image is named after the application in it (`kuva-arkisto:` and a version that publish
writes into `.env`), because Build does not rebuild an image whose name already exists. Older
ones can be deleted under **Image** now and then; they share all but their last layer, so they
take little room.

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
every `kuva-arkisto` image; then delete the folder `docker/kuva-arkisto` in File Station. The archive on
the PC and the photographs on the NAS are untouched by all of it.
