"""The local search UI.

Server-rendered, no build step, no framework on the client. The page is scanned
and operated rather than read, so the grid is the interface and everything else
stays out of its way.

Nothing here runs a model except the query encoder, which turns typed text into
one vector. Browsing touches only photos.db and the thumbnail cache.
"""

from __future__ import annotations

import html
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, HTMLResponse, Response

from . import config as config_mod
from . import db, embed, images
from . import paths as pathutil

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root {{
  --ground:#f2f3f0; --surface:#fafbf8; --ink:#161a19; --ink-2:#4e5653;
  --ink-3:#7b8480; --rule:#d2d7d1; --accent:#2c5c69;
}}
@media (prefers-color-scheme:dark) {{
  :root {{
    --ground:#121514; --surface:#191d1c; --ink:#e7eae6; --ink-2:#a2aca8;
    --ink-3:#79837f; --rule:#2a302e; --accent:#7fbac7;
  }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--ground); color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
header {{ position:sticky; top:0; z-index:5; background:var(--ground);
  border-bottom:1px solid var(--rule); padding:14px 20px; }}
form {{ display:flex; gap:10px; align-items:center; max-width:1500px; margin:0 auto; }}
input[type=search] {{ flex:1; padding:10px 14px; font-size:16px; color:var(--ink);
  background:var(--surface); border:1px solid var(--rule); border-radius:3px; }}
input[type=search]:focus {{ outline:2px solid var(--accent); outline-offset:-1px; }}
button {{ padding:10px 18px; font-size:15px; font-weight:600; cursor:pointer;
  background:var(--accent); color:var(--ground); border:0; border-radius:3px; }}
.meta {{ max-width:1500px; margin:0 auto; padding:10px 20px 0; color:var(--ink-3);
  font-size:13px; }}
.grid {{ max-width:1500px; margin:0 auto; padding:14px 20px 60px;
  display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:10px; }}
figure {{ margin:0; background:var(--surface); border:1px solid var(--rule);
  border-radius:3px; overflow:hidden; }}
figure img {{ display:block; width:100%; aspect-ratio:1; object-fit:cover;
  background:var(--rule); }}
figcaption {{ padding:7px 9px; font-size:11.5px; color:var(--ink-3);
  display:flex; justify-content:space-between; gap:8px; }}
figcaption b {{ color:var(--ink-2); font-weight:500; }}
.empty {{ max-width:640px; margin:70px auto; text-align:center; color:var(--ink-3); }}
.empty code {{ background:var(--surface); padding:2px 6px; border-radius:3px; }}
a {{ color:inherit; text-decoration:none; }}
</style></head><body>
<header><form action="/" method="get">
  <input type="search" name="q" value="{q}" placeholder="{placeholder}" autofocus>
  <button type="submit">Search</button>
</form></header>
{body}
</body></html>"""


def create_app(cfg) -> FastAPI:
    app = FastAPI(title="Kuva-arkisto")
    cache_dir = Path(cfg.thumbnails)

    state: dict = {"encoder": None}

    def connect() -> sqlite3.Connection:
        con = db.connect(cfg.database, create=False)
        con.row_factory = sqlite3.Row
        state["vec_ok"] = embed.load_vec_extension(con)
        return con

    def encoder():
        if state["encoder"] is None:
            state["encoder"] = embed.Encoder(
                model_name=cfg.scan.get("embed_model", embed.DEFAULT_MODEL),
                pretrained=cfg.scan.get("embed_pretrained", embed.DEFAULT_PRETRAINED),
            )
        return state["encoder"]

    def card(row, score=None) -> str:
        when = ""
        if row["taken_at"]:
            dt = datetime.fromtimestamp(row["taken_at"])
            p = row["taken_at_precision"]
            # No %-d / %#d: those are platform-specific. Build the day by hand.
            when = (f"{dt:%Y}" if p == "year" else
                    f"{dt:%b %Y}" if p == "month" else
                    f"{dt.day} {dt:%b %Y}")
        right = f"{score:.2f}" if score is not None else (row["camera_model"] or "")
        return (
            f'<figure><a href="/photo/{row["id"]}">'
            f'<img loading="lazy" src="/thumb/s/{row["content_hash"]}" alt="">'
            f'<figcaption><b>{html.escape(when)}</b>'
            f'<span>{html.escape(str(right)[:18])}</span></figcaption></a></figure>')

    @app.get("/", response_class=HTMLResponse)
    def home(q: str = Query(default=""), limit: int = 120):
        con = connect()
        total = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        embedded = con.execute(
            "SELECT COUNT(*) FROM image_vectors").fetchone()[0]

        if not q:
            rows = con.execute(
                """SELECT id, content_hash, taken_at, taken_at_precision, camera_model
                   FROM assets ORDER BY taken_at DESC LIMIT ?""", (limit,)).fetchall()
            meta = (f"{total:,} photographs &middot; {embedded:,} searchable"
                    "  &middot;  most recent first")
            body = f'<div class="meta">{meta}</div><div class="grid">' + \
                   "".join(card(r) for r in rows) + "</div>"
            if not rows:
                body = ('<div class="empty"><p>Nothing indexed yet.</p>'
                        '<p><code>mediaidx add &lt;folder&gt;</code></p></div>')
            return HTMLResponse(PAGE.format(
                title="Kuva-arkisto", q="", body=body,
                placeholder="kids on a beach at sunset"))

        if embedded == 0:
            body = ('<div class="empty"><p>No search vectors yet.</p>'
                    '<p><code>mediaidx embed</code></p></div>')
            return HTMLResponse(PAGE.format(
                title=f"{q} — Kuva-arkisto", q=html.escape(q), body=body,
                placeholder="kids on a beach at sunset"))

        vec = encoder().encode_text([q])[0]
        hits = embed.search(con, vec, limit=limit, vec_ok=state["vec_ok"])
        if not hits:
            body = '<div class="empty"><p>No matches.</p></div>'
        else:
            ids = [h[0] for h in hits]
            scores = dict(hits)
            rows = con.execute(
                f"""SELECT id, content_hash, taken_at, taken_at_precision, camera_model
                    FROM assets WHERE id IN ({','.join('?' * len(ids))})""",
                ids).fetchall()
            by_id = {r["id"]: r for r in rows}
            cards = "".join(card(by_id[i], scores[i]) for i in ids if i in by_id)
            body = (f'<div class="meta">{len(ids)} matches for '
                    f'&ldquo;{html.escape(q)}&rdquo; of {embedded:,} searchable'
                    f'</div><div class="grid">{cards}</div>')
        return HTMLResponse(PAGE.format(
            title=f"{q} — Kuva-arkisto", q=html.escape(q), body=body,
            placeholder="kids on a beach at sunset"))

    @app.get("/thumb/{size}/{content_hash}")
    def thumb(size: str, content_hash: str):
        if size not in images.THUMB_SIZES:
            return Response(status_code=404)
        p = images.thumb_path(cache_dir, content_hash, size)
        if not p.exists():
            return Response(status_code=404)
        return FileResponse(p, media_type="image/webp",
                            headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/photo/{asset_id}", response_class=HTMLResponse)
    def photo(asset_id: int):
        con = connect()
        a = con.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if a is None:
            return HTMLResponse("<p>not found</p>", status_code=404)
        files = con.execute(
            "SELECT path FROM files WHERE asset_id = ? ORDER BY path",
            (asset_id,)).fetchall()

        def row(k, v):
            return (f'<tr><td style="color:var(--ink-3);padding:3px 16px 3px 0">{k}</td>'
                    f'<td>{html.escape(str(v))}</td></tr>') if v not in (None, "") else ""

        when = "unknown"
        if a["taken_at"]:
            _d = datetime.fromtimestamp(a["taken_at"])
            when = f"{_d.day} {_d:%B %Y}, {_d:%H:%M}"
        gps = (f'{a["gps_lat"]:.5f}, {a["gps_lon"]:.5f}'
               if a["gps_lat"] is not None else "")
        info = "".join([
            row("taken", f'{when}  ({a["taken_at_source"]}, {a["taken_at_precision"]})'),
            row("camera", " ".join(filter(None, [a["camera_make"], a["camera_model"]]))),
            row("size", f'{a["width"]}x{a["height"]}' if a["width"] else ""),
            row("gps", gps),
            row("source", a["source_hint"]),
            row("conflict", a["conflict_note"]),
            row("hash", a["content_hash"][:24] + "..."),
        ])
        paths = "".join(
            f'<div style="color:var(--ink-3);font-size:12px;'
            f'font-family:ui-monospace,monospace;padding:1px 0">'
            f'{html.escape(f["path"])}</div>' for f in files)
        body = (
            f'<div style="max-width:1100px;margin:0 auto;padding:20px">'
            f'<a href="/" style="color:var(--accent)">&larr; back</a>'
            f'<img src="/thumb/m/{a["content_hash"]}" '
            f'style="display:block;max-width:100%;margin:14px 0;border-radius:3px">'
            f'<table style="font-size:14px">{info}</table>'
            f'<div style="margin-top:14px">{paths}</div></div>')
        return HTMLResponse(PAGE.format(
            title=f"{when} — Kuva-arkisto", q="", body=body,
            placeholder="kids on a beach at sunset"))

    return app


def run(cfg, host: str = "127.0.0.1", port: int = 8710) -> None:
    import uvicorn
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")
