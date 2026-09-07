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
nav {{ max-width:1500px; margin:8px auto 0; display:flex; gap:16px;
  font-size:13px; color:var(--ink-3); }}
nav a:hover {{ color:var(--accent); }}
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
</style></head><body>
<header><form action="/" method="get">
  <input type="search" name="q" value="{q}" placeholder="{placeholder}" autofocus>
  <button type="submit">Search</button>
</form>
<nav><a href="/">Photographs</a><a href="/people">People</a></nav></header>
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

        people = people_matching(con, q)

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
                placeholder="kids on a beach at sunset"))

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
            title=f"{when} — Kuva-arkisto", q="", body=body,
            placeholder="kids on a beach at sunset"))

    # ----------------------------------------------------------------------
    # People
    # ----------------------------------------------------------------------

    def page(title, body):
        return HTMLResponse(PAGE.format(
            title=title, q="", body=body,
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

    return app


def run(cfg, host: str = "127.0.0.1", port: int = 8710) -> None:
    import uvicorn
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")
