"""The local search UI.

Server-rendered, no build step, no framework on the client. The page is scanned
and operated rather than read, so the grid is the interface and everything else
stays out of its way.

Nothing here runs a model except the query encoder, which turns typed text into
one vector. Browsing touches only photos.db and the thumbnail cache.
"""

from __future__ import annotations

import html
import json
import mimetypes
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import FastAPI, Form, Query
from fastapi.responses import (FileResponse, HTMLResponse, RedirectResponse,
                               Response, StreamingResponse)

from . import cluster
from . import corrections
from . import config as config_mod
from . import db, embed, images
from . import events as events_mod
from . import paths as pathutil
from . import places as places_mod
from . import query
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
/* What the box understood. These are the interface, not decoration: each one
   states a criterion and carries the link that removes it, so the whole thing
   works as plain anchors with no script on the page. */
.chips {{ max-width:1500px; margin:0 auto; padding:12px 20px 0; display:flex;
  gap:8px; align-items:center; flex-wrap:wrap; }}
.crit {{ display:inline-flex; gap:8px; align-items:center; padding:5px 6px 5px 11px;
  background:var(--surface); border:1px solid var(--rule); border-radius:14px;
  font-size:13px; color:var(--ink); }}
.crit i {{ font-style:normal; color:var(--ink-3); font-size:11.5px;
  text-transform:uppercase; letter-spacing:.04em; }}
.crit a {{ color:var(--ink); }}
.crit a:hover {{ color:var(--accent); }}
.crit a.x {{ color:var(--ink-3); padding:0 5px; border-radius:9px;
  font-size:14px; line-height:1; }}
.crit a.x:hover {{ background:var(--rule); color:var(--ink); }}
.rest {{ color:var(--ink-3); font-size:13px; }}
.rest b {{ color:var(--ink-2); font-weight:500; }}
/* An unsure reading: shown, never applied. */
.offer {{ max-width:1500px; margin:0 auto; padding:8px 20px 0; font-size:13px;
  color:var(--ink-3); }}
.offer a {{ color:var(--accent); }}
details.filters {{ max-width:1500px; margin:0 auto; padding:8px 20px 0;
  font-size:13px; color:var(--ink-3); }}
details.filters summary {{ cursor:pointer; color:var(--ink-3); }}
details.filters form {{ display:flex; gap:14px; align-items:flex-end;
  flex-wrap:wrap; margin:10px 0 0; max-width:none; }}
details.filters label {{ display:flex; flex-direction:column; gap:4px;
  font-size:11.5px; text-transform:uppercase; letter-spacing:.04em; }}
details.filters input, details.filters select {{ padding:6px 9px; font-size:14px;
  color:var(--ink); background:var(--surface); border:1px solid var(--rule);
  border-radius:3px; text-transform:none; letter-spacing:0; }}
.more {{ max-width:1500px; margin:0 auto; padding:0 20px 50px; display:flex;
  gap:12px; align-items:center; }}
.more a {{ display:inline-block; padding:9px 18px; border:1px solid var(--rule);
  border-radius:3px; background:var(--surface); color:var(--ink-2);
  font-size:14px; }}
.more a:hover {{ border-color:var(--accent); color:var(--accent); }}
/* Everyone in one photograph, each with the means to say otherwise. Bigger
   than the review grid: this is one picture being looked at carefully, not a
   wall of faces being scanned. */
.mugs {{ display:flex; gap:12px; flex-wrap:wrap; margin:16px 0 20px; }}
.mug {{ margin:0; width:170px; background:var(--surface);
  border:1px solid var(--rule); border-radius:4px; padding:9px; }}
.mug img {{ display:block; width:100%; aspect-ratio:1; object-fit:cover;
  border-radius:3px; background:var(--rule); }}
.mug figcaption {{ display:block; padding:7px 1px 8px; font-size:13px; }}
.mug figcaption b {{ font-weight:600; }}
.mug figcaption span {{ display:block; color:var(--ink-3); font-size:11px; }}
.mug form {{ display:flex; gap:5px; margin:0; }}
.mug input[type=text] {{ min-width:0; flex:1; padding:5px 7px; font-size:13px;
  color:var(--ink); background:var(--ground); border:1px solid var(--rule);
  border-radius:3px; }}
.mug button {{ padding:5px 8px; font-size:12px; }}
/* The correction sitting under the fact it corrects, quiet until wanted. */
.fix {{ margin-top:5px; }}
.fix form {{ display:flex; gap:6px; margin:0; max-width:none; }}
.fix input[type=text] {{ padding:5px 9px; font-size:13px; color:var(--ink);
  background:var(--surface); border:1px solid var(--rule); border-radius:3px; }}
.fix button {{ padding:5px 11px; font-size:12px; }}
tr:not(:hover) .fix {{ opacity:.35; }}
.fix:focus-within {{ opacity:1 !important; }}
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
        state["vec_ok"] = embed.vec_index_ready(con)
        return con

    def encoder():
        if state["encoder"] is None:
            state["encoder"] = embed.Encoder(
                model_name=cfg.scan.get("embed_model", embed.DEFAULT_MODEL),
                pretrained=cfg.scan.get("embed_pretrained", embed.DEFAULT_PRETRAINED),
            )
        return state["encoder"]

    def safe_back(where: str) -> str:
        """A return path, or the front page if it is not one.

        `back` arrives in the URL, so it is only ever followed as a relative
        path on this application: anything absolute, protocol-relative or
        otherwise not starting with a single slash is discarded rather than
        turned into a link somewhere else.
        """
        where = (where or "/").strip()
        if not where.startswith("/") or where.startswith("//"):
            return "/"
        return where

    def card(row, score=None, back=None) -> str:
        when = ""
        if row["taken_at"]:
            dt = datetime.fromtimestamp(row["taken_at"])
            p = row["taken_at_precision"]
            # No %-d / %#d: those are platform-specific. Build the day by hand.
            when = (f"{dt:%Y}" if p == "year" else
                    f"{dt:%b %Y}" if p == "month" else
                    f"{dt.day} {dt:%b %Y}")
        right = f"{score:.2f}" if score is not None else (row["camera_model"] or "")
        # Where this card was rendered travels with it, so the photograph
        # can offer the way back to the page that led there rather than always
        # to the front (spec 8.2).
        to = f'/photo/{row["id"]}'
        if back:
            to += f"?back={quote_plus(back)}"
        return (
            f'<figure><a href="{to}">'
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

    # How many photographs a page shows, and how deep the ranked search looks
    # before paging through what it found. 600 is far past the point where a
    # relevant result is plausible; it exists so the cut and the page count are
    # computed over the whole answer rather than over one screen of it.
    PAGE_SIZE = 120
    MAX_RANKED = 600

    FILTERS = ("from", "to", "cam", "faces", "who", "dsrc", "psrc")

    def _qs(q: str, f: dict, **over) -> str:
        """A link back to this search with something changed."""
        parts = {"q": q, **{k: v for k, v in f.items() if v}, **over}
        out = []
        for k, v in parts.items():
            if v in (None, "", 0):
                continue
            out.append(f"{quote_plus(str(k))}={quote_plus(str(v))}")
        return "/?" + "&".join(out) if out else "/"

    def _where(con, r, f) -> tuple:
        """Every chip and filter as one SQL predicate over `assets a`.

        Built once and used twice: to narrow the candidate set before the
        vector search ranks it (§7.2), and as the whole query when there is no
        description to rank by. Two code paths reading one definition of what
        was asked for.
        """
        preds, args = [], []

        pids = [t.ref_id for t in r.people]
        if pids:
            # Everyone named has to be in it -- "Tommi and Emmi" is a question
            # about a pair, not about either of them.
            marks = ",".join("?" * len(pids))
            preds.append(
                f"""a.id IN (SELECT asset_id FROM faces
                              WHERE person_id IN ({marks})
                              GROUP BY asset_id
                             HAVING COUNT(DISTINCT person_id) = ?)""")
            args.extend([*pids, len(pids)])

        for t in r.places:
            row = con.execute("SELECT path FROM places WHERE id = ?",
                              (t.ref_id,)).fetchone()
            if row is None:
                continue
            # Naming any level matches everything beneath it, with one indexed
            # comparison and no recursion (§7.2).
            preds.append("""a.place_id IN (SELECT id FROM places
                             WHERE path = ? OR path LIKE ? ESCAPE '\\')""")
            args.extend([row["path"], _like(row["path"]) + "/%"])

        for t in r.events:
            # Matched by name, not by id: three separate holidays are called
            # Alicante, and asking for Alicante means all three (§7.1).
            preds.append("""a.id IN (SELECT ea.asset_id FROM event_assets ea
                                       JOIN events e ON e.id = ea.event_id
                                      WHERE e.name = ?)""")
            args.append(t.label)

        for t in r.dates:
            preds.append("(a.taken_at >= ? AND a.taken_at < ?)")
            args.extend([t.start, t.end])

        if f.get("from"):
            preds.append("a.taken_at >= ?")
            args.append(_epoch(f["from"], 0))
        if f.get("to"):
            preds.append("a.taken_at < ?")
            args.append(_epoch(f["to"], 1))
        if f.get("cam"):
            preds.append("(a.camera_model LIKE ? OR a.camera_make LIKE ?)")
            args.extend([f"%{_like(f['cam'])}%"] * 2)
        if f.get("faces") == "y":
            preds.append("EXISTS (SELECT 1 FROM faces x WHERE x.asset_id = a.id)")
        elif f.get("faces") == "n":
            preds.append("NOT EXISTS (SELECT 1 FROM faces x WHERE x.asset_id = a.id)")
        if f.get("who"):
            # How many people are in it, which is a different question from
            # which people are in it: the chips say who, this says how many, and
            # together they ask for the two of them and nobody else. Counted
            # from detected faces, so somebody turned away is not among them.
            n = f["who"]
            if n == "3":
                preds.append("(SELECT COUNT(*) FROM faces x "
                             "WHERE x.asset_id = a.id) >= 3")
            elif n in ("1", "2"):
                preds.append("(SELECT COUNT(*) FROM faces x "
                             "WHERE x.asset_id = a.id) = ?")
                args.append(int(n))
        if f.get("dsrc"):
            preds.append("a.taken_at_source = ?")
            args.append(f["dsrc"])
        if f.get("psrc"):
            preds.append("a.place_source = ?")
            args.append(f["psrc"])

        return (" AND ".join(preds), args) if preds else ("", [])

    def _epoch(text: str, end: int) -> int:
        """A date typed into a filter box. Unreadable input filters nothing."""
        for fmt, add in (("%Y-%m-%d", 1), ("%Y-%m", 31), ("%Y", 366)):
            try:
                dt = datetime.strptime(text.strip(), fmt)
            except ValueError:
                continue
            if not end:
                return int(dt.timestamp())
            return int(dt.timestamp()) + add * 86400
        return 0 if not end else 4102444800

    def chip_row(r, f) -> str:
        """What the box understood, and how to take any of it back."""
        if not r.terms and not r.text:
            return ""
        seen = {"person": "/person/", "place": "/place/", "event": "/event/"}
        bits = []
        for t in r.terms:
            gone = _qs(r.without(t), f)
            # The label leads to the thing itself, so a chip is a way through
            # to the person, place or event as well as a criterion.
            label = html.escape(t.label)
            if t.kind in seen and t.ref_id:
                label = f'<a href="{seen[t.kind]}{t.ref_id}">{label}</a>'
            bits.append(
                f'<span class="crit"><i>{t.kind}</i> {label}'
                f'<a class="x" href="{gone}" title="not this">&times;</a></span>')
        if r.text and r.terms:
            bits.append(f'<span class="rest">and <b>{html.escape(r.text)}</b> '
                        'as the description</span>')
        return f'<div class="chips">{"".join(bits)}</div>' if bits else ""

    def _count_for(con, raw: str, f: dict) -> int:
        """How many photographs another reading of the query would find.

        Only the chips are counted. Where the query also carries a description
        this is the size of the set the ranking would run inside, which is the
        honest thing to promise: how many the search would then choose from.
        """
        alt = query.resolve(con, raw, place_counts=subtree_counts(con))
        where, args = _where(con, alt, f)
        clause = f" WHERE {where}" if where else ""
        return con.execute(f"SELECT COUNT(*) FROM assets a{clause}",
                           args).fetchone()[0]

    def offer_row(con, r, f) -> str:
        """A reading that was plausible but not acted on (§7.1).

        Shown rather than applied, because the archive holding a handful of
        photographs somewhere named like an ordinary word is not evidence that
        the word was meant as a place.
        """
        bits = []
        for t in r.offers:
            take = _qs(r.raw.replace(t.span, f"place:{t.span}"), f)
            bits.append(
                f'&ldquo;{html.escape(t.span)}&rdquo; is also '
                f'<a href="{take}">{html.escape(t.label)}</a>, '
                f'where you have {t.held:,}. Searching the words instead.')

        # A word that reads two ways says so, rather than letting precedence
        # decide in silence. `Hartola` is an event of three photographs and a
        # municipality of 1,286: picking the event is defensible, picking it
        # without saying so is not, and the difference only shows as an
        # unexplained empty page.
        for t in r.terms:
            for alt in t.also:
                other = r.raw.replace(t.span, f"{alt.kind}:{t.span}")
                take = _qs(other, f)
                # The count has to be the count *this link would give*, which
                # is the whole query read the other way -- not how much the
                # place holds by itself. Hartola the municipality holds 1,286
                # photographs and shares 20 of them with Tommi and Ansku;
                # offering the larger number answers a question nobody asked.
                would = _count_for(con, other, f)
                says = (f' &mdash; {would:,} photograph'
                        f'{"" if would == 1 else "s"}' if would else
                        ", which finds nothing either")
                bits.append(
                    f'&ldquo;{html.escape(t.span)}&rdquo; is also a {alt.kind}, '
                    f'<a href="{take}">{html.escape(alt.label)}</a>{says}.')
        return f'<div class="offer">{" ".join(bits)}</div>' if bits else ""

    def filter_row(q: str, f: dict) -> str:
        """The auditing controls, collapsed until wanted (§7.4).

        Not chips: a chip answers "what am I looking for", these answer "what
        do I want to check", and nobody types `place-source is a folder name`
        as a question.
        """
        def opt(name, value, label, current):
            sel = " selected" if current == value else ""
            return f'<option value="{value}"{sel}>{label}</option>'

        on = sum(1 for k in FILTERS if f.get(k))
        summary = "Filters" + (f" &middot; {on} on" if on else "")
        sel_faces = "".join([
            opt("faces", "", "any", f.get("faces")),
            opt("faces", "y", "with faces", f.get("faces")),
            opt("faces", "n", "without", f.get("faces"))])
        sel_who = "".join([
            opt("who", "", "any number", f.get("who")),
            opt("who", "1", "one alone", f.get("who")),
            opt("who", "2", "just two", f.get("who")),
            opt("who", "3", "three or more", f.get("who"))])
        sel_dsrc = "".join([
            opt("dsrc", "", "any", f.get("dsrc")),
            opt("dsrc", "exif", "EXIF", f.get("dsrc")),
            opt("dsrc", "filename", "filename", f.get("dsrc")),
            opt("dsrc", "folder", "folder", f.get("dsrc"))])
        sel_psrc = "".join([
            opt("psrc", "", "any", f.get("psrc")),
            opt("psrc", "gps", "coordinate", f.get("psrc")),
            opt("psrc", "folder", "folder name", f.get("psrc"))])
        return (
            f'<details class="filters"{" open" if on else ""}>'
            f'<summary>{summary}</summary>'
            '<form action="/" method="get">'
            f'<input type="hidden" name="q" value="{html.escape(q)}">'
            '<label>from<input type="text" name="from" placeholder="2019-06" '
            f'value="{html.escape(f.get("from", ""))}"></label>'
            '<label>to<input type="text" name="to" placeholder="2020" '
            f'value="{html.escape(f.get("to", ""))}"></label>'
            '<label>camera<input type="text" name="cam" placeholder="any" '
            f'value="{html.escape(f.get("cam", ""))}"></label>'
            f'<label>faces<select name="faces">{sel_faces}</select></label>'
            f'<label>people in it<select name="who">{sel_who}</select></label>'
            f'<label>date from<select name="dsrc">{sel_dsrc}</select></label>'
            f'<label>place from<select name="psrc">{sel_psrc}</select></label>'
            '<button type="submit">Apply</button>'
            f'<a class="chip" href="{_qs(q, {})}" style="padding:7px 12px">'
            'clear</a>'
            '</form></details>')

    @app.get("/", response_class=HTMLResponse)
    def home(q: str = Query(default=""), page: int = 0, more: int = 0,
             date_from: str = Query(default="", alias="from"),
             to: str = Query(default=""), cam: str = Query(default=""),
             faces: str = Query(default=""), who: str = Query(default=""),
             dsrc: str = Query(default=""), psrc: str = Query(default="")):
        con = connect()
        f = {"from": date_from.strip(), "to": to.strip(), "cam": cam.strip(),
             "faces": faces.strip(), "who": who.strip(),
             "dsrc": dsrc.strip(), "psrc": psrc.strip()}
        page = max(0, page)
        total_all = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        embedded = con.execute(
            "SELECT COUNT(*) FROM image_vectors").fetchone()[0]

        r = query.resolve(con, q, place_counts=subtree_counts(con))
        where, args = _where(con, r, f)
        # This exact page, so a photograph opened from it can lead back here
        # rather than to the front -- including the page number and the filters.
        here = _qs(q, f, page=page or None, more=more or None)
        head = chip_row(r, f) + offer_row(con, r, f) + filter_row(q, f)

        cols = ("a.id, a.content_hash, a.taken_at, a.taken_at_precision, "
                "a.camera_model")
        clause = f" WHERE {where}" if where else ""

        # Nothing to rank by: the answer is exact, so it is every photograph
        # that matches, newest first, and the count is the real count.
        if not r.text:
            total = con.execute(
                f"SELECT COUNT(*) FROM assets a{clause}", args).fetchone()[0]
            rows = con.execute(
                f"SELECT {cols} FROM assets a{clause} "
                "ORDER BY a.taken_at DESC LIMIT ? OFFSET ?",
                [*args, PAGE_SIZE, page * PAGE_SIZE]).fetchall()
            # Genuinely nothing indexed -- not merely nothing matching, which a
            # filter can produce on a full library and which must not be
            # reported as an empty archive.
            if not total_all:
                return HTMLResponse(PAGE.format(
                    title="Kuva-arkisto", q="", focus=" autofocus",
                    placeholder="kids on a beach at sunset",
                    body='<div class="empty"><p>Nothing indexed yet.</p>'
                         '<p><code>mediaidx add &lt;folder&gt;</code></p></div>'))
            if not total:
                # "0 photographs, most recent first" is not an answer. Say what
                # was asked for and, where a word read two ways, that the other
                # reading is a link away -- which is the whole of the Hartola
                # case: three photographs as an event, 1,286 as a municipality.
                # A filter alone leaves no chips to name, so there is nothing
                # to list back and the sentence has to stand without one.
                names = [html.escape(t.label) for t in r.terms]
                asked = ("all of that" if not names else names[0] if
                         len(names) == 1 else
                         ", ".join(names[:-1]) + " and " + names[-1])
                body = (head + '<div class="empty">'
                        f'<p>No photograph has {asked or "all of that"}.</p>'
                        '</div>')
                return HTMLResponse(PAGE.format(
                    title=f"{q} — Kuva-arkisto", q=html.escape(q), body=body,
                    focus=" autofocus", placeholder="kids on a beach at sunset"))
            what = ("photographs" if where else
                    f"photographs &middot; {embedded:,} searchable")
            meta = (f"{total:,} {what} &middot; most recent first"
                    if where or not q else
                    f"{total_all:,} {what} &middot; most recent first")
            body = (head + f'<div class="meta">{meta}</div>'
                    f'<div class="grid">'
                    f'{"".join(card(x, back=here) for x in rows)}</div>'
                    + pager(q, f, page, total, PAGE_SIZE))
            return HTMLResponse(PAGE.format(
                title=f"{q} — Kuva-arkisto" if q else "Kuva-arkisto",
                q=html.escape(q), body=body, focus=" autofocus",
                placeholder="kids on a beach at sunset"))

        if embedded == 0:
            # The strips still answer, even with nothing to rank: a partial
            # name is exactly what they are for, and having no vectors is no
            # reason to stop finding people by name.
            return HTMLResponse(PAGE.format(
                title=f"{q} — Kuva-arkisto", q=html.escape(q), focus=" autofocus",
                placeholder="kids on a beach at sunset",
                body=head + people_matching(con, r.text)
                     + places_matching(con, r.text)
                     + events_matching(con, r.text)
                     + '<div class="empty"><p>No search vectors yet.</p>'
                       '<p><code>mediaidx embed</code></p></div>'))

        # Narrow first, then rank inside it (§7.2).
        cand = None
        if where:
            cand = [x[0] for x in con.execute(
                f"SELECT a.id FROM assets a WHERE {where}", args)]
            if not cand:
                strips = (people_matching(con, r.text)
                          + places_matching(con, r.text)
                          + events_matching(con, r.text))
                return HTMLResponse(PAGE.format(
                    title=f"{q} — Kuva-arkisto", q=html.escape(q),
                    focus=" autofocus", placeholder="kids on a beach at sunset",
                    body=head + strips + '<div class="empty">'
                         '<p>Nothing matches all of that.</p></div>'))

        vec = encoder().encode_text([r.text])[0]
        hits = embed.search(con, vec, limit=MAX_RANKED,
                            vec_ok=state["vec_ok"], candidate_ids=cand)
        answers = embed.cut([s for _, s in hits])
        keep = hits if more else hits[:answers]
        window = keep[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

        # The strips answer a different question from the chips: a chip needs
        # the whole name, so "emm" resolves to nothing, and the strip is what
        # turns it into Emmi. They are fed the description alone, so anything
        # already resolved into a chip is not also offered as a suggestion.
        strips = (people_matching(con, r.text) + places_matching(con, r.text)
                  + events_matching(con, r.text))

        if not window:
            return HTMLResponse(PAGE.format(
                title=f"{q} — Kuva-arkisto", q=html.escape(q),
                focus=" autofocus", placeholder="kids on a beach at sunset",
                body=head + strips +
                     '<div class="empty"><p>No matching photographs.</p></div>'))

        ids = [a for a, _ in window]
        scores = dict(window)
        by_id = {x["id"]: x for x in con.execute(
            f"SELECT {cols} FROM assets a "
            f"WHERE a.id IN ({','.join('?' * len(ids))})", ids)}
        cards = "".join(card(by_id[i], scores[i], back=here)
                        for i in ids if i in by_id)

        scope = f"of {len(cand):,} matching" if cand else f"of {embedded:,} searchable"
        held = len(hits) - answers
        meta = (f'{len(keep):,} for &ldquo;{html.escape(r.text)}&rdquo; {scope}'
                + (f' &middot; {held:,} weaker held back' if held and not more
                   else ""))
        tail = pager(q, f, page, len(keep), PAGE_SIZE,
                     more=more, held=held if not more else 0)
        body = (head + strips + f'<div class="meta">{meta}</div>'
                f'<div class="grid">{cards}</div>' + tail)
        return HTMLResponse(PAGE.format(
            title=f"{q} — Kuva-arkisto", q=html.escape(q), body=body,
            focus=" autofocus", placeholder="kids on a beach at sunset"))

    def pager(q, f, page, total, size, *, more=0, held=0) -> str:
        """Next and previous, and the way past the cut.

        Nothing below the cut is discarded; it is one link away, and the link
        says how much is behind it (§7.3).
        """
        bits = []
        if page > 0:
            bits.append(f'<a href="{_qs(q, f, page=page - 1, more=more or None)}">'
                        '&larr; previous</a>')
        if (page + 1) * size < total:
            bits.append(f'<a href="{_qs(q, f, page=page + 1, more=more or None)}">'
                        'next page &rarr;</a>')
        if held:
            bits.append(f'<a href="{_qs(q, f, more=1)}">'
                        f'show {held:,} weaker</a>')
        if total > size:
            first = page * size + 1
            bits.append(f'<span class="rest">{first:,}&ndash;'
                        f'{min(total, (page + 1) * size):,} of {total:,}</span>')
        if total:
            # Deliberately last and deliberately plain: it is the one control
            # here that reaches across the network for originals.
            bits.append(f'<a href="{_qs(q, f).replace("/?", "/zip?", 1)}" '
                        'class="rest" style="border:0;padding:9px 4px">'
                        'take these as a zip</a>')
        return f'<div class="more">{"".join(bits)}</div>' if bits else ""

    @app.get("/thumb/{size}/{content_hash}")
    def thumb(size: str, content_hash: str):
        if size not in images.THUMB_SIZES:
            return Response(status_code=404)
        p = images.thumb_path(cache_dir, content_hash, size)
        if not p.exists():
            return Response(status_code=404)
        return FileResponse(p, media_type="image/webp",
                            headers={"Cache-Control": "public, max-age=604800"})

    # A result set taken away as one file. This is the only thing in the
    # application that reads many originals from the NAS on demand, over the
    # link that is already the slowest part of the whole system (§1.2), so it
    # is the only thing that asks first.
    ZIP_MAX_FILES = 500

    # Measured on this link: ~3.2 photographs a second at roughly 3.5 MB each.
    # Used to turn a size into a wait, because "1.7 GB" means less to a person
    # standing at a laptop than "about four minutes".
    LINK_MB_PER_S = 11.0

    def _zip_items(con, where, args, limit=ZIP_MAX_FILES) -> list:
        """(asset_id, path, size, name) for a result set, newest first.

        Sizes come from the index, not from the disk, so the total can be shown
        before anything is fetched -- the point of asking at all.
        """
        rows = con.execute(
            f"""SELECT a.id, a.taken_at,
                       (SELECT f.path FROM files f WHERE f.asset_id = a.id
                         ORDER BY f.missing_since IS NOT NULL, f.path LIMIT 1) AS path,
                       (SELECT f.size_bytes FROM files f WHERE f.asset_id = a.id
                         ORDER BY f.missing_since IS NOT NULL, f.path LIMIT 1) AS size
                  FROM assets a{f" WHERE {where}" if where else ""}
                 ORDER BY a.taken_at DESC LIMIT ?""", [*args, limit]).fetchall()
        seen: dict = {}
        out = []
        for r in rows:
            if not r["path"]:
                continue
            name = r["path"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
            # Two folders may hold the same filename; inside one archive they
            # cannot, so the later one is numbered rather than overwritten.
            n = seen.get(name.lower(), 0)
            seen[name.lower()] = n + 1
            if n:
                stem, _, ext = name.rpartition(".")
                name = f"{stem}-{n}.{ext}" if stem else f"{name}-{n}"
            out.append((r["id"], r["path"], r["size"] or 0, name))
        return out

    def _size_words(n: int) -> str:
        mb = n / (1024 * 1024)
        return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:,.0f} MB"


    class _Sink:
        """Somewhere for zipfile to write that is a stream, not a file.

        Given something it cannot seek in, zipfile writes each entry's size and
        checksum *after* the data, in a descriptor, which is exactly what makes
        a zip streamable. So the archive is never assembled anywhere -- it is
        handed out in pieces as the originals are read.
        """

        def __init__(self):
            self.buf = bytearray()
            self.pos = 0

        def write(self, b) -> int:
            self.buf += b
            self.pos += len(b)
            return len(b)

        def tell(self) -> int:
            return self.pos

        def flush(self) -> None:
            pass

        def seekable(self) -> bool:
            return False

        def take(self) -> bytes:
            b = bytes(self.buf)
            self.buf.clear()
            return b

    @app.get("/zip")
    def zip_results(q: str = Query(default=""), go: int = 0,
                    date_from: str = Query(default="", alias="from"),
                    to: str = Query(default=""), cam: str = Query(default=""),
                    faces: str = Query(default=""), who: str = Query(default=""),
                    dsrc: str = Query(default=""), psrc: str = Query(default="")):
        con = connect()
        f = {"from": date_from.strip(), "to": to.strip(), "cam": cam.strip(),
             "faces": faces.strip(), "who": who.strip(),
             "dsrc": dsrc.strip(), "psrc": psrc.strip()}
        r = query.resolve(con, q, place_counts=subtree_counts(con))
        where, args = _where(con, r, f)
        items = _zip_items(con, where, args)

        total = con.execute(
            f"SELECT COUNT(*) FROM assets a{f' WHERE {where}' if where else ''}",
            args).fetchone()[0]
        bytes_ = sum(s for _, _, s, _ in items)

        if not go:
            if not items:
                return HTMLResponse(PAGE.format(
                    title="Download — Kuva-arkisto", q=html.escape(q), focus="",
                    placeholder="kids on a beach at sunset",
                    body='<div class="empty"><p>Nothing to download.</p></div>'))
            minutes = bytes_ / (1024 * 1024) / LINK_MB_PER_S / 60
            capped = ("" if total <= ZIP_MAX_FILES else
                      f'<p>That search matches {total:,}; the first '
                      f'{ZIP_MAX_FILES:,} are taken, newest first. Narrow it '
                      'to reach the rest.</p>')
            wait = ("under a minute" if minutes < 1
                    else f"about {minutes:.0f} minutes")
            body = (
                '<div class="empty" style="max-width:620px;text-align:left">'
                f'<p><b>{len(items):,} photographs, {_size_words(bytes_)}.</b></p>'
                f'{capped}'
                '<p>These come from the archive itself rather than from the '
                f'thumbnail cache, so they cross the network: {wait} at the '
                'speed this link has been measured at. Nothing else in the '
                'application reads originals in bulk, which is why this asks '
                'first.</p>'
                f'<p><a href="{_qs(q, f, go=1).replace("/?", "/zip?")}" '
                'style="color:var(--accent)"><b>Start the download</b></a>'
                f' &nbsp; <a href="{_qs(q, f)}">go back</a></p></div>')
            return HTMLResponse(PAGE.format(
                title="Download — Kuva-arkisto", q=html.escape(q), body=body,
                focus="", placeholder="kids on a beach at sunset"))

        def stream():
            sink = _Sink()
            with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED) as z:
                for _, path, _, name in items:
                    try:
                        src = open(pathutil.long_path(path), "rb")
                    except OSError:
                        continue          # a copy that has gone; the rest still go
                    with src, z.open(name, "w") as dst:
                        while True:
                            chunk = src.read(1 << 20)
                            if not chunk:
                                break
                            dst.write(chunk)
                            if sink.buf:
                                yield sink.take()
                    if sink.buf:
                        yield sink.take()
            if sink.buf:
                yield sink.take()

        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        return StreamingResponse(
            stream(), media_type="application/zip",
            headers={"Content-Disposition":
                     f'attachment; filename="kuva-arkisto-{stamp}.zip"'})

    def _reachable(con, asset_id: int):
        """The first path holding this photograph that is actually there.

        An asset has as many paths as copies of it, and a NAS that is asleep,
        unplugged or renamed has none of them. Trying each in turn and saying
        plainly when none answers is better than a broken image and a guess.
        """
        for f in con.execute("SELECT path FROM files WHERE asset_id = ? "
                             "ORDER BY missing_since IS NOT NULL, path",
                             (asset_id,)):
            try:
                if Path(pathutil.long_path(f["path"])).exists():
                    return f["path"]
            except OSError:
                continue
        return None

    @app.get("/full/{asset_id}")
    def full(asset_id: int, download: int = 0):
        """The original, at full size, from wherever it actually lives.

        Browsing never pulls a full-size file across the network (§7.5); a
        deliberate click is not browsing. It is served rather than linked as
        `file://`, which browsers refuse to follow from a served page.
        """
        con = connect()
        path = _reachable(con, asset_id)
        if path is None:
            row = con.execute("SELECT id FROM assets WHERE id = ?",
                              (asset_id,)).fetchone()
            if row is None:
                return HTMLResponse("<p>not found</p>", status_code=404)
            return HTMLResponse(
                "<p>Not reachable. Every known copy of this photograph is on "
                "a drive that is not answering.</p>", status_code=503)
        kind, _ = mimetypes.guess_type(path)
        name = path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        return FileResponse(
            pathutil.long_path(path), media_type=kind or "image/jpeg",
            filename=name if download else None)

    def _fact(k, v, *, note="", edit="") -> str:
        if v in (None, "") and not edit:
            return ""
        return (f'<tr><td style="color:var(--ink-3);padding:5px 16px 5px 0;'
                f'vertical-align:top;white-space:nowrap">{k}</td>'
                f'<td style="padding:5px 0">{v}'
                + (f' <span style="color:var(--ink-3)">&mdash; {note}</span>'
                   if note else "")
                + (f'<div class="fix">{edit}</div>' if edit else "")
                + '</td></tr>')

    def _fix_form(asset_id, kind, back, *, value="", placeholder="", extra="",
                  wide=260) -> str:
        return (
            f'<form method="post" action="/photo/{asset_id}/{kind}">'
            f'<input type="hidden" name="back" value="{html.escape(back)}">'
            f'{extra}'
            f'<input type="text" name="value" value="{html.escape(value)}" '
            f'placeholder="{html.escape(placeholder)}" '
            f'style="min-width:{wide}px">'
            '<button type="submit">put it right</button></form>')

    def _undo_form(asset_id, kind, back, *, idx=None) -> str:
        return (f'<form method="post" action="/photo/{asset_id}/uncorrect">'
                f'<input type="hidden" name="back" value="{html.escape(back)}">'
                f'<input type="hidden" name="kind" value="{kind}">'
                + (f'<input type="hidden" name="idx" value="{idx}">'
                   if idx is not None else "")
                + '<button class="ghost" type="submit">undo</button></form>')

    def _questions(con, asset_id: int) -> str:
        """Anything in the queue that this photograph is waiting on.

        A photograph belongs to an event and its faces to clusters, and any of
        them may be a question nobody has answered yet. Reaching them from here
        saves hunting for the one that mentions this picture.
        """
        rows = con.execute(
            """SELECT q.id, q.kind, q.suggestion FROM review_queue q
                WHERE q.status = 'open' AND (
                  (q.kind = 'event' AND q.ref_id IN (
                      SELECT event_id FROM event_assets WHERE asset_id = ?))
               OR (q.kind = 'face_cluster' AND q.ref_id IN (
                      SELECT cluster_id FROM faces
                       WHERE asset_id = ? AND cluster_id IS NOT NULL)))""",
            (asset_id, asset_id)).fetchall()
        if not rows:
            return ""
        what = ("one open question" if len(rows) == 1
                else f"{len(rows)} open questions")
        return ('<div class="meta" style="padding-left:0">'
                f'<a href="/review" style="color:var(--accent)">{what}</a> '
                'about this photograph</div>')

    @app.get("/photo/{asset_id}", response_class=HTMLResponse)
    def photo(asset_id: int, back: str = Query(default="/"),
              msg: str = Query(default="")):
        con = connect()
        a = con.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if a is None:
            return HTMLResponse("<p>not found</p>", status_code=404)
        back = safe_back(back)
        h = a["content_hash"]
        fixed = {(r["kind"], r["face_idx"]): r
                 for r in corrections.for_asset(con, h)}

        files = con.execute(
            "SELECT path FROM files WHERE asset_id = ? ORDER BY path",
            (asset_id,)).fetchall()

        when, typed = "unknown", ""
        if a["taken_at"]:
            d = datetime.fromtimestamp(a["taken_at"])
            p = a["taken_at_precision"]
            when = (f"{d:%Y}" if p == "year" else f"{d:%B %Y}" if p == "month"
                    else f"{d.day} {d:%B %Y}" if p == "day"
                    else f"{d.day} {d:%B %Y}, {d:%H:%M}")
            typed = f"{d:%Y-%m-%d}"

        # Every fact says where it came from. A date and a place are guesses of
        # very different quality, and one that arrived from a folder name should
        # never read like one measured by a satellite (§8.2).
        date_note = f'{a["taken_at_source"]}, {a["taken_at_precision"]}'
        if ("date", -1) in fixed:
            date_note = "put right by hand"
        info = "".join([
            _fact("taken", html.escape(when), note=date_note,
                  edit=(_undo_form(asset_id, "date", back)
                        if ("date", -1) in fixed else
                        _fix_form(asset_id, "date", back, value=typed,
                                  placeholder="1998-07, or kesä 1998"))),
            _fact("camera", html.escape(" ".join(filter(
                None, [a["camera_make"], a["camera_model"]])))),
            _fact("size", f'{a["width"]}x{a["height"]}' if a["width"] else ""),
            _fact("gps", (f'{a["gps_lat"]:.5f}, {a["gps_lon"]:.5f}'
                          if a["gps_lat"] is not None else "")),
            _place_fact(con, a, asset_id, back, ("place", -1) in fixed),
            _event_row(con, asset_id),
            _fact("source", html.escape(a["source_hint"] or "")),
            _fact("conflict", html.escape(a["conflict_note"] or "")),
            _fact("hash", h[:24] + "&hellip;"),
        ])

        # Who is in it, and the means to say otherwise. Correcting a face here
        # is one row and outranks the clusterer for good (§4.7).
        in_photo = con.execute(
            """SELECT fa.idx, fa.cluster_id, p.id AS pid, p.name
               FROM faces fa LEFT JOIN persons p ON p.id = fa.person_id
               WHERE fa.asset_id = ? ORDER BY fa.idx""",
            (asset_id,)).fetchall()
        who = ""
        if in_photo:
            cards = []
            for f in in_photo:
                fix = fixed.get(("person", f["idx"]))
                name = fix["value"] if fix else f["name"]
                href = (f'/person/{f["pid"]}' if f["pid"] and not fix else
                        (f'/cluster/{f["cluster_id"]}' if f["cluster_id"]
                         and not fix else None))
                label = html.escape(name or "not named")
                if href:
                    label = f'<a href="{href}">{label}</a>'
                cards.append(
                    '<figure class="mug">'
                    f'<img src="/face/{h}/{f["idx"]}" alt="">'
                    f'<figcaption><b>{label}</b>'
                    + ('<span>by hand</span>' if fix else "")
                    + '</figcaption>'
                    + (_undo_form(asset_id, "person", back, idx=f["idx"]) if fix
                       else _fix_form(asset_id, "person", back,
                                      placeholder="who is this?", wide=150,
                                      extra=f'<input type="hidden" name="idx" '
                                            f'value="{f["idx"]}">'))
                    + '</figure>')
            who = f'<div class="mugs">{"".join(cards)}</div>'

        paths = "".join(
            f'<div style="color:var(--ink-3);font-size:12px;'
            f'font-family:ui-monospace,monospace;padding:1px 0;'
            f'word-break:break-all">{html.escape(f["path"])}</div>'
            for f in files)

        note = (f'<div class="offer" style="padding-left:0">{html.escape(msg)}</div>'
                if msg else "")
        body = (
            '<div style="max-width:1100px;margin:0 auto;padding:20px">'
            f'<a href="{html.escape(back)}" style="color:var(--accent)">'
            '&larr; back</a>'
            f'<img src="/thumb/m/{h}" '
            'style="display:block;max-width:100%;margin:14px 0;border-radius:3px">'
            '<div class="more" style="padding:0 0 14px">'
            f'<a href="/full/{asset_id}">see the original</a>'
            f'<a href="/full/{asset_id}?download=1">download it</a>'
            '</div>'
            + note + _questions(con, asset_id) + who
            + f'<table style="font-size:14px">{info}</table>'
            f'<div style="margin-top:14px">{paths}</div></div>')
        return HTMLResponse(PAGE.format(
            title=f"{when} — Kuva-arkisto", q="", body=body, focus="",
            placeholder="kids on a beach at sunset"))

    def _hash_of(con, asset_id: int):
        r = con.execute("SELECT content_hash FROM assets WHERE id = ?",
                        (asset_id,)).fetchone()
        return None if r is None else r["content_hash"]

    def _back_to(asset_id, back, msg="") -> RedirectResponse:
        to = f"/photo/{asset_id}?back={quote_plus(back)}"
        if msg:
            to += f"&msg={quote_plus(msg)}"
        return RedirectResponse(to, status_code=303)

    @app.post("/photo/{asset_id}/date")
    def fix_date(asset_id: int, value: str = Form(default=""),
                 back: str = Form(default="/")):
        con = connect()
        h = _hash_of(con, asset_id)
        if h is None:
            return HTMLResponse("<p>not found</p>", status_code=404)
        text = value.strip()
        if text and query.parse_date(text) is None:
            return _back_to(asset_id, back,
                            f"{text!r} is not a date I can read. "
                            "Try 1998, 1998-07, or kesä 1998.")
        corrections.record(con, "date", h, text or None)
        con.commit()
        return _back_to(asset_id, back)

    @app.post("/photo/{asset_id}/place")
    def fix_place(asset_id: int, value: str = Form(default=""),
                  back: str = Form(default="/")):
        con = connect()
        h = _hash_of(con, asset_id)
        if h is None:
            return HTMLResponse("<p>not found</p>", status_code=404)
        text = value.strip()
        path = None
        if text:
            # Whichever place of that name this library knows best, which is the
            # same rule the search box uses (§7.1).
            counts = subtree_counts(con)
            found = sorted(places_mod.lookup(con, text, limit=12),
                           key=lambda r: -counts.get(r["id"], 0))
            if not found:
                return _back_to(asset_id, back,
                                f"No place called {text!r} in the gazetteer.")
            path = found[0]["path"]
        corrections.record(con, "place", h, path)
        con.commit()
        return _back_to(asset_id, back)

    @app.post("/photo/{asset_id}/person")
    def fix_person(asset_id: int, idx: int = Form(...),
                   value: str = Form(default=""), back: str = Form(default="/")):
        con = connect()
        h = _hash_of(con, asset_id)
        if h is None:
            return HTMLResponse("<p>not found</p>", status_code=404)
        row = con.execute("SELECT bbox FROM faces WHERE asset_id = ? AND idx = ?",
                          (asset_id, idx)).fetchone()
        if row is None:
            return HTMLResponse("<p>no such face</p>", status_code=404)
        name = value.strip()
        if name:
            cluster.person_id_for(con, name)     # creates the person if new
        corrections.record(con, "person", h, name or None, face_idx=idx,
                           face_bbox=json.loads(row["bbox"]))
        con.commit()
        return _back_to(asset_id, back)

    @app.post("/photo/{asset_id}/uncorrect")
    def uncorrect(asset_id: int, kind: str = Form(...),
                  idx: int = Form(default=-1), back: str = Form(default="/")):
        con = connect()
        h = _hash_of(con, asset_id)
        if h is None:
            return HTMLResponse("<p>not found</p>", status_code=404)
        corrections.clear(con, kind, h, face_idx=idx)
        con.commit()
        return _back_to(
            asset_id, back,
            "Withdrawn. The application's own answer comes back the next time "
            "it works one out.")

    def _place_fact(con, a, asset_id, back, corrected: bool) -> str:
        """Where this was taken, and -- always -- on what evidence.

        A coordinate and a folder name are not the same kind of claim, and a
        third kind arrives once somebody says so themselves. The row names which
        it is rather than letting a guess look like a measurement.
        """
        where, note = "", ""
        if a["place_id"]:
            p = con.execute("SELECT id, name, path FROM places WHERE id = ?",
                            (a["place_id"],)).fetchone()
            if p is not None:
                trail = " · ".join(reversed(p["path"].split("/")))
                where = (f'<a href="/place/{p["id"]}" style="color:var(--accent)">'
                         f'{html.escape(trail)}</a>')
                note = {"gps": "from the coordinate",
                        "folder": "from the folder name",
                        "corrected": "put right by hand"}.get(
                            a["place_source"], a["place_source"] or "")
        elif corrected:
            where, note = "nowhere", "put right by hand"
        edit = (_undo_form(asset_id, "place", back) if corrected else
                _fix_form(asset_id, "place", back,
                          placeholder="a town, or empty for nowhere"))
        return _fact("place", where, note=note, edit=edit)

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
                f'<div class="grid">'
                f'{"".join(card(r, back=f"/place/{place_id}") for r in rows)}'
                '</div>')
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
                f'<div class="grid">'
                f'{"".join(card(r, back=f"/person/{person_id}") for r in rows)}'
                '</div>')
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
                + '<div class="grid">'
                + "".join(card(r, back=f"/event/{event_id}") for r in rows)
                + '</div>')
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
                    '<div class="grid">'
                    + "".join(card(r, back="/review") for r in sample)
                    + '</div>' + KEYS)
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
