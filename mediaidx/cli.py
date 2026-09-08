"""Command line surface."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config as config_mod
from . import db, dates, exif, pipeline
from . import paths as pathutil


def _open(args, *, create=True):
    cfg = config_mod.load(args.config)
    con = db.connect(cfg.database, create=create)
    if create:
        db.migrate(con)
    return cfg, con


def _fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else str(n)


# --------------------------------------------------------------------------
# add / scan
# --------------------------------------------------------------------------

def cmd_add(args) -> int:
    cfg, con = _open(args)
    if args.workers:
        cfg.scan["reader_threads"] = args.workers

    total = pipeline.Stats()
    for raw in args.paths:
        root = os.path.abspath(raw)
        if not os.path.isdir(pathutil.long_path(pathutil.to_unc(root))):
            print(f"not a folder: {raw}", file=sys.stderr)
            return 2

        print(f"\n{pathutil.to_unc(root)}", flush=True)
        if args.dry_run:
            print("  (dry run -- nothing is read or written)", flush=True)

        try:
            st = pipeline.index_folder(
                con, root, cfg,
                recurse=not args.no_recurse,
                user_label=args.label,
                person_hint=args.person,
                dry_run=args.dry_run,
                progress=pipeline.render_progress,
            )
        except KeyboardInterrupt:
            print("\n  interrupted -- completed chunks are committed; "
                  "re-run to continue")
            return 130

        print()
        for k in ("seen", "skipped", "new_files", "new_assets",
                  "duplicates", "failed", "missing", "conflicts"):
            setattr(total, k, getattr(total, k) + getattr(st, k))
        total.bytes_read += st.bytes_read
        for k, v in st.by_source.items():
            total.by_source[k] = total.by_source.get(k, 0) + v

    print("\nindexed")
    print(f"  files seen        {_fmt(total.seen):>10}")
    print(f"  unchanged, skipped{_fmt(total.skipped):>10}")
    print(f"  files recorded    {_fmt(total.new_files):>10}")
    print(f"  new photographs   {_fmt(total.new_assets):>10}")
    print(f"  duplicate copies  {_fmt(total.duplicates):>10}")
    if total.failed:
        print(f"  unreadable        {_fmt(total.failed):>10}")
    if total.missing:
        print(f"  now missing       {_fmt(total.missing):>10}")
    if total.by_source:
        print("  dates from        " + ", ".join(
            f"{k} {v:,}" for k, v in sorted(
                total.by_source.items(), key=lambda kv: -kv[1])))
    if total.conflicts:
        print(f"  date conflicts    {_fmt(total.conflicts):>10}"
              "   (see `mediaidx status --conflicts`)")
    print(f"  {total.mb_per_s:.1f} MB/s over {total.elapsed:.0f}s")
    return 0


def cmd_scan(args) -> int:
    cfg, con = _open(args, create=False)
    roots = args.paths or [
        r["path"] for r in con.execute(
            "SELECT path FROM folders WHERE is_root = 1 ORDER BY path")]
    if not roots:
        print("no roots recorded yet -- use `mediaidx add <folder>` first")
        return 1
    args.paths = roots
    args.label = args.person = None
    args.no_recurse = False
    args.dry_run = False
    return cmd_add(args)


# --------------------------------------------------------------------------
# roots
# --------------------------------------------------------------------------

def cmd_roots(args) -> int:
    cfg, con = _open(args, create=False)
    if args.forget:
        target = pathutil.to_unc(os.path.abspath(args.forget))
        cur = con.execute(
            "UPDATE folders SET is_root = 0 WHERE path = ? AND is_root = 1", (target,))
        con.commit()
        if cur.rowcount:
            print(f"forgotten: {target}\n"
                  "indexed photographs are retained; it is simply no longer re-scanned")
            return 0
        print(f"not a recorded root: {target}", file=sys.stderr)
        return 1

    rows = con.execute(
        """SELECT f.path, f.person_hint, f.user_label, f.root_added_at,
                  (SELECT COUNT(*) FROM files x WHERE x.path LIKE f.path || '%') AS n
           FROM folders f WHERE f.is_root = 1 ORDER BY f.path""").fetchall()
    if not rows:
        print("no roots recorded yet")
        return 0
    for r in rows:
        extra = []
        if r["person_hint"]:
            extra.append(f"person={r['person_hint']}")
        if r["user_label"]:
            extra.append(f"label={r['user_label']}")
        suffix = ("  [" + ", ".join(extra) + "]") if extra else ""
        print(f"{r['n']:>8,}  {r['path']}{suffix}")
    return 0


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def cmd_status(args) -> int:
    cfg, con = _open(args, create=False)

    if args.conflicts:
        rows = con.execute(
            """SELECT a.conflict_note, f.path
               FROM assets a JOIN files f ON f.asset_id = a.id
               WHERE a.date_conflict = 1
               GROUP BY a.id ORDER BY f.path LIMIT ?""", (args.limit,)).fetchall()
        if not rows:
            print("no date conflicts")
            return 0
        print(f"date conflicts (showing up to {args.limit}):\n")
        for r in rows:
            print(f"  {r['conflict_note']}")
            print(f"      {r['path']}")
        return 0

    if args.errors:
        rows = con.execute(
            """SELECT path, stage, error, attempts FROM scan_errors
               ORDER BY updated_at DESC LIMIT ?""", (args.limit,)).fetchall()
        if not rows:
            print("no recorded errors")
            return 0
        for r in rows:
            print(f"  [{r['stage']}] {r['error']}  x{r['attempts']}")
            print(f"      {r['path']}")
        return 0

    one = lambda q, *a: con.execute(q, a).fetchone()[0]

    assets = one("SELECT COUNT(*) FROM assets")
    files = one("SELECT COUNT(*) FROM files WHERE missing_since IS NULL")
    missing = one("SELECT COUNT(*) FROM files WHERE missing_since IS NOT NULL")
    dupes = files - assets if files > assets else 0

    print(f"database   {cfg.database}")
    size = cfg.database.stat().st_size / 1e6 if cfg.database.exists() else 0
    print(f"           {size:.0f} MB")
    # Which config produced this matters: silently falling back to the defaults
    # puts the database somewhere unexpected and drops library-specific patterns.
    print(f"config     {cfg.source or 'NONE FOUND -- using built-in defaults'}\n")
    print(f"  photographs      {assets:>10,}")
    print(f"  file copies      {files:>10,}   ({dupes:,} duplicate paths)")
    if missing:
        print(f"  missing          {missing:>10,}")

    # Counted at the *current* version of each stage, not merely `done`: a
    # stage whose logic has changed is pending again for every asset (spec
    # 4.2), and a status line that called that finished would hide exactly the
    # work someone needs to be told about.
    from . import embed as embed_mod
    from . import faces as faces_mod
    versions = {"meta": pipeline.STAGE_VERSION,
                "faces": faces_mod.STAGE_VERSION,
                "embed": embed_mod.STAGE_VERSION,
                "caption": 1}
    print("\nstages")
    for stage, version in versions.items():
        done = one("SELECT COUNT(*) FROM stage_state WHERE stage=? "
                   "AND status='done' AND version >= ?", stage, version)
        stale = one("SELECT COUNT(*) FROM stage_state WHERE stage=? "
                    "AND status='done' AND version < ?", stage, version)
        pend = assets - done
        bar = "done" if pend == 0 and done else f"{pend:,} pending"
        if stale:
            bar += f"   ({stale:,} at an older version)"
        print(f"  {stage:<9} {done:>10,} done   {bar}")

    print("\ndates")
    for r in con.execute(
            """SELECT taken_at_source AS s, taken_at_precision AS p, COUNT(*) AS n
               FROM assets GROUP BY s, p ORDER BY n DESC"""):
        pct = 100 * r["n"] / assets if assets else 0
        print(f"  {r['s'] or '-':<9} {r['p'] or '-':<7} {r['n']:>10,}   {pct:5.1f}%")

    conflicts = one("SELECT COUNT(*) FROM assets WHERE date_conflict = 1")
    if conflicts:
        print(f"\n  {conflicts:,} date conflicts   `mediaidx status --conflicts`")

    span = one("SELECT MIN(taken_at) FROM assets WHERE taken_at IS NOT NULL"), \
        one("SELECT MAX(taken_at) FROM assets WHERE taken_at IS NOT NULL")
    if span[0]:
        print(f"\n  span  {datetime.fromtimestamp(span[0]):%Y-%m-%d}"
              f"  to  {datetime.fromtimestamp(span[1]):%Y-%m-%d}")

    gps = one("SELECT COUNT(*) FROM assets WHERE gps_lat IS NOT NULL")
    print(f"  gps   {gps:,} of {assets:,}"
          f"  ({100*gps/assets if assets else 0:.0f}%)")

    has_places = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='places'").fetchone()
    if has_places:
        gaz = one("SELECT COUNT(*) FROM places")
        print("\nplaces")
        if not gaz:
            print("  no gazetteer loaded   `mediaidx places --load`")
        else:
            placed = one("SELECT COUNT(*) FROM assets WHERE place_id IS NOT NULL")
            by = {r["s"]: r["n"] for r in con.execute(
                "SELECT place_source AS s, COUNT(*) AS n FROM assets "
                "WHERE place_id IS NOT NULL GROUP BY s")}
            print(f"  gazetteer        {gaz:>10,}   places")
            print(f"  placed           {placed:>10,}"
                  f"  ({100*placed/assets if assets else 0:.0f}%)"
                  f"   {by.get('gps', 0):,} by coordinate,"
                  f" {by.get('folder', 0):,} by folder")

    has_faces = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='faces'").fetchone()
    if has_faces and one("SELECT COUNT(*) FROM faces"):
        nf = one("SELECT COUNT(*) FROM faces")
        imgs = one("SELECT COUNT(DISTINCT asset_id) FROM faces")
        clusters = one("SELECT COUNT(*) FROM face_clusters")
        named = one("SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL")
        print(f"\nfaces")
        print(f"  detected         {nf:>10,}   in {imgs:,} photographs")
        if clusters:
            print(f"  clusters         {clusters:>10,}")
            print(f"  named            {named:>10,}"
                  f"  ({100*named/nf if nf else 0:.0f}% of faces)")
        else:
            print("  not clustered yet   `mediaidx faces cluster`")

    has_events = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone()
    if has_events and one("SELECT COUNT(*) FROM events"):
        nev = one("SELECT COUNT(*) FROM events")
        named = one("SELECT COUNT(*) FROM events WHERE name IS NOT NULL")
        in_ev = one("SELECT COUNT(DISTINCT asset_id) FROM event_assets")
        print("\nevents")
        print(f"  events           {nev:>10,}   {named:,} named, {nev - named:,} not")
        print(f"  photographs      {in_ev:>10,}"
              f"  ({100*in_ev/assets if assets else 0:.0f}%)")
        openq = one("SELECT COUNT(*) FROM review_queue WHERE status = 'open'")
        if openq:
            print(f"  queue            {openq:>10,}   questions"
                  "   `mediaidx review`")
    elif has_events:
        print("\nevents\n  none yet   `mediaidx derive`")

    errs = one("SELECT COUNT(*) FROM scan_errors")
    if errs:
        print(f"\n  {errs:,} errors recorded   `mediaidx status --errors`")
    return 0


# --------------------------------------------------------------------------
# embed / search / serve
# --------------------------------------------------------------------------

def cmd_embed(args) -> int:
    from . import embed as embed_mod
    from . import vision_pass
    cfg, con = _open(args, create=False)

    db.migrate(con)                      # schema v2 adds image_vectors
    todo = vision_pass.pending(con, embed_mod.STAGE_VERSION)
    if not todo:
        print("nothing pending -- every photograph already has a search vector")
        return 0
    print(f"{len(todo):,} photographs pending"
          + (f", processing {args.limit:,}" if args.limit else ""))
    print("loading model (first run downloads it) ...")

    try:
        st = vision_pass.run(con, cfg, limit=args.limit, threads=args.workers,
                             gpu_batch=args.batch,
                             progress=vision_pass.render_progress)
    except KeyboardInterrupt:
        print("\n  interrupted -- committed batches are kept; re-run to continue")
        return 130

    print(f"\n\n  embedded   {st.done:>9,}")
    print(f"  thumbnails {st.thumbs:>9,}")
    if st.failed:
        print(f"  failed     {st.failed:>9,}")
    print(f"  {st.rate:.1f} img/s over {st.elapsed:.0f}s")
    return 0


def cmd_faces(args) -> int:
    if args.action == "cluster":
        return cmd_faces_cluster(args)

    from . import face_pass
    from . import faces as faces_mod
    cfg, con = _open(args, create=False)

    db.migrate(con)                      # schema v3 adds faces, persons, clusters
    todo = face_pass.pending(con, faces_mod.STAGE_VERSION)
    if not todo:
        print("nothing pending -- every photograph has been searched for faces")
        return 0
    print(f"{len(todo):,} photographs pending"
          + (f", processing {args.limit:,}" if args.limit else ""))

    if not args.cpu and not faces_mod.cuda_available():
        print("\n  the CUDA execution provider is unavailable -- this would run on the\n"
              "  CPU, roughly seven times slower. `mediaidx doctor` says why.\n"
              "  Pass --cpu to run it anyway.", file=sys.stderr)
        return 1

    print("loading model (first run downloads it) ...")
    st = None
    try:
        st = face_pass.run(con, cfg, limit=args.limit, threads=args.workers,
                           progress=face_pass.render_progress,
                           device="cpu" if args.cpu else "cuda")
    except KeyboardInterrupt:
        print("\n  interrupted -- committed chunks are kept; re-run to continue")
        return 130

    print(f"\n\n  photographs {st.done:>9,}")
    print(f"  faces       {st.faces:>9,}   in {st.with_faces:,} photographs")
    if st.thumbs:
        print(f"  thumbnails  {st.thumbs:>9,}")
    if st.failed:
        print(f"  failed      {st.failed:>9,}   `mediaidx status --errors`")
    print(f"  {st.rate:.1f} img/s over {st.elapsed:.0f}s")
    return 0


def cmd_faces_cluster(args) -> int:
    from . import cluster
    cfg, con = _open(args, create=False)
    db.migrate(con)

    n = con.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
    if n == 0:
        print("no faces yet -- run `mediaidx faces` first", file=sys.stderr)
        return 1

    existing = con.execute("SELECT COUNT(*) FROM face_clusters").fetchone()[0]
    if existing and not args.rebuild:
        # The cheap path: only the faces that arrived since the last build,
        # matched against the centroids that already exist.
        moved = cluster.assign(con, cfg)
        left = con.execute(
            "SELECT COUNT(*) FROM faces WHERE cluster_id IS NULL").fetchone()[0]
        print(f"  assigned {moved:,} new faces to existing clusters")
        print(f"  {left:,} still unclustered   "
              "`mediaidx faces cluster --rebuild` to recompute from scratch")
        return 0

    print(f"clustering {n:,} faces "
          + ("(rebuild -- names are preserved by majority vote)" if args.rebuild
             else "for the first time"))
    print("  this is CPU work over the database; no file is read")
    st = cluster.build(con, cfg, rebuild=args.rebuild)

    print(f"\n  clusters    {st.clusters:>9,}")
    print(f"  clustered   {st.clustered:>9,}   "
          f"({100*st.clustered/st.faces if st.faces else 0:.0f}% of faces)")
    print(f"  unclustered {st.noise:>9,}   left as noise, not forced")
    if st.named:
        print(f"  names kept  {st.named:>9,}")
    print(f"  {st.elapsed:.0f}s")
    print("\n  `mediaidx serve`, then People, to name them")
    return 0


def cmd_meta(args) -> int:
    from . import pipeline
    cfg, con = _open(args, create=False)
    db.migrate(con)

    todo = pipeline.pending_meta(con)
    if not todo:
        print("nothing pending -- every photograph's metadata is current")
        return 0
    print(f"{len(todo):,} photographs whose metadata predates the current "
          f"stage version")
    print("  headers only -- nothing is hashed and no image is decoded")
    try:
        st = pipeline.refresh_meta(con, cfg, limit=args.limit,
                                   progress=pipeline.render_meta_progress)
    except KeyboardInterrupt:
        print("\n  interrupted -- committed chunks are kept; re-run to continue")
        return 130

    print(f"\n\n  re-read          {st.updated:>10,}")
    if st.moved:
        print(f"  coordinates moved{st.moved:>10,}   "
              "`mediaidx places` to put them back on the map")
    if st.failed:
        print(f"  failed           {st.failed:>10,}   `mediaidx status --errors`")
    print(f"  {st.rate:.1f} files/s over {st.elapsed:.0f}s")
    return 0


def cmd_places(args) -> int:
    from . import places
    cfg, con = _open(args, create=False)
    db.migrate(con)

    if args.rejected:
        rows = con.execute(
            "SELECT label_norm, place_path FROM place_exceptions "
            "ORDER BY label_norm").fetchall()
        if not rows:
            print("nothing rejected")
            return 0
        for r in rows:
            print(f"  {r['label_norm']:<28} "
                  f"{r['place_path'] or 'never a place at all'}")
        return 0

    loaded = places.state(con)
    if args.load or loaded is None:
        gaz = cfg.gazetteer
        if not gaz.exists():
            print(f"no gazetteer at {gaz}\n\n"
                  "  Download these four files from "
                  "https://download.geonames.org/export/dump/ into that folder:\n"
                  "    cities500.zip  admin1CodesASCII.txt  admin2Codes.txt  "
                  "countryInfo.txt", file=sys.stderr)
            return 1
        print(f"loading the gazetteer from {gaz}")
        g = places.load_gazetteer(con, gaz)
        print(f"  countries   {g.countries:>9,}")
        print(f"  regions     {g.admin1 + g.admin2:>9,}")
        print(f"  localities  {g.localities:>9,}")
        print(f"  names       {g.names:>9,}   {g.collisions:,} dropped as "
              "same-named siblings")
        print(f"  {g.elapsed:.0f}s\n")

    st = places.run(con, cfg, progress=None)
    con.commit()

    total = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    print(f"  by coordinate    {st.gps_placed:>10,}   "
          f"from {st.bins:,} distinct 1 km bins")
    if st.gps_unplaced:
        print(f"  coordinate lost  {st.gps_unplaced:>10,}   "
              "nearest place is too far away to name")
    print(f"  by folder label  {st.folder_placed:>10,}   "
          f"{st.labels_matched:,} of {st.labels:,} labels matched")
    print(f"  placed           {st.placed:>10,}   "
          f"({100 * st.placed / total if total else 0:.0f}% of {total:,})")
    print(f"  {st.elapsed:.0f}s")

    rows = con.execute(
        """SELECT fp.label, p.path, fp.candidates FROM folder_places fp
             JOIN places p ON p.id = fp.place_id
            GROUP BY fp.label ORDER BY fp.candidates DESC, fp.label LIMIT ?""",
        (args.limit,)).fetchall()
    if rows:
        # Every folder match is a guess made from a word, so they are printed
        # rather than merely counted -- ambiguous ones first.
        print("\nfolder labels resolved (most ambiguous first)")
        for r in rows:
            note = f"   1 of {r['candidates']}" if r["candidates"] > 1 else ""
            print(f"  {r['label']:<26} {r['path']}{note}")
        print("\n  wrong ones: `mediaidx serve`, then Places, to reject one")
    return 0


def cmd_derive(args) -> int:
    """Everything the database can work out about itself, in one pass.

    Places, then events, then the queue -- in that order because an event asks
    where its photographs were, and the queue asks both. None of it reads a
    file, so this is seconds of work and safe to repeat whenever a threshold
    changes.
    """
    from . import events as events_mod
    from . import places as places_mod
    from . import queue as queue_mod
    cfg, con = _open(args, create=False)
    db.migrate(con)

    if not args.no_places:
        if places_mod.state(con) is None:
            print("no gazetteer loaded -- `mediaidx places --load` first; "
                  "carrying on without places", file=sys.stderr)
        else:
            p = places_mod.run(con, cfg)
            con.commit()
            print(f"places     {p.placed:>10,} placed   "
                  f"{p.gps_placed:,} by coordinate, {p.folder_placed:,} by folder"
                  f"   {p.elapsed:.0f}s")

    e = events_mod.run(con, cfg)
    print(f"events     {e.events:>10,}          "
          f"{e.from_folder:,} from folder names, {e.from_time:,} from the clock"
          f"   {e.elapsed:.0f}s")
    print(f"           {e.assets:>10,} photographs in an event"
          f"   ({e.ungrouped:,} in runs too short to be one)")
    print(f"           {e.named:>10,} named, {e.unnamed:,} not")

    q = queue_mod.build(con, cfg)
    print(f"queue      {q.total:>10,} questions   "
          f"{q.events:,} events, {q.clusters:,} face clusters"
          f"   {q.suggested:,} with a suggestion")
    print(f"           {q.photographs:>10,} photographs would be labelled by "
          "answering them")
    print("\n  `mediaidx review` to work through it")
    return 0


def cmd_events(args) -> int:
    from . import events as events_mod
    cfg, con = _open(args, create=False)
    db.migrate(con)                      # schema v5 adds the events tables
    rows = events_mod.overview(con, limit=args.limit,
                               unnamed_only=args.unnamed)
    if not rows:
        print("no events yet -- `mediaidx derive`")
        return 0
    total = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    named = con.execute(
        "SELECT COUNT(*) FROM events WHERE name IS NOT NULL").fetchone()[0]
    for r in rows:
        when = datetime.fromtimestamp(r["start_at"]).strftime("%Y-%m-%d")
        name = r["name"] or "-"
        where = r["place"] or ""
        src = {"folder": "folder", "user": "named"}.get(r["name_source"], "")
        print(f"  {when}  {r['asset_count']:>6,}  {name:<34.34} "
              f"{where:<18.18} {src}")
    print(f"\n  {total:,} events, {named:,} named, {total - named:,} not")
    return 0


def cmd_review(args) -> int:
    """Open the queue in the browser, which is where it is answered."""
    from . import queue as queue_mod
    from . import serve
    cfg, con = _open(args, create=False)
    db.migrate(con)                      # schema v5 adds the queue
    c = queue_mod.counts(con)
    if not c["total"]:
        print("nothing in the queue -- `mediaidx derive` builds it")
        return 0
    print(f"  {c['open']:,} open questions   "
          f"{c['events']:,} events, {c['clusters']:,} face clusters")
    print(f"  {c['photographs']:,} photographs would be labelled by answering them")
    con.close()
    url = f"http://{args.host}:{args.port}/review"
    print(f"\n  {url}\n  ctrl-c to stop")
    if not args.no_open:
        import threading
        import webbrowser
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    serve.run(cfg, host=args.host, port=args.port)
    return 0


def cmd_people(args) -> int:
    cfg, con = _open(args, create=False)
    rows = con.execute(
        """SELECT p.name, COUNT(DISTINCT c.id) AS clusters,
                  COUNT(f.id) AS faces,
                  COUNT(DISTINCT f.asset_id) AS photos
           FROM persons p
           LEFT JOIN face_clusters c ON c.person_id = p.id
           LEFT JOIN faces f ON f.person_id = p.id
           GROUP BY p.id ORDER BY photos DESC""").fetchall()
    if not rows:
        print("nobody named yet -- `mediaidx serve`, then People")
        return 0
    unnamed = con.execute(
        "SELECT COUNT(*) FROM face_clusters WHERE person_id IS NULL "
        "AND status != 'ignored'").fetchone()[0]
    for r in rows:
        print(f"  {r['photos']:>7,} photographs  {r['faces']:>7,} faces  "
              f"{r['clusters']:>3} clusters  {r['name']}")
    if unnamed:
        print(f"\n  {unnamed:,} clusters still unnamed")
    return 0


def cmd_search(args) -> int:
    from . import embed as embed_mod
    cfg, con = _open(args, create=False)
    vec_ok = embed_mod.load_vec_extension(con)
    n = con.execute("SELECT COUNT(*) FROM image_vectors").fetchone()[0]
    if n == 0:
        print("no search vectors yet -- run `mediaidx embed`", file=sys.stderr)
        return 1

    query = " ".join(args.query)
    enc = embed_mod.Encoder(
        model_name=cfg.scan.get("embed_model", embed_mod.DEFAULT_MODEL),
        pretrained=cfg.scan.get("embed_pretrained", embed_mod.DEFAULT_PRETRAINED))
    hits = embed_mod.search(con, enc.encode_text([query])[0],
                            limit=args.limit, vec_ok=vec_ok)
    print(f'\n"{query}"   {n:,} searchable\n')
    for asset_id, score in hits:
        r = con.execute(
            """SELECT a.taken_at, f.path FROM assets a
               JOIN files f ON f.asset_id = a.id WHERE a.id = ? LIMIT 1""",
            (asset_id,)).fetchone()
        when = (datetime.fromtimestamp(r["taken_at"]).strftime("%Y-%m-%d")
                if r["taken_at"] else "----------")
        print(f"  {score:.3f}  {when}  {os.path.basename(r['path'])}")
    return 0


def cmd_serve(args) -> int:
    from . import serve
    cfg = config_mod.load(args.config)
    if not cfg.database.exists():
        print("no database yet -- run `mediaidx add <folder>` first", file=sys.stderr)
        return 1
    print(f"  http://{args.host}:{args.port}\n  ctrl-c to stop")
    serve.run(cfg, host=args.host, port=args.port)
    return 0


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

def cmd_doctor(args) -> int:
    cfg = config_mod.load(args.config)
    ok = True

    def check(label, good, detail=""):
        nonlocal ok
        mark = "OK  " if good else "FAIL"
        if not good:
            ok = False
        print(f"  [{mark}] {label}" + (f"   {detail}" if detail else ""))

    print("environment")
    v = sys.version_info
    check("python", v >= (3, 11), f"{v.major}.{v.minor}.{v.micro}")
    check("sqlite", sqlite3.sqlite_version_info >= (3, 35), sqlite3.sqlite_version)

    exe = exif.find_exiftool(cfg.tools.get("exiftool", ""))
    check("exiftool", exe is not None, exe or "not found on PATH")

    if exe:
        # The cp1252 trap: this must survive a non-ASCII filename (spec §5.1).
        import tempfile
        try:
            with tempfile.TemporaryDirectory() as tmp:
                probe = Path(tmp) / "hyvää-yötä-ääkkönen.jpg"
                probe.write_bytes(bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9"))
                with exif.Exif(exe) as et:
                    got = et.batch([str(probe)])
                check("exiftool utf-8 round-trip", bool(got),
                      "non-ASCII filename decoded")
        except Exception as e:
            check("exiftool utf-8 round-trip", False, str(e)[:80])

    print("\nface stage")
    try:
        from . import faces as faces_mod
        import onnxruntime as ort
        faces_mod.preload_cuda()
        check("onnxruntime", True, ort.__version__)

        model_dir = Path(os.path.expanduser("~/.insightface/models")) / \
            cfg.models.get("face", faces_mod.DEFAULT_MODEL)
        have = model_dir.is_dir() and any(model_dir.glob("*.onnx"))
        print(f"  [{'OK  ' if have else 'INFO'}] face model"
              f"   {'cached at ' + str(model_dir) if have else 'not yet, first `faces` run will fetch it'}")

        # Whether the provider *lists* is not the question -- it lists even when
        # its CUDA libraries are missing, and then quietly runs on the CPU seven
        # times slower. The only honest test is to build a session and ask what
        # it actually got.
        if have:
            det = model_dir / "det_10g.onnx"
            s = ort.InferenceSession(str(det), providers=["CUDAExecutionProvider",
                                                          "CPUExecutionProvider"])
            got = s.get_providers()[0]
            check("CUDA execution provider", got == "CUDAExecutionProvider",
                  got if got == "CUDAExecutionProvider"
                  else "falling back to the CPU -- install onnxruntime-gpu[cuda,cudnn]")
        else:
            print("  [INFO] CUDA execution provider   not testable until the model is cached")
    except ImportError as e:
        print(f"  [INFO] face stack not installed   {e}")
    except Exception as e:
        check("face stage", False, str(e)[:100])

    print("\nprivacy")
    offline = os.environ.get("HF_HUB_OFFLINE") in ("1", "true", "TRUE")
    hf_cache = Path(os.path.expanduser("~/.cache/huggingface/hub"))
    cached = hf_cache.exists() and any(hf_cache.iterdir())
    print(f"  [INFO] photographs are never transmitted; all inference is local")
    print(f"  [{'OK  ' if cached else 'INFO'}] model weights cached"
          f"   {'yes -- no download needed' if cached else 'not yet, first embed will fetch them'}")
    print(f"  [{'OK  ' if offline else 'INFO'}] HF_HUB_OFFLINE"
          f"   {'set -- network access disabled' if offline else 'unset; set it to 1 to enforce offline once weights are cached'}")

    print("\nstorage")
    sync = pathutil.is_in_sync_folder(str(cfg.database))
    check("database outside sync folder", sync is None,
          f"inside {sync} -- WAL corruption risk, see spec 10.5" if sync
          else str(cfg.database.parent))
    sync_t = pathutil.is_in_sync_folder(str(cfg.thumbnails))
    check("thumbnails outside sync folder", sync_t is None, sync_t or "")

    try:
        cfg.database.parent.mkdir(parents=True, exist_ok=True)
        con = db.connect(cfg.database)
        db.migrate(con)
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        check("database integrity", integrity == "ok", integrity)
        jm = con.execute("PRAGMA journal_mode").fetchone()[0]
        check("journal mode", jm.lower() == "wal", jm)
    except Exception as e:
        check("database", False, str(e)[:80])

    print("\nroots")
    try:
        con = db.connect(cfg.database, create=False)
        rows = con.execute("SELECT path FROM folders WHERE is_root=1").fetchall()
        if not rows:
            print("  (none recorded yet)")
        for r in rows:
            p = pathutil.long_path(r["path"])
            check(r["path"], os.path.isdir(p),
                  "" if os.path.isdir(p) else "unreachable right now")
    except Exception:
        print("  (no database yet)")

    print("\n" + ("all good" if ok else "problems found -- see FAIL lines above"))
    return 0 if ok else 1


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mediaidx",
        description="Local, private index over a photo archive. "
                    "Reads only the folders you give it; never writes to them.")
    p.add_argument("--config", help="path to config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="index the given folders")
    a.add_argument("paths", nargs="+")
    a.add_argument("--no-recurse", action="store_true")
    a.add_argument("--label", help="attach a label to these folders")
    a.add_argument("--person", help="these folders mostly depict this person")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--workers", type=int)
    a.set_defaults(func=cmd_add)

    s = sub.add_parser("scan", help="re-scan known roots for changes")
    s.add_argument("paths", nargs="*")
    s.add_argument("--workers", type=int)
    s.set_defaults(func=cmd_scan)

    r = sub.add_parser("roots", help="list or forget indexed roots")
    r.add_argument("forget", nargs="?", help="stop re-scanning this root")
    r.set_defaults(func=cmd_roots)

    st = sub.add_parser("status", help="what is indexed and what is pending")
    st.add_argument("--conflicts", action="store_true")
    st.add_argument("--errors", action="store_true")
    st.add_argument("--limit", type=int, default=25)
    st.set_defaults(func=cmd_status)

    e = sub.add_parser("embed", help="build search vectors and thumbnails")
    e.add_argument("--limit", type=int, help="stop after N photographs")
    e.add_argument("--workers", type=int, default=8)
    e.add_argument("--batch", type=int, default=32, help="GPU batch size")
    e.set_defaults(func=cmd_embed)

    fa = sub.add_parser("faces", help="detect and embed faces; `faces cluster` groups them")
    fa.add_argument("action", nargs="?", choices=["cluster"],
                    help="'cluster' to group faces into people; omit to detect")
    fa.add_argument("--limit", type=int, help="stop after N photographs")
    fa.add_argument("--workers", type=int, default=8)
    fa.add_argument("--cpu", action="store_true",
                    help="run without the GPU; correct, but far slower")
    fa.add_argument("--rebuild", action="store_true",
                    help="with `cluster`: recompute from scratch, preserving names")
    fa.set_defaults(func=cmd_faces)

    m = sub.add_parser("meta", help="re-read metadata for photographs whose stage is behind")
    m.add_argument("--limit", type=int, default=None)
    m.set_defaults(func=cmd_meta)

    pl = sub.add_parser("places", help="put photographs on the map, by coordinate and by folder")
    pl.add_argument("--load", action="store_true",
                    help="rebuild the gazetteer from the extract before matching")
    pl.add_argument("--rejected", action="store_true",
                    help="list the folder-label matches that were thrown out")
    pl.add_argument("--limit", type=int, default=40)
    pl.set_defaults(func=cmd_places)

    dv = sub.add_parser("derive", help="rebuild places, events and the review queue")
    dv.add_argument("--no-places", action="store_true",
                    help="leave the places alone; rebuild only events and the queue")
    dv.set_defaults(func=cmd_derive)

    evp = sub.add_parser("events", help="what the archive says happened, and when")
    evp.add_argument("--unnamed", action="store_true",
                     help="only the ones with no name")
    evp.add_argument("--limit", type=int, default=60)
    evp.set_defaults(func=cmd_events)

    rv = sub.add_parser("review", help="work through the questions, largest first")
    rv.add_argument("--host", default="127.0.0.1")
    rv.add_argument("--port", type=int, default=8710)
    rv.add_argument("--no-open", action="store_true",
                    help="print the address instead of opening a browser")
    rv.set_defaults(func=cmd_review)

    pe = sub.add_parser("people", help="who has been named, and how much of the library")
    pe.set_defaults(func=cmd_people)

    q = sub.add_parser("search", help="search from the terminal")
    q.add_argument("query", nargs="+")
    q.add_argument("--limit", type=int, default=10)
    q.set_defaults(func=cmd_search)

    sv = sub.add_parser("serve", help="open the local search UI")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8710)
    sv.set_defaults(func=cmd_serve)

    d = sub.add_parser("doctor", help="check the environment")
    d.set_defaults(func=cmd_doctor)

    return p


def main(argv=None) -> int:
    # Windows gives a redirected stdout the ANSI codepage, and cp1252 cannot
    # encode most of the gazetteer: printing `Ρόδος` or `Côte d'Azur` raises
    # UnicodeEncodeError and takes the whole command down *after* the work is
    # done. Everything stored is UTF-8, so everything written is too.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass                        # already wrapped, or not a real stream

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
