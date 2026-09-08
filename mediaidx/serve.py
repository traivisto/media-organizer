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

from fastapi import FastAPI, Form, Query
from fastapi.responses import (FileResponse, HTMLResponse, RedirectResponse,
                               Response)

from . import cluster
from . import config as config_mod
from . import db, embed, images
from . import events as events_mod
from . import paths as pathutil
from . import places as places_mod
from . import queue as queue_mod

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
nav {{ max-width:1500px; margin:8px auto 0; display:flex; gap:16px;
  font-size:13px; color:var(--ink-3); }}
nav a:hover {{ color:var(--accent); }}
/* The header is sticky, so focusing the search box makes the browser scroll it
   clear -- taking the first line of the page under the header with it. The
   search pages want the caret; every other page wants its own first line, so
   the attribute is filled in per page rather than baked in here. */
/* Faces are square, small and scanned in bulk: a tighter grid than the photo
   one, because recognising a person takes a glance, not a look. */
.faces {{ max-width:1500px; margin:0 auto; padding:14px 20px 60px;
  display:grid; grid-template-columns:repeat(auto-fill,minmax(132px,1fr)); gap:10px; }}
.faces figure img {{ aspect-ratio:1; }}
.chip {{ display:inline-block; padding:2px 8px; border:1px solid var(--rule);
  border-radius:11px; font-size:12px; color:var(--ink-2); background:var(--surface); }}
.panel {{ max-width:1500px; margin:0 auto; padding:14px 20px 0;
  display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
.panel input[type=text] {{ padding:8px 12px; font-size:15px; color:var(--ink);
  background:var(--surface); border:1px solid var(--rule); border-radius:3px; }}
.panel form {{ display:flex; gap:8px; align-items:center; margin:0; }}
.people {{ max-width:1500px; margin:0 auto; padding:6px 20px 0;
  display:flex; gap:10px; flex-wrap:wrap; }}
.person {{ display:flex; gap:10px; align-items:center; padding:8px 14px 8px 8px;
  background:var(--surface); border:1px solid var(--rule); border-radius:30px;
  font-size:13px; color:var(--ink-2); }}
.person:hover {{ border-color:var(--accent); }}
.person img {{ width:44px; height:44px; border-radius:50%; object-fit:cover;
  background:var(--rule); }}
.person b {{ color:var(--ink); font-size:14.5px; }}
.ghost {{ background:transparent; color:var(--ink-2); border:1px solid var(--rule); }}
.cands {{ max-width:1500px; margin:0 auto; padding:8px 20px 0; display:flex;
  gap:10px; flex-wrap:wrap; }}
.cand {{ display:flex; gap:9px; align-items:center; margin:0; padding:8px;
  background:var(--surface); border:1px solid var(--rule); border-radius:4px;
  font-size:12px; color:var(--ink-3); }}
.cand img {{ width:56px; height:56px; border-radius:3px; object-fit:cover;
  background:var(--rule); }}
.cand button {{ padding:6px 10px; font-size:12px; }}
/* A place list is read down the column, not scanned like a grid of faces:
   one line each, the count where the eye can compare them. */
.rows {{ max-width:1500px; margin:0 auto; padding:8px 20px 0; }}
.rows a, .rows div.row {{ display:flex; gap:14px; align-items:baseline;
  padding:7px 10px; border-bottom:1px solid var(--rule); }}
.rows a:hover {{ background:var(--surface); }}
.rows b {{ flex:1; font-weight:500; color:var(--ink); }}
.rows span {{ color:var(--ink-3); font-size:12.5px; }}
.rows form {{ display:inline; margin:0; }}
.rows form button {{ padding:3px 9px; font-size:11.5px; }}
.crumb {{ max-width:1500px; margin:0 auto; padding:12px 20px 0; font-size:13px;
  color:var(--ink-3); }}
.crumb a {{ color:var(--accent); }}
</style></head><body>
<header><form action="/" method="get">
  <input type="search" name="q" value="{q}" placeholder="{placeholder}"{focus}>
  <button type="submit">Search</button>
</form>
<nav><a href="/">Photographs</a><a href="/people">People</a><a href="/places">Places</a><a href="/events">Events</a><a href="/review">Review</a></nav></header>
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

    def people_matching(con, q: str) -> str:
        """Named people whose name contains the query.

        The vector search answers "what is in the photograph", and a person's
        name is not in the photograph -- SigLIP has never been told what Anski
        looks like. So a typed name has to be matched against the names people
        have actually given, before the query goes anywhere near a model.
        Both results are shown: "Anski" finds the person, "Anski beach" still
        ranks beaches, and neither hides the other.
        """
        needle = q.strip().casefold()
        if not needle:
            return ""
        # Matched in Python, not with LIKE ... COLLATE NOCASE: SQLite's NOCASE
        # folds ASCII A-Z and nothing else, so on this library "ÄÄKKÖNEN" would
        # not find "Ääkkönen" and "ökö" would not find "Ökö". casefold() knows
        # what those letters are. There are tens of people, not thousands, so
        # reading them all and filtering here costs nothing.
        rows = [r for r in con.execute(
            """SELECT p.id, p.name,
                      COUNT(DISTINCT f.asset_id) AS photos,
                      (SELECT a.content_hash FROM faces x
                         JOIN assets a ON a.id = x.asset_id
                        WHERE x.person_id = p.id
                        ORDER BY x.blur_score DESC LIMIT 1) AS cover_hash,
                      (SELECT x.idx FROM faces x WHERE x.person_id = p.id
                        ORDER BY x.blur_score DESC LIMIT 1) AS cover_idx
               FROM persons p JOIN faces f ON f.person_id = p.id
               GROUP BY p.id ORDER BY photos DESC""").fetchall()
            if needle in r["name"].casefold()][:12]
        if not rows:
            return ""
        chips = "".join(
            f'<a href="/person/{r["id"]}" class="person">'
            f'<img src="/face/{r["cover_hash"]}/{r["cover_idx"]}" alt="">'
            f'<span><b>{html.escape(r["name"])}</b><br>'
            f'{r["photos"]:,} photographs</span></a>' for r in rows
            if r["cover_hash"])
        return f'<div class="meta">People</div><div class="people">{chips}</div>'

    def _like(prefix: str) -> str:
        """A place name may contain % or _ -- Wilkes-Barre/Scranton does not,
        but nothing says one cannot."""
        return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def subtree_counts(con) -> dict:
        """photographs in each place *and everything under it*.

        Counted upwards from the places actually used, so this walks a few
        thousand rows rather than the gazetteer's 285,000.
        """
        rows = con.execute(
            """WITH RECURSIVE up(id, n) AS (
                 SELECT place_id, COUNT(*) FROM assets
                  WHERE place_id IS NOT NULL GROUP BY place_id
                 UNION ALL
                 SELECT p.parent_id, up.n FROM up JOIN places p ON p.id = up.id
                  WHERE p.parent_id IS NOT NULL)
               SELECT id, SUM(n) AS n FROM up GROUP BY id""").fetchall()
        return {r["id"]: r["n"] for r in rows}

    def place_photos(con, path: str, limit: int):
        esc = _like(path)
        return con.execute(
            """SELECT a.id, a.content_hash, a.taken_at, a.taken_at_precision,
                      a.camera_model
                 FROM assets a JOIN places p ON p.id = a.place_id
                WHERE p.path = ? OR p.path LIKE ? ESCAPE '\\'
                ORDER BY a.taken_at DESC LIMIT ?""",
            (path, esc + "/%", limit)).fetchall()

    def places_matching(con, q: str) -> str:
        """Places whose name the query mentions.

        Same reasoning as the people strip: a place name is not *in* the
        photograph either, so the model cannot find it and the gazetteer has
        to be asked directly. `Alicante` shows the place; `Alicante beach`
        still ranks beaches.
        """
        needle = q.strip()
        if not needle or not con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='places'").fetchone():
            return ""
        counts = subtree_counts(con)
        rows = [r for r in places_mod.lookup(con, needle, limit=8)
                if counts.get(r["id"])]
        if not rows:
            return ""
        chips = "".join(
            f'<a href="/place/{r["id"]}" class="person" style="padding:8px 14px">'
            f'<span><b>{html.escape(r["name"])}</b><br>'
            f'{counts[r["id"]]:,} photographs</span></a>' for r in rows[:6])
        return f'<div class="meta">Places</div><div class="people">{chips}</div>'

    def events_matching(con, q: str) -> str:
        """Events whose name the query mentions.

        The third of the same argument: `rippijuhlat` is not a thing SigLIP can
        see in a photograph, it is a word somebody wrote on a folder. So the
        names are matched directly, and the vector search still runs underneath.
        """
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='events'").fetchone():
            return ""
        rows = events_mod.matching(con, q, limit=6)
        if not rows:
            return ""
        chips = "".join(
            f'<a href="/event/{r["id"]}" class="person" style="padding:8px 14px">'
            f'<span><b>{html.escape(r["name"])}</b><br>'
            f'{datetime.fromtimestamp(r["start_at"]):%B %Y} &middot; '
            f'{r["asset_count"]:,} photographs</span></a>' for r in rows)
        return f'<div class="meta">Events</div><div class="people">{chips}</div>'

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
                title="Kuva-arkisto", q="", body=body, focus=" autofocus",
                placeholder="kids on a beach at sunset"))

        people = (people_matching(con, q) + places_matching(con, q)
                  + events_matching(con, q))

        # "Tommi and Emmi" is a question about a pair, not about either of
        # them. If the query names more than one person, the photographs they
        # are both in come first -- that is what was asked -- and the vector
        # search still runs underneath.
        together = ""
        named = cluster.people_in_query(con, q)
        if len(named) > 1:
            person_ids = [r["id"] for r in named]
            rows = cluster.photos_with_all(con, person_ids)
            total = cluster.count_photos_with_all(con, person_ids)
            who = " and ".join(html.escape(r["name"]) for r in named)
            if rows:
                # The count is the count, not the length of one page of it.
                shown = ("" if len(rows) >= total
                         else f' &middot; showing the {len(rows):,} most recent')
                together = (
                    f'<div class="meta">{total:,} photographs with '
                    f'<b>{who}</b> together{shown}</div>'
                    f'<div class="grid">{"".join(card(r) for r in rows)}</div>')
            else:
                together = (f'<div class="meta">No photograph has {who} '
                            'in it together.</div>')

        if embedded == 0:
            body = people + together + (
                '<div class="empty"><p>No search vectors yet.</p>'
                '<p><code>mediaidx embed</code></p></div>')
            return HTMLResponse(PAGE.format(
                title=f"{q} — Kuva-arkisto", q=html.escape(q), body=body,
                focus=" autofocus", placeholder="kids on a beach at sunset"))

        vec = encoder().encode_text([q])[0]
        hits = embed.search(con, vec, limit=limit, vec_ok=state["vec_ok"])
        if not hits:
            body = people + together + (
                '<div class="empty"><p>No matching photographs.</p></div>'
                if not (people or together) else "")
        else:
            ids = [h[0] for h in hits]
            scores = dict(hits)
            rows = con.execute(
                f"""SELECT id, content_hash, taken_at, taken_at_precision, camera_model
                    FROM assets WHERE id IN ({','.join('?' * len(ids))})""",
                ids).fetchall()
            by_id = {r["id"]: r for r in rows}
            cards = "".join(card(by_id[i], scores[i]) for i in ids if i in by_id)
            body = (people + together +
                    f'<div class="meta">{len(ids)} matches for '
                    f'&ldquo;{html.escape(q)}&rdquo; of {embedded:,} searchable'
                    f'</div><div class="grid">{cards}</div>')
        return HTMLResponse(PAGE.format(
            title=f"{q} — Kuva-arkisto", q=html.escape(q), body=body,
            focus=" autofocus", placeholder="kids on a beach at sunset"))

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
            _place_row(con, a),
            _event_row(con, asset_id),
            row("source", a["source_hint"]),
            row("conflict", a["conflict_note"]),
            row("hash", a["content_hash"][:24] + "..."),
        ])
        paths = "".join(
            f'<div style="color:var(--ink-3);font-size:12px;'
            f'font-family:ui-monospace,monospace;padding:1px 0">'
            f'{html.escape(f["path"])}</div>' for f in files)

        # Who is in it: named people first, then a count of the rest, so a
        # photograph says what is known without pretending to know more.
        in_photo = con.execute(
            """SELECT fa.id, fa.idx, fa.cluster_id, p.id AS pid, p.name
               FROM faces fa LEFT JOIN persons p ON p.id = fa.person_id
               WHERE fa.asset_id = ? ORDER BY p.name IS NULL, fa.idx""",
            (asset_id,)).fetchall()
        who = ""
        if in_photo:
            chips = []
            for f in in_photo:
                href = (f'/person/{f["pid"]}' if f["pid"]
                        else (f'/cluster/{f["cluster_id"]}' if f["cluster_id"] else None))
                label = f["name"] or "unnamed"
                chip = (f'<img src="/face/{a["content_hash"]}/{f["idx"]}" '
                        f'style="width:34px;height:34px;border-radius:50%;'
                        f'object-fit:cover;vertical-align:middle;margin-right:6px">'
                        f'<span class="chip">{html.escape(label)}</span>')
                chips.append(f'<a href="{href}">{chip}</a>' if href else chip)
            who = ('<div style="margin:14px 0;display:flex;gap:14px;'
                   'align-items:center;flex-wrap:wrap">' + "".join(chips) + '</div>')
        body = (
            f'<div style="max-width:1100px;margin:0 auto;padding:20px">'
            f'<a href="/" style="color:var(--accent)">&larr; back</a>'
            f'<img src="/thumb/m/{a["content_hash"]}" '
            f'style="display:block;max-width:100%;margin:14px 0;border-radius:3px">'
            f'{who}'
            f'<table style="font-size:14px">{info}</table>'
            f'<div style="margin-top:14px">{paths}</div></div>')
        return HTMLResponse(PAGE.format(
            title=f"{when} — Kuva-arkisto", q="", body=body, focus="",
            placeholder="kids on a beach at sunset"))

    def _place_row(con, a) -> str:
        """Where this was taken, and -- always -- on what evidence.

        A coordinate and a folder name are not the same kind of claim, and the
        page says which one it is rather than letting a guess look like a
        measurement.
        """
        if not a["place_id"]:
            return ""
        p = con.execute("SELECT id, name, path FROM places WHERE id = ?",
                        (a["place_id"],)).fetchone()
        if p is None:
            return ""
        where = " · ".join(reversed(p["path"].split("/")))
        note = ("from the coordinate" if a["place_source"] == "gps"
                else "from the folder name")
        return ('<tr><td style="color:var(--ink-3);padding:3px 16px 3px 0">place</td>'
                f'<td><a href="/place/{p["id"]}" style="color:var(--accent)">'
                f'{html.escape(where)}</a> '
                f'<span style="color:var(--ink-3)">&mdash; {note}</span></td></tr>')

    # ----------------------------------------------------------------------
    # Places
    # ----------------------------------------------------------------------

    @app.get("/places", response_class=HTMLResponse)
    def places_view():
        con = connect()
        if not con.execute("SELECT COUNT(*) FROM places").fetchone()[0]:
            return page("Places — Kuva-arkisto",
                        '<div class="empty"><p>No gazetteer loaded.</p>'
                        '<p><code>mediaidx places --load</code></p></div>')
        counts = subtree_counts(con)
        rows = [r for r in con.execute(
            "SELECT id, name FROM places WHERE kind = 'country' ORDER BY name")
            if counts.get(r["id"])]
        placed = con.execute(
            "SELECT COUNT(*) FROM assets WHERE place_id IS NOT NULL").fetchone()[0]
        total = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        if not rows:
            return page("Places — Kuva-arkisto",
                        '<div class="empty"><p>Nothing placed yet.</p>'
                        '<p><code>mediaidx places</code></p></div>')

        listing = "".join(
            f'<a href="/place/{r["id"]}"><b>{html.escape(r["name"])}</b>'
            f'<span>{counts[r["id"]]:,}</span></a>'
            for r in sorted(rows, key=lambda r: -counts[r["id"]]))
        body = [f'<div class="meta">{placed:,} of {total:,} photographs placed '
                f'&middot; {len(rows)} countries</div>',
                f'<div class="rows">{listing}</div>',
                _folder_review(con)]
        return page("Places — Kuva-arkisto", "".join(body))

    def _folder_review(con) -> str:
        """Every match made from a folder name, offered for rejection.

        These are guesses made from a single word, and the ambiguous ones come
        first: `1 of 16` means fifteen other places carry that name.
        """
        rows = con.execute(
            """SELECT fp.label, fp.candidates, p.id, p.path,
                      COUNT(a.id) AS n
                 FROM folder_places fp
                 JOIN places p ON p.id = fp.place_id
                 LEFT JOIN files f ON f.folder_id = fp.folder_id
                 LEFT JOIN assets a ON a.id = f.asset_id
                                   AND a.place_source = 'folder'
                GROUP BY fp.folder_id
                ORDER BY fp.candidates DESC, n DESC""").fetchall()
        if not rows:
            return ""
        seen, listing = set(), []
        for r in rows:
            if (r["label"], r["id"]) in seen:
                continue
            seen.add((r["label"], r["id"]))
            amb = (f'1 of {r["candidates"]}' if r["candidates"] > 1 else "only match")
            listing.append(
                '<div class="row">'
                f'<b>{html.escape(r["label"])}</b>'
                f'<span style="flex:2"><a href="/place/{r["id"]}" '
                f'style="color:var(--accent)">{html.escape(r["path"])}</a></span>'
                f'<span>{amb}</span>'
                f'<form method="post" action="/place/reject">'
                f'<input type="hidden" name="label" value="{html.escape(r["label"])}">'
                f'<input type="hidden" name="place_id" value="{r["id"]}">'
                f'<button class="ghost" type="submit">Not this place</button>'
                '</form></div>')
        return ('<div class="meta" style="padding-top:26px">From folder names '
                '&mdash; a guess made from one word, most ambiguous first. '
                'Rejecting one is permanent and re-runs the match.</div>'
                f'<div class="rows">{"".join(listing)}</div>')

    @app.post("/place/reject")
    def place_reject(label: str = Form(...), place_id: int = Form(...)):
        con = connect()
        places_mod.reject(con, label, place_id)
        places_mod.rematch_folders(con, cfg)
        con.commit()
        return RedirectResponse("/places", status_code=303)

    @app.get("/place/{place_id}", response_class=HTMLResponse)
    def place_view(place_id: int, limit: int = 240):
        con = connect()
        p = con.execute("SELECT * FROM places WHERE id = ?",
                        (place_id,)).fetchone()
        if p is None:
            return HTMLResponse("<p>not found</p>", status_code=404)
        counts = subtree_counts(con)

        crumbs, node = [], p
        while node is not None:
            crumbs.append(f'<a href="/place/{node["id"]}">'
                          f'{html.escape(node["name"])}</a>')
            node = con.execute("SELECT * FROM places WHERE id = ?",
                               (node["parent_id"],)).fetchone()                 if node["parent_id"] else None
        crumb = ('<div class="crumb">'
                 + " / ".join(reversed(crumbs)) + "</div>")

        children = [r for r in con.execute(
            "SELECT id, name FROM places WHERE parent_id = ? ORDER BY name",
            (place_id,)) if counts.get(r["id"])]
        listing = ""
        if children:
            listing = ('<div class="meta">Within</div><div class="rows">'
                       + "".join(
                           f'<a href="/place/{c["id"]}"><b>{html.escape(c["name"])}'
                           f'</b><span>{counts[c["id"]]:,}</span></a>'
                           for c in sorted(children, key=lambda c: -counts[c["id"]]))
                       + "</div>")

        rows = place_photos(con, p["path"], limit)
        total = counts.get(place_id, 0)
        # The count is the count of the whole subtree; the grid is one page of
        # it. Saying so beats letting the two numbers quietly disagree.
        shown = "" if len(rows) >= total else f' &middot; showing {len(rows):,}'
        grid = (f'<div class="meta">{total:,} photographs in '
                f'{html.escape(p["name"])} and everything within it{shown}</div>'
                f'<div class="grid">{"".join(card(r) for r in rows)}</div>')
        return page(f'{p["name"]} — Kuva-arkisto', crumb + listing + grid)

    # ----------------------------------------------------------------------
    # People
    # ----------------------------------------------------------------------

    def page(title, body):
        return HTMLResponse(PAGE.format(
            title=title, q="", body=body, focus="",
            placeholder="kids on a beach at sunset"))

    def face_card(hash_, idx, label, href, sub="") -> str:
        return (
            f'<figure><a href="{href}">'
            f'<img loading="lazy" src="/face/{hash_}/{idx}" alt="">'
            f'<figcaption><b>{html.escape(str(label))}</b>'
            f'<span>{html.escape(str(sub))}</span></figcaption></a></figure>')

    @app.get("/face/{content_hash}/{idx}")
    def face(content_hash: str, idx: int):
        p = images.face_thumb_path(cache_dir, content_hash, idx)
        if not p.exists():
            return Response(status_code=404)
        return FileResponse(p, media_type="image/webp",
                            headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/people", response_class=HTMLResponse)
    def people():
        con = connect()
        rows = cluster.overview(con)
        if not rows:
            nfaces = con.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
            hint = ("mediaidx faces cluster" if nfaces else "mediaidx faces")
            body = (f'<div class="empty"><p>No face clusters yet.</p>'
                    f'<p><code>{hint}</code></p></div>')
            return page("People — Kuva-arkisto", body)

        named = [r for r in rows if r["person"]]
        unnamed = [r for r in rows if not r["person"]]
        total = sum(r["size"] for r in rows)

        def group(rs):
            # The number is shown because it is how a cluster is addressed
            # when merging one that the similarity shortlist did not offer.
            return "".join(
                face_card(r["cover_hash"], r["cover_idx"],
                          r["person"] or f'{r["size"]:,} faces',
                          f'/cluster/{r["id"]}',
                          f'#{r["id"]}')
                for r in rs if r["cover_hash"])

        parts = [f'<div class="meta">{len(rows):,} clusters &middot; '
                 f'{total:,} faces &middot; {len(unnamed):,} still unnamed'
                 '  &middot;  biggest first, because naming one is worth the most'
                 '</div>']
        if unnamed:
            parts.append(f'<div class="faces">{group(unnamed)}</div>')
        if named:
            parts.append('<div class="meta">Named</div>'
                         f'<div class="faces">{group(named)}</div>')
        return page("People — Kuva-arkisto", "".join(parts))

    @app.get("/cluster/{cluster_id}", response_class=HTMLResponse)
    def cluster_view(cluster_id: int, error: str = Query(default="")):
        con = connect()
        c = con.execute(
            """SELECT c.*, p.name AS person FROM face_clusters c
               LEFT JOIN persons p ON p.id = c.person_id WHERE c.id = ?""",
            (cluster_id,)).fetchone()
        if c is None:
            return HTMLResponse("<p>not found</p>", status_code=404)

        error = (f' &middot; <b style="color:#c2554a">{html.escape(error)}</b>'
                 if error else "")

        # A name is the unique key for a person, so typing one that already
        # exists attaches this cluster to that person -- which is exactly what
        # you want for one person photographed several ways, and exactly how
        # two different people who share a first name get silently conflated.
        # Either way it should be visible rather than inferred afterwards.
        shared = ""
        if c["person_id"]:
            other = con.execute(
                "SELECT (SELECT COUNT(*) FROM face_clusters "
                "          WHERE person_id = ? AND id != ?) AS clusters, "
                "       (SELECT COUNT(*) FROM faces WHERE person_id = ?) AS faces",
                (c["person_id"], cluster_id, c["person_id"])).fetchone()
            if other["clusters"]:
                plural = "s" if other["clusters"] > 1 else ""
                shared = (
                    f'<div class="meta">{html.escape(c["person"])} is also on '
                    f'{other["clusters"]} other cluster{plural} &mdash; '
                    f'{other["faces"]:,} faces in total. Right for one person '
                    'photographed several ways; wrong for two people who share '
                    'a name, in which case rename one of them.</div>')
        faces_rows = cluster.representatives(con, cluster_id, limit=200)
        proposal = cluster.suggest(con, cluster_id) if not c["person"] else None
        candidates = cluster.similar(con, cluster_id, limit=10)

        suggestion = ""
        if proposal:
            suggestion = (f'<div class="meta">Suggested: '
                          f'<b>{html.escape(proposal[0])}</b> &mdash; '
                          f'{html.escape(proposal[1])}. '
                          'It is only a suggestion; nothing is named until you say so.'
                          '</div>')

        panel = (
            '<div class="panel">'
            f'<form method="post" action="/cluster/{cluster_id}/name">'
            f'<input type="text" name="name" placeholder="who is this?" '
            f'value="{html.escape(c["person"] or (proposal[0] if proposal else ""))}" '
            'autofocus>'
            '<button type="submit">Save</button></form>'
            # One way to merge, not three. The face strip below covers the
            # likely candidates, which a dropdown of the same ten names only
            # restated in a worse medium; this field covers everything else,
            # because similarity is a shortlist and the same child at 4 and at
            # 17 sits outside it.
            f'<form method="post" action="/cluster/{cluster_id}/merge">'
            '<input type="number" name="other" min="1" placeholder="cluster #" '
            'style="width:110px;padding:8px" required>'
            '<button class="ghost" type="submit">Merge in</button></form>'
            f'<form method="post" action="/cluster/{cluster_id}/ignore">'
            '<button class="ghost" type="submit">Not a person</button></form>'
            '</div>')

        # The likeliest merges, shown as faces with one button each. Merging is
        # the common repair on this data -- the same person across enough years
        # becomes several clusters -- so it should cost a look and a click, not
        # a hunt through a list of 737 numbers.
        def candidate_tile(c) -> str:
            label = html.escape(c["name"] or f'#{c["id"]}')
            return (
                f'<form method="post" action="/cluster/{cluster_id}/merge" '
                f'class="cand">'
                f'<input type="hidden" name="other" value="{c["id"]}">'
                f'<a href="/cluster/{c["id"]}">'
                f'<img src="/face/{c["cover_hash"]}/{c["cover_idx"]}" alt=""></a>'
                f'<span>{label}<br>{c["size"]:,} faces '
                f'&middot; {c["similarity"]:.2f}</span>'
                f'<button class="ghost" type="submit">Merge in</button></form>')

        strip = ""
        if candidates:
            tiles = "".join(candidate_tile(c) for c in candidates
                            if c["cover_hash"])
            strip = ('<div class="meta">Most similar clusters &mdash; the same '
                     'person often lands in more than one. Merging keeps '
                     f'<b>this</b> cluster (#{cluster_id}) and absorbs the other. '
                     'The number is cosine similarity; look before merging.</div>'
                     f'<div class="cands">{tiles}</div>')

        cards = "".join(
            face_card(r["content_hash"], r["idx"],
                      datetime.fromtimestamp(r["taken_at"]).strftime("%Y")
                      if r["taken_at"] else "",
                      f'/photo/{r["asset_id"]}')
            for r in faces_rows)

        title = c["person"] or f'Unnamed cluster of {c["size"]:,} faces'
        heading = html.escape(title)
        if c["person"]:
            heading = (f'<a href="/person/{c["person_id"]}" '
                       f'style="color:var(--accent)">{heading}</a>')
        body = (f'<div class="meta"><a href="/people" style="color:var(--accent)">'
                f'&larr; people</a> &middot; <b>{heading}</b> '
                f'&middot; cluster #{cluster_id} &middot; {c["size"]:,} faces'
                f'{error}</div>'
                f'{suggestion}{shared}{panel}{strip}'
                f'<div class="meta">Every face in this cluster</div>'
                f'<div class="faces">{cards}</div>')
        return page(f"{title} — Kuva-arkisto", body)

    @app.post("/cluster/{cluster_id}/name")
    def cluster_name(cluster_id: int, name: str = Form(default="")):
        con = connect()
        if name.strip():
            cluster.set_person(con, cluster_id, name)
        else:
            cluster.clear_person(con, cluster_id)
        return RedirectResponse(f"/cluster/{cluster_id}", status_code=303)

    @app.post("/cluster/{cluster_id}/merge")
    def cluster_merge(cluster_id: int, other: int = Form(...)):
        con = connect()
        try:
            cluster.merge(con, cluster_id, other)
        except ValueError:
            return RedirectResponse(
                f"/cluster/{cluster_id}?error=no+cluster+%23{other}",
                status_code=303)
        return RedirectResponse(f"/cluster/{cluster_id}", status_code=303)

    @app.post("/cluster/{cluster_id}/ignore")
    def cluster_ignore(cluster_id: int):
        con = connect()
        cluster.set_status(con, cluster_id, "ignored")
        return RedirectResponse("/people", status_code=303)

    @app.get("/person/{person_id}", response_class=HTMLResponse)
    def person(person_id: int, limit: int = 300):
        con = connect()
        p = con.execute("SELECT * FROM persons WHERE id = ?",
                        (person_id,)).fetchone()
        if p is None:
            return HTMLResponse("<p>not found</p>", status_code=404)
        rows = con.execute(
            """SELECT DISTINCT a.id, a.content_hash, a.taken_at,
                      a.taken_at_precision, a.camera_model
               FROM assets a JOIN faces f ON f.asset_id = a.id
               WHERE f.person_id = ? ORDER BY a.taken_at DESC LIMIT ?""",
            (person_id, limit)).fetchall()
        body = (f'<div class="meta"><a href="/people" style="color:var(--accent)">'
                f'&larr; people</a> &middot; <b>{html.escape(p["name"])}</b> '
                f'&middot; {len(rows):,} photographs</div>'
                f'<div class="grid">{"".join(card(r) for r in rows)}</div>')
        return page(f'{p["name"]} — Kuva-arkisto', body)

    # ----------------------------------------------------------------------
    # Events
    # ----------------------------------------------------------------------

    def _event_row(con, asset_id: int) -> str:
        """Which event this photograph belongs to, and where its name came from."""
        e = events_mod.for_asset(con, asset_id)
        if e is None:
            return ""
        label = e["name"] or f'{e["asset_count"]:,} photographs, unnamed'
        note = {"folder": "from the folder name",
                "user": "you named it"}.get(e["name_source"], "grouped by time")
        return ('<tr><td style="color:var(--ink-3);padding:3px 16px 3px 0">event</td>'
                f'<td><a href="/event/{e["id"]}" style="color:var(--accent)">'
                f'{html.escape(label)}</a> '
                f'<span style="color:var(--ink-3)">&mdash; {note}</span></td></tr>')

    def when_range(start: int, end: int) -> str:
        a, b = datetime.fromtimestamp(start), datetime.fromtimestamp(end)
        if a.date() == b.date():
            return f"{a.day} {a:%B %Y}"
        if (a.year, a.month) == (b.year, b.month):
            return f"{a.day}&ndash;{b.day} {a:%B %Y}"
        if a.year == b.year:
            return f"{a.day} {a:%B} &ndash; {b.day} {b:%B %Y}"
        return f"{a.day} {a:%B %Y} &ndash; {b.day} {b:%B %Y}"

    @app.get("/events", response_class=HTMLResponse)
    def events_view(unnamed: int = 0, limit: int = 400):
        con = connect()
        total = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if not total:
            return page("Events — Kuva-arkisto",
                        '<div class="empty"><p>No events yet.</p>'
                        '<p><code>mediaidx derive</code></p></div>')
        named = con.execute(
            "SELECT COUNT(*) FROM events WHERE name IS NOT NULL").fetchone()[0]
        rows = events_mod.overview(con, limit=limit, unnamed_only=bool(unnamed))

        listing = []
        year = None
        for r in rows:
            y = datetime.fromtimestamp(r["start_at"]).year
            if y != year:
                year = y
                listing.append(f'<div class="row" style="border:0;padding-top:18px">'
                               f'<b style="color:var(--ink-3)">{y}</b></div>')
            name = r["name"] or "unnamed"
            style = "" if r["name"] else ' style="color:var(--ink-3)"'
            listing.append(
                f'<a href="/event/{r["id"]}"><b{style}>{html.escape(name)}</b>'
                f'<span style="flex:1">{when_range(r["start_at"], r["end_at"])}</span>'
                f'<span style="flex:1">{html.escape(r["place"] or "")}</span>'
                f'<span>{r["asset_count"]:,}</span></a>')

        toggle = ('<a href="/events" style="color:var(--accent)">show all</a>'
                  if unnamed else
                  '<a href="/events?unnamed=1" style="color:var(--accent)">'
                  'only the unnamed</a>')
        body = (f'<div class="meta">{total:,} events &middot; {named:,} named '
                f'&middot; {total - named:,} not &middot; {toggle}</div>'
                f'<div class="rows">{"".join(listing)}</div>')
        return page("Events — Kuva-arkisto", body)

    def split_form(con, e, back: str) -> str:
        """Splitting cuts at the longest interruption inside the event, one
        press at a time, and the cut is permanent. It is offered only where
        there is somewhere to cut that leaves a real event on both sides --
        an action that would silently drop a photograph out of the timeline is
        better not drawn than explained afterwards."""
        if events_mod.split_point(con, e["id"], cfg) is None:
            return ""
        return (f'<form method="post" action="/event/{e["id"]}/split">'
                f'<input type="hidden" name="back" value="{html.escape(back)}">'
                '<button class="ghost" type="submit">Not one event</button></form>')

    def event_panel(con, e, *, back: str) -> str:
        """Name it, split it, or say it is not worth asking about."""
        return (
            '<div class="panel">'
            f'<form method="post" action="/event/{e["id"]}/name">'
            f'<input type="hidden" name="back" value="{html.escape(back)}">'
            f'<input type="text" name="name" style="min-width:320px" '
            f'placeholder="what was this?" '
            f'value="{html.escape(e["name"] or "")}" autofocus>'
            '<button type="submit">Save</button></form>'
            + split_form(con, e, back) +
            f'<form method="post" action="/event/{e["id"]}/dismiss">'
            f'<input type="hidden" name="back" value="{html.escape(back)}">'
            '<button class="ghost" type="submit">Don&rsquo;t ask again</button>'
            '</form></div>')

    def event_facts(con, e) -> str:
        bits = [when_range(e["start_at"], e["end_at"]),
                f'{e["asset_count"]:,} photographs']
        if e["place_id"]:
            bits.append(f'<a href="/place/{e["place_id"]}" '
                        f'style="color:var(--accent)">'
                        f'{html.escape(e["place_path"] or e["place"])}</a>')
        if e["folder"]:
            bits.append(f'<span style="font-family:ui-monospace,monospace;'
                        f'font-size:12px">{html.escape(e["folder"])}</span>')
        who = events_mod.people_in(con, e["id"])
        line = '<div class="meta">' + " &middot; ".join(bits) + "</div>"
        if who:
            chips = "".join(
                f'<a href="/person/{p["id"]}" class="chip" '
                f'style="margin-right:6px">{html.escape(p["name"])} '
                f'&middot; {p["photos"]}</a>' for p in who[:8])
            line += f'<div class="meta">Already known to be here: {chips}</div>'
        return line

    @app.get("/event/{event_id}", response_class=HTMLResponse)
    def event_view(event_id: int, limit: int = 300):
        con = connect()
        e = events_mod.get(con, event_id)
        if e is None:
            return HTMLResponse("<p>not found</p>", status_code=404)
        rows = events_mod.assets_of(con, event_id, limit)
        title = e["name"] or f'Unnamed, {e["asset_count"]:,} photographs'
        src = {"folder": "named by its folder", "user": "you named it"}.get(
            e["name_source"], "grouped by time and place")
        shown = ("" if len(rows) >= e["asset_count"]
                 else f' &middot; showing {len(rows):,}')
        body = (f'<div class="meta"><a href="/events" style="color:var(--accent)">'
                f'&larr; events</a> &middot; <b>{html.escape(title)}</b> '
                f'&middot; {src}</div>'
                + event_facts(con, e)
                + event_panel(con, e, back=f"/event/{event_id}")
                + f'<div class="meta">In order{shown}</div>'
                + f'<div class="grid">{"".join(card(r) for r in rows)}</div>')
        return page(f"{title} — Kuva-arkisto", body)

    @app.post("/event/{event_id}/name")
    def event_name(event_id: int, name: str = Form(default=""),
                   back: str = Form(default="/events")):
        con = connect()
        events_mod.set_name(con, event_id, name)
        return RedirectResponse(_safe_back(back), status_code=303)

    @app.post("/event/{event_id}/dismiss")
    def event_dismiss(event_id: int, back: str = Form(default="/events")):
        con = connect()
        events_mod.dismiss(con, event_id)
        return RedirectResponse(_safe_back(back), status_code=303)

    @app.post("/event/{event_id}/split")
    def event_split(event_id: int, back: str = Form(default="/events")):
        con = connect()
        at = events_mod.split(con, event_id, cfg)
        if at is None:
            return RedirectResponse(_safe_back(back), status_code=303)
        # The cut is recorded against a photograph, so the grouping has to be
        # rebuilt for it to mean anything -- and the queue with it, because
        # every event id has just changed.
        events_mod.run(con, cfg)
        queue_mod.build(con, cfg)
        con.commit()
        if back.startswith("/event/"):
            e = events_mod.for_asset(con, at)
            return RedirectResponse(f'/event/{e["id"]}' if e else "/events",
                                    status_code=303)
        return RedirectResponse(_safe_back(back), status_code=303)

    def _safe_back(back: str) -> str:
        """Only ever redirect within this application."""
        return back if back.startswith("/") and not back.startswith("//") else "/events"

    # ----------------------------------------------------------------------
    # Review
    # ----------------------------------------------------------------------

    # Enter is the form's own doing; Esc is not, and skipping has to be as
    # cheap as answering or the queue does not get worked through. This is the
    # only script on the site.
    #
    # The field is focused from here rather than with `autofocus`, which scrolls
    # itself into view and slides the question's own context -- the dates, the
    # place, the folder, who is in it -- up underneath the sticky header. On the
    # one screen whose whole purpose is to be answered from what it shows, that
    # is not a detail. `preventScroll` keeps the caret in the field and the page
    # at the top.
    KEYS = ("<script>document.addEventListener('keydown',function(e){"
            "if(e.key==='Escape'){var f=document.getElementById('skip');"
            "if(f){e.preventDefault();f.submit();}}});"
            "var a=document.getElementById('answer');"
            "if(a){a.focus({preventScroll:true});"
            "a.setSelectionRange(a.value.length,a.value.length);}</script>")

    @app.get("/review", response_class=HTMLResponse)
    def review():
        con = connect()
        c = queue_mod.counts(con)
        item = queue_mod.next_item(con)
        if item is None:
            # An emptied queue and one that was never built look identical in
            # the table, and telling somebody who has just answered the last
            # question to go and build the queue would be absurd. The events
            # are what tell the two apart.
            if not con.execute("SELECT COUNT(*) FROM events").fetchone()[0]:
                return page("Review — Kuva-arkisto",
                            '<div class="empty"><p>Nothing in the queue.</p>'
                            '<p><code>mediaidx derive</code> builds it.</p></div>')
            more = (f'<p>{c["skipped"]:,} skipped; they come back after the '
                    'next <code>mediaidx derive</code>.</p>' if c["skipped"]
                    else '<p>Every question has an answer.</p>')
            return page("Review — Kuva-arkisto",
                        f'<div class="empty"><p>Nothing left to ask.</p>{more}'
                        '<p><a href="/events" style="color:var(--accent)">'
                        'the events</a></p></div>')

        head = (f'<div class="meta"><b>{c["open"]:,} questions left</b> '
                f'&middot; {c["events"]:,} events, {c["clusters"]:,} people '
                f'&middot; {c["photographs"]:,} photographs waiting on an answer '
                '&middot; largest first</div>')
        why = ""
        if item["rationale"]:
            why = (f'<div class="meta">Suggested: '
                   f'<b>{html.escape(item["suggestion"] or "")}</b> &mdash; '
                   f'{html.escape(item["rationale"])}. '
                   'It is a suggestion; nothing is named until you say so.</div>')

        skip = (f'<form method="post" action="/review/{item["id"]}/skip" id="skip">'
                '<button class="ghost" type="submit">Skip &nbsp;Esc</button></form>')

        if item["kind"] == "event":
            e = events_mod.get(con, item["ref_id"])
            if e is None:                      # the queue outlived a rebuild
                queue_mod.drop(con, item["id"])
                return RedirectResponse("/review", status_code=303)
            panel = (
                '<div class="panel">'
                f'<form method="post" action="/event/{e["id"]}/name">'
                '<input type="hidden" name="back" value="/review">'
                '<input type="text" name="name" id="answer" '
                'style="min-width:340px" placeholder="what was this?" '
                f'value="{html.escape(item["suggestion"] or "")}">'
                '<button type="submit">Name it &nbsp;&crarr;</button></form>'
                + split_form(con, e, "/review") + skip +
                f'<form method="post" action="/event/{e["id"]}/dismiss">'
                '<input type="hidden" name="back" value="/review">'
                '<button class="ghost" type="submit">Don&rsquo;t ask again</button>'
                '</form></div>')
            sample = events_mod.sample(con, e["id"], limit=12)
            body = (head + event_facts(con, e) + why + panel +
                    f'<div class="meta">{len(sample)} of {e["asset_count"]:,}, '
                    'spread across it &middot; '
                    f'<a href="/event/{e["id"]}" style="color:var(--accent)">'
                    'see all of them</a></div>'
                    f'<div class="grid">{"".join(card(r) for r in sample)}</div>'
                    + KEYS)
            return page("Review — Kuva-arkisto", body)

        cl = con.execute("SELECT * FROM face_clusters WHERE id = ?",
                         (item["ref_id"],)).fetchone()
        if cl is None or cl["person_id"]:
            queue_mod.drop(con, item["id"])
            return RedirectResponse("/review", status_code=303)
        panel = (
            '<div class="panel">'
            f'<form method="post" action="/review/{item["id"]}/person">'
            '<input type="text" name="name" id="answer" '
            'style="min-width:340px" placeholder="who is this?" '
            f'value="{html.escape(item["suggestion"] or "")}">'
            '<button type="submit">Name them &nbsp;&crarr;</button></form>'
            f'<a href="/cluster/{cl["id"]}" class="chip" style="padding:9px 14px">'
            'Open the cluster to merge</a>'
            + skip +
            f'<form method="post" action="/review/{item["id"]}/ignore">'
            '<button class="ghost" type="submit">Not a person</button></form>'
            '</div>')
        faces_rows = cluster.representatives(con, cl["id"], limit=12)
        cards = "".join(
            face_card(r["content_hash"], r["idx"],
                      datetime.fromtimestamp(r["taken_at"]).strftime("%Y")
                      if r["taken_at"] else "",
                      f'/photo/{r["asset_id"]}') for r in faces_rows)
        body = (head +
                f'<div class="meta">Cluster #{cl["id"]} &middot; '
                f'{cl["size"]:,} faces in {item["priority"]:,} photographs</div>'
                + why + panel +
                f'<div class="faces">{cards}</div>' + KEYS)
        return page("Review — Kuva-arkisto", body)

    @app.post("/review/{queue_id}/skip")
    def review_skip(queue_id: int):
        con = connect()
        queue_mod.skip(con, queue_id)
        return RedirectResponse("/review", status_code=303)

    @app.post("/review/{queue_id}/person")
    def review_person(queue_id: int, name: str = Form(default="")):
        con = connect()
        item = queue_mod.get(con, queue_id)
        if item and name.strip():
            cluster.set_person(con, item["ref_id"], name)
            queue_mod.drop(con, queue_id)
        return RedirectResponse("/review", status_code=303)

    @app.post("/review/{queue_id}/ignore")
    def review_ignore(queue_id: int):
        con = connect()
        item = queue_mod.get(con, queue_id)
        if item:
            cluster.set_status(con, item["ref_id"], "ignored")
            queue_mod.drop(con, queue_id)
        return RedirectResponse("/review", status_code=303)

    return app


def run(cfg, host: str = "127.0.0.1", port: int = 8710) -> None:
    import uvicorn
    # A database indexed before this milestone has no events tables at all, and
    # the Events and Review screens would answer 500 rather than "nothing yet".
    # Migration is idempotent; doing it here covers both `serve` and `review`.
    con = db.connect(cfg.database, create=False)
    db.migrate(con)
    con.close()
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")
