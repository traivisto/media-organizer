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

    print("\nstages")
    for stage in ("meta", "faces", "embed", "caption"):
        done = one("SELECT COUNT(*) FROM stage_state WHERE stage=? AND status='done'", stage)
        pend = assets - done
        bar = "done" if pend == 0 and done else f"{pend:,} pending"
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
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
