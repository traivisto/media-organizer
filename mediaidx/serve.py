"""The local search UI.

Server-rendered, no build step, no framework on the client. The page is scanned
and operated rather than read, so the grid is the interface and everything else
stays out of its way.

Nothing here runs a model except the query encoder, which turns typed text into
one vector. Browsing touches only photos.db and the thumbnail cache.
"""

from __future__ import annotations

import gc
import html
import io
import ipaddress
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import sys
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import (parse_qs, parse_qsl, quote_plus, urlencode,
                          urlparse)

import numpy as np
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse, Response,
                               StreamingResponse)

from . import cluster
from . import corrections
from . import config as config_mod
from . import db, embed, images
from . import events as events_mod
from . import odd as odd_mod
from . import paths as pathutil
from . import places as places_mod
from . import query
from . import queue as queue_mod
from . import textmodel

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
/* One palette, named the way the inline styles across the pages already use
   it (--ink-3, --accent, --rule ...), so restyling the frame restyles them. */
:root {{
  --ground:#f6f7f9; --surface:#ffffff; --soft:#eef1f5; --ink:#15181d;
  --ink-2:#4a5260; --ink-3:#737c8b; --rule:#e4e7ec; --accent:#2563eb;
  --accent-soft:#e3ecff; --on-accent:#ffffff;
  --person:#6d28d9; --person-soft:#efe7ff; --date:#0f766e; --date-soft:#dff3ef;
  --place:#c2410c; --place-soft:#fdece1; --event:#be185d; --event-soft:#fce7f1;
  --no:#dc2626; --no-soft:#fdecec;
  --shadow:0 1px 2px rgba(16,24,40,.06),0 8px 24px rgba(16,24,40,.09);
}}
@media (prefers-color-scheme:dark) {{
  :root {{
    --ground:#0f1115; --surface:#171a1f; --soft:#1f232a; --ink:#e8eaee;
    --ink-2:#b3bac5; --ink-3:#8a93a0; --rule:#2a2f37; --accent:#6b9cff;
    --accent-soft:#1b2848; --on-accent:#0b1020;
    --person:#c9b0ff; --person-soft:#2a2141; --date:#63d4c4; --date-soft:#14302c;
    --place:#ffab7d; --place-soft:#3a2418; --event:#f7a8cf; --event-soft:#3b1a2c;
    --no:#ff8f8f; --no-soft:#3d1a1c;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.5);
  }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--ground); color:var(--ink);
  font:14.5px/1.5 "Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,sans-serif; }}
a {{ color:inherit; text-decoration:none; }}
form {{ margin:0; }}
svg.ic {{ width:17px; height:17px; flex:none; fill:none; stroke:currentColor;
  stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }}
button {{ font:inherit; padding:9px 16px; font-size:14px; font-weight:600;
  cursor:pointer; background:var(--accent); color:var(--on-accent);
  border:1px solid transparent; border-radius:10px; }}
button:hover {{ filter:brightness(1.08); }}
.ghost {{ background:var(--surface); color:var(--ink-2); border-color:var(--rule); }}
.ghost:hover {{ filter:none; color:var(--ink); border-color:var(--ink-3); }}
input[type=text], input[type=number] {{ font:inherit; font-size:14.5px;
  color:var(--ink); background:var(--surface); border:1px solid var(--rule);
  border-radius:10px; padding:8px 12px; }}
input[type=text]:focus, input[type=number]:focus {{ outline:0;
  border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-soft); }}

/* The frame: the box, and everything else behind one menu. */
header {{ position:sticky; top:0; z-index:10; background:var(--surface);
  border-bottom:1px solid var(--rule); }}
.bar {{ max-width:1500px; margin:0 auto; padding:12px 20px; display:flex;
  gap:16px; align-items:center; }}
.logo {{ display:flex; gap:9px; align-items:center; font-weight:650;
  font-size:16px; white-space:nowrap; }}
.logo span {{ width:32px; height:32px; border-radius:9px; display:grid;
  place-items:center; color:#fff;
  background:linear-gradient(135deg,#3b82f6,#7c3aed); }}
.search {{ flex:1; max-width:780px; display:flex; align-items:center; gap:8px;
  height:46px; padding:0 6px 0 16px; background:var(--soft);
  border:1px solid transparent; border-radius:999px; }}
.search:focus-within {{ background:var(--surface); border-color:var(--accent);
  box-shadow:0 0 0 4px var(--accent-soft); }}
.search > svg.ic {{ color:var(--ink-3); }}
.search input {{ flex:1; min-width:0; border:0; outline:0; background:transparent;
  color:var(--ink); font:inherit; font-size:16px; }}
.search input::-webkit-search-cancel-button {{ display:none; }}
.search .clear {{ display:grid; place-items:center; width:30px; height:30px;
  border-radius:50%; color:var(--ink-3); }}
.search .clear:hover {{ background:var(--rule); color:var(--ink); }}
.search button {{ height:34px; padding:0 18px; border-radius:999px; }}
.spacer {{ flex:1; }}
details.pop {{ position:relative; }}
details.pop > summary {{ list-style:none; cursor:pointer; display:inline-flex;
  gap:7px; align-items:center; height:38px; padding:0 12px; border-radius:10px;
  border:1px solid var(--rule); background:var(--surface); color:var(--ink-2);
  font-weight:550; white-space:nowrap; user-select:none; }}
details.pop > summary::-webkit-details-marker {{ display:none; }}
details.pop > summary:hover, details.pop[open] > summary {{ color:var(--ink);
  border-color:var(--ink-3); }}
.menu {{ position:absolute; right:0; top:46px; width:240px; z-index:20;
  background:var(--surface); border:1px solid var(--rule); border-radius:12px;
  box-shadow:var(--shadow); padding:6px; }}
.menu a {{ display:flex; gap:10px; align-items:center; padding:9px 10px;
  border-radius:8px; font-weight:500; }}
.menu a:hover {{ background:var(--soft); }}
.menu a svg.ic {{ color:var(--ink-3); }}
.menu a .badge {{ margin-left:auto; }}
.menu hr {{ border:0; border-top:1px solid var(--rule); margin:6px 4px; }}
.badge {{ display:inline-grid; place-items:center; min-width:19px; height:19px;
  padding:0 6px; border-radius:10px; background:var(--accent);
  color:var(--on-accent); font-size:11px; font-weight:700; }}
@media (max-width:720px) {{
  .logo b, details.browse > summary b {{ display:none; }}
  .bar {{ gap:10px; padding:10px 12px; }}
}}
/* The header is sticky, so focusing the search box makes the browser scroll it
   clear -- taking the first line of the page under the header with it. The
   search pages want the caret; every other page wants its own first line, so
   the attribute is filled in per page rather than baked in here. */

/* What the box understood, and every filter that is on. These are the
   interface, not decoration: each one states a criterion and carries the link
   that removes it, so the whole thing works as plain anchors. */
.chips {{ position:relative; max-width:1500px; margin:0 auto; padding:14px 20px 0;
  display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
.crit {{ display:inline-flex; gap:7px; align-items:center; height:34px;
  padding:0 5px 0 11px; border-radius:999px; background:var(--soft);
  color:var(--ink); font-size:13.5px; font-weight:550; white-space:nowrap;
  border:1px solid transparent; }}
.crit i {{ font-style:normal; display:inline-flex; }}
.crit a:hover {{ text-decoration:underline; }}
.crit a.x {{ display:grid; place-items:center; width:24px; height:24px;
  border-radius:50%; font-size:17px; line-height:1; opacity:.6; }}
.crit a.x:hover {{ opacity:1; background:rgba(127,127,127,.22);
  text-decoration:none; }}
.crit[data-kind=person] {{ background:var(--person-soft); color:var(--person);
  padding-left:3px; }}
.crit[data-kind=person] img, .crit .av {{ width:28px; height:28px;
  border-radius:50%; object-fit:cover; background:var(--rule); display:grid;
  place-items:center; }}
.crit[data-kind=date] {{ background:var(--date-soft); color:var(--date); }}
.crit[data-kind=place] {{ background:var(--place-soft); color:var(--place); }}
.crit[data-kind=event] {{ background:var(--event-soft); color:var(--event); }}
.crit.filter {{ background:var(--surface); border-color:var(--rule);
  color:var(--ink-2); }}
.crit[data-kind=not] {{ background:var(--no-soft); color:var(--no);
  padding-left:3px; }}
.crit[data-kind=not] img {{ width:28px; height:28px; border-radius:50%;
  object-fit:cover; filter:grayscale(1); opacity:.75; }}
.crit[data-kind=mode] {{ background:var(--surface); border-color:var(--ink-3); }}
.crit a.swap {{ display:grid; place-items:center; width:24px; height:24px;
  border-radius:50%; opacity:.45; }}
.crit a.swap:hover {{ opacity:1; background:rgba(127,127,127,.22); }}
.crit a.swap svg.ic {{ width:15px; height:15px; }}
.rest {{ color:var(--ink-3); font-size:13.5px; }}
.rest b {{ color:var(--ink-2); font-weight:550; }}
/* The auditing filters, one button at the end of the chips. Positioned against
   the row rather than the button, so the form opens full width beneath it. */
details.filters {{ position:static; }}
details.filters > summary {{ height:34px; border-radius:999px;
  border-style:dashed; font-size:13.5px; }}
details.filters form {{ position:absolute; left:20px; right:20px;
  top:calc(100% + 8px); z-index:8; max-width:1080px; background:var(--surface);
  border:1px solid var(--rule); border-radius:14px; box-shadow:var(--shadow);
  padding:18px 20px; display:flex; gap:22px; align-items:flex-start;
  flex-wrap:wrap; }}
details.filters label {{ display:flex; flex-direction:column; gap:5px;
  font-size:11.5px; font-weight:650; color:var(--ink-3);
  text-transform:uppercase; letter-spacing:.05em; }}
details.filters input, details.filters select {{ height:38px; padding:0 10px;
  font:inherit; font-size:14px; font-weight:400; color:var(--ink);
  background:var(--surface); border:1px solid var(--rule); border-radius:10px;
  text-transform:none; letter-spacing:0; }}
details.filters .clearall {{ color:var(--ink-3); padding:9px 4px; }}
details.filters .clearall:hover {{ color:var(--ink); }}
details.filters fieldset {{ border:0; margin:0; padding:0; flex:1;
  min-width:230px; display:flex; flex-direction:column; gap:8px; }}
details.filters legend {{ padding:0 0 8px; font-size:11.5px; font-weight:650;
  color:var(--ink-3); text-transform:uppercase; letter-spacing:.05em; }}
details.filters label.choice {{ flex-direction:row; align-items:flex-start;
  gap:10px; padding:8px 11px; border:1px solid var(--rule); border-radius:10px;
  font-size:14px; font-weight:550; color:var(--ink); text-transform:none;
  letter-spacing:0; cursor:pointer; }}
details.filters label.choice:has(input:checked) {{ border-color:var(--accent);
  background:var(--accent-soft); }}
details.filters label.choice:has(input:disabled) {{ opacity:.5; cursor:default; }}
details.filters label.choice input {{ height:auto; margin:3px 0 0; padding:0;
  accent-color:var(--accent); }}
details.filters label.choice small {{ display:block; font-size:12px;
  font-weight:400; color:var(--ink-3); }}
details.filters .pickrow {{ display:flex; gap:8px; }}
details.filters .pickrow > * {{ flex:1; min-width:0; }}
details.filters select:disabled {{ background:var(--soft); color:var(--ink-3); }}
details.filters .hint {{ margin:0; font-size:12px; color:var(--ink-3); }}
details.filters .factions {{ flex-basis:100%; display:flex; gap:12px;
  align-items:center; }}
/* An unsure reading: shown, never applied. */
.offer {{ max-width:1500px; margin:0 auto; padding:10px 20px 0; font-size:13.5px;
  color:var(--ink-2); }}
.offer a {{ color:var(--accent); font-weight:550; }}

.meta {{ max-width:1500px; margin:0 auto; padding:14px 20px 0; color:var(--ink-3);
  font-size:13.5px; }}
.meta b {{ color:var(--ink); }}
.crumb {{ max-width:1500px; margin:0 auto; padding:14px 20px 0; font-size:13.5px;
  color:var(--ink-3); }}
.crumb a {{ color:var(--accent); }}
.empty {{ max-width:640px; margin:70px auto; padding:0 20px; text-align:center;
  color:var(--ink-3); }}
.empty code, .offer code {{ background:var(--soft); color:var(--ink-2);
  padding:2px 7px; border-radius:6px; }}

/* A photograph in a result grid is looked at rather than read: no frame, no
   caption line, and its date laid over it only when pointed at. */
.grid {{ max-width:1500px; margin:0 auto; padding:12px 20px 40px; display:grid;
  grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:6px; }}
figure {{ margin:0; position:relative; background:var(--surface);
  border-radius:10px; overflow:hidden; }}
figure img {{ display:block; width:100%; aspect-ratio:1; object-fit:cover;
  background:var(--rule); transition:transform .3s; }}
figcaption {{ padding:7px 9px; font-size:12px; color:var(--ink-3); display:flex;
  justify-content:space-between; gap:8px; }}
figcaption b {{ color:var(--ink-2); font-weight:550; }}
.grid figure:not(.odd) figcaption {{ position:absolute; left:0; right:0; bottom:0;
  padding:30px 10px 8px; background:linear-gradient(transparent,rgba(0,0,0,.62));
  opacity:0; transition:opacity .2s; pointer-events:none; }}
.grid figure:not(.odd) figcaption b,
.grid figure:not(.odd) figcaption span {{ color:#fff; }}
.grid figure:not(.odd):hover figcaption,
.grid figure:not(.odd) a:focus-visible figcaption {{ opacity:1; }}
.grid figure:not(.odd):hover img {{ transform:scale(1.04); }}
/* Where the month changes. Inside the grid and spanning it, so the page stays
   one grid and paging does not have to know about months. */
.month {{ grid-column:1/-1; padding:18px 0 4px; font-weight:650; font-size:15px;
  color:var(--ink); }}
.month:first-child {{ padding-top:2px; }}
/* Faces are square, small and scanned in bulk: a tighter grid than the photo
   one, because recognising a person takes a glance, not a look. */
.faces {{ max-width:1500px; margin:0 auto; padding:12px 20px 40px; display:grid;
  grid-template-columns:repeat(auto-fill,minmax(132px,1fr)); gap:10px; }}
.faces figure {{ border:1px solid var(--rule); }}
.faces figure:hover {{ border-color:var(--accent); }}
/* An odd one out, with the means to say it is not. */
.odd figcaption {{ display:flex; justify-content:space-between;
  align-items:center; gap:6px; padding:6px 8px; }}
.odd figcaption span {{ color:var(--ink-3); font-size:11.5px; }}
.odd button {{ padding:4px 9px; font-size:11.5px; }}

.chip {{ display:inline-flex; align-items:center; padding:3px 10px;
  border:1px solid var(--rule); border-radius:999px; font-size:12.5px;
  color:var(--ink-2); background:var(--surface); }}
.chip:hover {{ border-color:var(--accent); color:var(--accent); }}
.panel {{ max-width:1500px; margin:0 auto; padding:14px 20px 0; display:flex;
  gap:10px; align-items:center; flex-wrap:wrap; }}
.panel form {{ display:flex; gap:8px; align-items:center; }}
.people {{ max-width:1500px; margin:0 auto; padding:8px 20px 0; display:flex;
  gap:10px; flex-wrap:wrap; }}
.person {{ display:flex; gap:10px; align-items:center; padding:6px 16px 6px 6px;
  background:var(--surface); border:1px solid var(--rule); border-radius:999px;
  font-size:13px; color:var(--ink-3); }}
.person:hover {{ border-color:var(--accent); box-shadow:var(--shadow); }}
.person img {{ width:44px; height:44px; border-radius:50%; object-fit:cover;
  background:var(--rule); }}
.person b {{ color:var(--ink); font-size:14.5px; font-weight:600; }}
.cands {{ max-width:1500px; margin:0 auto; padding:8px 20px 0; display:flex;
  gap:10px; flex-wrap:wrap; }}
.cand {{ display:flex; gap:9px; align-items:center; padding:8px;
  background:var(--surface); border:1px solid var(--rule); border-radius:12px;
  font-size:12px; color:var(--ink-3); }}
.cand img {{ width:56px; height:56px; border-radius:8px; object-fit:cover;
  background:var(--rule); }}
.cand button {{ padding:6px 10px; font-size:12px; }}
/* A place list is read down the column, not scanned like a grid of faces:
   one line each, the count where the eye can compare them. */
.rows {{ max-width:1500px; margin:6px auto 0; padding:0 20px; }}
.rows a, .rows div.row {{ display:flex; gap:14px; align-items:baseline;
  padding:9px 12px; border-bottom:1px solid var(--rule); }}
.rows a:hover {{ background:var(--surface); }}
.rows b {{ flex:1; font-weight:550; color:var(--ink); }}
.rows span {{ color:var(--ink-3); font-size:13px; }}
.rows form {{ display:inline; }}
.rows form button {{ padding:4px 10px; font-size:12px; }}
.more {{ max-width:1500px; margin:0 auto; padding:0 20px 50px; display:flex;
  gap:10px; align-items:center; flex-wrap:wrap; }}
.more a {{ display:inline-flex; align-items:center; height:38px; padding:0 16px;
  border:1px solid var(--rule); border-radius:10px; background:var(--surface);
  color:var(--ink-2); font-size:14px; font-weight:550; }}
.more a:hover {{ border-color:var(--accent); color:var(--accent); }}

/* The map, when it has been asked for. Nothing is written into the page until
   then, which is the whole of the privacy argument (10.4). */
.map {{ margin:10px 0 4px; border:1px solid var(--rule); border-radius:12px;
  overflow:hidden; background:var(--surface); max-width:620px;
  scroll-margin-top:90px; }}
#where {{ scroll-margin-top:90px; }}
.map iframe {{ display:block; width:100%; height:300px; border:0; }}
/* Previous and next, sitting with the way back. */
.walk {{ display:inline-flex; gap:14px; align-items:center; margin-left:20px;
  font-size:13.5px; }}
.walk a {{ color:var(--ink-2); }}
.walk a:hover {{ color:var(--accent); }}
/* Everyone in one photograph, each with the means to say otherwise. Bigger
   than the review grid: this is one picture being looked at carefully, not a
   wall of faces being scanned. */
.mugs {{ display:flex; gap:12px; flex-wrap:wrap; margin:16px 0 20px; }}
.mug {{ margin:0; width:170px; background:var(--surface);
  border:1px solid var(--rule); border-radius:12px; padding:9px; }}
.mug img {{ display:block; width:100%; aspect-ratio:1; object-fit:cover;
  border-radius:8px; background:var(--rule); }}
.mug figcaption {{ display:block; padding:7px 1px 8px; font-size:13px; }}
.mug figcaption b {{ font-weight:600; }}
.mug figcaption span {{ display:block; color:var(--ink-3); font-size:11px; }}
.mug form {{ display:flex; gap:5px; }}
.mug input[type=text] {{ min-width:0; flex:1; padding:5px 8px; font-size:13px;
  background:var(--ground); border-radius:8px; }}
.mug button {{ padding:5px 9px; font-size:12px; border-radius:8px; }}
/* The correction sitting under the fact it corrects, quiet until wanted. */
.fix {{ margin-top:5px; }}
.fix form {{ display:flex; gap:6px; }}
.fix input[type=text] {{ flex:1; min-width:0; padding:5px 9px; font-size:13px;
  border-radius:8px; }}
.fix button {{ padding:5px 11px; font-size:12px; border-radius:8px; }}
/* One photograph: the picture on a dark stage with the way through the result
   at its sides, what matters about it beside it, and everything for putting it
   right one click further, under Edit & details. The stage stays put while the
   panel scrolls, so opening the details never pushes the picture away. */
.viewer {{ display:flex; min-height:calc(100vh - 71px); }}
.stage {{ flex:1; min-width:0; position:sticky; top:71px; height:calc(100vh - 71px);
  display:flex; flex-direction:column; background:#0c0d0f; color:#e9ecef; }}
.vbar {{ display:flex; align-items:center; gap:4px; padding:10px 12px; }}
.vbtn {{ display:inline-flex; align-items:center; justify-content:center; gap:7px;
  height:38px; min-width:38px; padding:0 10px; border-radius:10px; color:#d6dbe0;
  font-weight:550; }}
.vbtn:hover, .vbtn.on {{ background:rgba(255,255,255,.13); color:#fff; }}
.vbar .pos {{ color:#9aa3ad; font-size:13.5px; margin-left:6px; }}
.pic {{ flex:1; min-height:0; position:relative; display:flex; align-items:center;
  justify-content:center; padding:0 84px 22px; }}
.pic img {{ display:block; max-width:100%; max-height:100%; object-fit:contain;
  border-radius:4px; }}
/* Visible without hunting for them, and brighter when pointed at. */
.arrow {{ position:absolute; top:50%; transform:translateY(-50%); width:54px;
  height:54px; border-radius:50%; display:grid; place-items:center; color:#fff;
  background:rgba(255,255,255,.08); opacity:.5; transition:opacity .2s, background .2s; }}
.arrow svg.ic {{ width:26px; height:26px; }}
.pic:hover .arrow {{ opacity:.85; }}
.arrow:hover, .arrow:focus-visible {{ opacity:1 !important; background:rgba(255,255,255,.22); }}
.arrow.prev {{ left:14px; }}
.arrow.next {{ right:14px; }}
.side {{ width:380px; flex:none; background:var(--surface); border-left:1px solid var(--rule);
  padding:22px 22px 44px; }}
.side h1 {{ margin:0; font-size:21px; font-weight:650; letter-spacing:-.01em; }}
.side .sub {{ color:var(--ink-3); margin-top:2px; }}
.side .warn {{ color:var(--place); font-size:13px; margin-top:6px; }}
.side .note {{ color:var(--ink-3); font-size:12.5px; }}
.side .note a {{ color:var(--accent); }}
.notice {{ background:var(--accent-soft); color:var(--ink); padding:9px 12px;
  border-radius:10px; font-size:13.5px; margin-bottom:14px; }}
.facts {{ margin-top:20px; display:flex; flex-direction:column; gap:16px; }}
.fact {{ display:flex; gap:12px; align-items:flex-start; scroll-margin-top:90px; }}
.fact > svg.ic {{ color:var(--ink-3); margin-top:3px; }}
.fact > div {{ flex:1; min-width:0; }}
.fact .k {{ color:var(--ink-3); font-size:12px; }}
.fact a.link {{ border-bottom:1px solid var(--rule); }}
.fact a.link:hover {{ border-color:var(--ink); }}
.faces-in {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:6px; }}
.face-chip {{ display:inline-flex; align-items:center; gap:8px; padding:3px 12px 3px 3px;
  border-radius:999px; background:var(--soft); font-size:13.5px; font-weight:550; }}
.face-chip img {{ width:30px; height:30px; border-radius:50%; object-fit:cover;
  background:var(--rule); }}
.face-chip.unnamed {{ color:var(--ink-3); font-weight:400; }}
a.face-chip:hover {{ background:var(--rule); }}
.coord {{ color:var(--ink-3); font-size:12.5px; margin-top:2px; }}
.maplink {{ display:inline-flex; gap:6px; align-items:center; margin-top:6px;
  color:var(--accent); font-weight:550; }}
.maplink svg.ic {{ width:15px; height:15px; }}
.side .map {{ max-width:none; margin:8px 0 6px; }}
.side .map iframe {{ height:240px; }}
.qlink {{ display:inline-flex; gap:6px; align-items:center; margin-top:18px;
  color:var(--place); font-size:13.5px; }}
.qlink svg.ic {{ width:15px; height:15px; }}
details.edit {{ margin-top:22px; }}
details.edit > summary {{ list-style:none; cursor:pointer; display:flex; gap:8px;
  align-items:center; justify-content:center; height:40px; border:1px solid var(--rule);
  border-radius:10px; font-weight:550; color:var(--ink-2); user-select:none; }}
details.edit > summary::-webkit-details-marker {{ display:none; }}
details.edit > summary:hover, details.edit[open] > summary {{ color:var(--ink);
  border-color:var(--ink-3); }}
details.edit h3 {{ margin:20px 0 6px; font-size:11.5px; font-weight:650; color:var(--ink-3);
  text-transform:uppercase; letter-spacing:.05em; }}
.frow {{ padding:8px 0; border-bottom:1px solid var(--rule); font-size:14px;
  scroll-margin-top:90px; }}
.frow .k {{ color:var(--ink-3); font-size:12px; }}
.frow .note {{ color:var(--ink-3); font-size:13px; }}
.side .mugs {{ margin:6px 0 0; gap:8px; }}
.side .mug {{ width:calc(50% - 4px); }}
.orig {{ display:flex; flex-wrap:wrap; gap:6px 16px; font-size:13.5px; }}
.orig a {{ color:var(--accent); font-weight:550; }}
.paths {{ margin-top:10px; font:11.5px/1.5 ui-monospace,Consolas,monospace;
  color:var(--ink-3); word-break:break-all; }}
@media (max-width:900px) {{
  .viewer {{ flex-direction:column; }}
  .stage {{ position:static; height:auto; }}
  .pic {{ padding:0 8px 14px; }}
  .pic img {{ max-height:72vh; }}
  .arrow {{ width:44px; height:44px; }}
  .side {{ width:auto; border-left:0; }}
}}
/* The slideshow: its controls on the stage, and while it plays, the picture and
   nothing else -- the header, the panel and, once the mouse is still, the
   controls and the arrows all give way. */
button.vbtn {{ background:transparent; border:0; font:inherit; filter:none; }}
.vbar details.pop > summary {{ height:38px; min-width:38px; justify-content:center;
  padding:0 10px; background:transparent; border:0; color:#d6dbe0; }}
.vbar details.pop > summary:hover, .vbar details.pop[open] > summary {{
  background:rgba(255,255,255,.13); color:#fff; }}
.slideopts .menu {{ width:300px; padding:12px; color:var(--ink); }}
.slideopts .opt {{ display:flex; flex-direction:column; gap:6px; font-size:13px;
  color:var(--ink-2); }}
.slideopts .opt select {{ height:36px; padding:0 8px; font:inherit; color:var(--ink);
  background:var(--surface); border:1px solid var(--rule); border-radius:8px; }}
.slideopts .opt.check {{ flex-direction:row; align-items:flex-start; gap:10px;
  margin-top:14px; color:var(--ink); font-weight:550; cursor:pointer; }}
.slideopts .opt.check input {{ margin-top:3px; accent-color:var(--accent); }}
.slideopts .opt small {{ display:block; margin-top:2px; font-size:12px; font-weight:400;
  color:var(--ink-3); }}
.progress {{ position:absolute; left:0; top:0; z-index:3; height:3px; width:0;
  background:#3b82f6; }}
.progress.run {{ animation:grow var(--slide, 5s) linear forwards; }}
@keyframes grow {{ from {{ width:0; }} to {{ width:100%; }} }}
.caption {{ position:absolute; left:24px; right:260px; bottom:12px; color:#e9ecef;
  font-size:14px; text-shadow:0 1px 3px rgba(0,0,0,.9); white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis; opacity:0; transition:opacity .4s;
  pointer-events:none; }}
.nas {{ position:absolute; right:16px; bottom:9px; padding:5px 10px; border-radius:8px;
  background:rgba(0,0,0,.65); color:#fbbf24; font-size:12.5px; }}
body.playing header, body.playing .side {{ display:none; }}
body.playing .viewer {{ min-height:100vh; }}
body.playing .stage {{ position:sticky; top:0; height:100vh; }}
body.playing .pic {{ padding-bottom:40px; }}
body.playing .pic img {{ max-height:100%; }}
body.playing .caption {{ opacity:.92; }}
body.playing .vbar {{ transition:opacity .4s; }}
body.playing.idle .vbar, body.playing.idle .arrow {{ opacity:0 !important;
  pointer-events:none; }}
body.playing.idle {{ cursor:none; }}
.meta a.play {{ float:right; display:inline-flex; gap:7px; align-items:center;
  height:34px; margin-top:-7px; padding:0 14px; border-radius:10px;
  background:var(--ink); color:var(--surface); font-weight:600; }}
.meta a.play:hover {{ opacity:.88; }}
.meta a.play svg.ic {{ width:15px; height:15px; }}
/* Back from a photograph lands on its card, clear of the sticky header and
   outlined so the eye finds it. */
.grid figure {{ scroll-margin-top:96px; }}
.grid figure:target {{ outline:3px solid var(--accent); outline-offset:2px; }}
/* The order, beside the count. */
.meta .sort {{ display:inline-flex; margin-left:10px; vertical-align:middle;
  border:1px solid var(--rule); border-radius:8px; overflow:hidden; }}
.meta .sort a {{ padding:2px 10px; font-size:12.5px; color:var(--ink-2); }}
.meta .sort a + a {{ border-left:1px solid var(--rule); }}
.meta .sort a:hover {{ color:var(--ink); background:var(--soft); }}
.meta .sort a.on {{ background:var(--ink); color:var(--surface); }}
/* Opening the file in its application, and the line that answers. */
.vbar form {{ display:flex; }}
.orig .linkish {{ padding:0; border:0; background:none; color:var(--accent);
  font:inherit; font-size:13.5px; font-weight:550; cursor:pointer;
  white-space:nowrap; }}
.orig .linkish:hover {{ filter:none; text-decoration:underline; }}
.toast {{ position:absolute; left:50%; bottom:18px; transform:translateX(-50%);
  z-index:4; padding:8px 14px; border-radius:10px; background:rgba(20,22,26,.92);
  color:#fff; font-size:13.5px; white-space:nowrap; }}
/* A viewing copy says so, quietly, where the eye passes on every page. */
.viewonly {{ padding:4px 10px; border:1px solid var(--rule); border-radius:999px;
  font-size:12px; font-weight:550; color:var(--ink-3); white-space:nowrap; }}
@media (max-width:720px) {{ .viewonly {{ display:none; }} }}
/* The count line holds its Slideshow button. Floated and not cleared, the
   button reached into the grid below, and a grid beside a float shrinks to
   the room left over: on a phone, one narrow column and a blank half. */
.meta::after {{ content:""; display:table; clear:both; }}
/* A phone, held upright (spec 8.5). Nothing may be wider than the screen: one
   element that is makes the browser lay the whole page out wider and shrink it
   to fit, which is what made every page small and awkward. */
@media (max-width:720px) {{
  .bar {{ gap:8px; padding:8px 10px; }}
  /* A text box will not shrink below its own default width unless told it
     may; on the narrowest phones that pushed Browse off the screen. */
  .search {{ height:42px; padding:0 4px 0 12px; min-width:0; }}
  .search input {{ width:0; }}
  /* The keyboard's own Go key searches; the button only took the room. */
  .search button {{ display:none; }}
  /* Below 16px a phone zooms the page when the box is touched. */
  .search input {{ font-size:16px; }}
  details.browse > summary {{ padding:0 9px; }}
  details.browse > summary svg.ic:last-child {{ display:none; }}
  .chips {{ padding:10px 10px 0; gap:6px; }}
  .crit {{ height:32px; font-size:13px; }}
  details.filters form {{ left:8px; right:8px; padding:14px; gap:16px; }}
  details.filters fieldset {{ min-width:0; flex-basis:100%; }}
  .meta, .offer, .crumb {{ padding-left:12px; padding-right:12px; }}
  .meta a.play {{ height:32px; margin-top:-5px; padding:0 12px; }}
  /* Three across, edge to edge, as a phone's own gallery has them. */
  .grid {{ grid-template-columns:repeat(3,1fr); gap:3px; padding:8px 3px 30px; }}
  .grid figure {{ border-radius:3px; }}
  .month {{ padding:16px 9px 4px; font-size:14px; }}
  .faces {{ grid-template-columns:repeat(auto-fill,minmax(96px,1fr));
    gap:8px; padding:10px 10px 30px; }}
  .people, .rows, .cands, .panel, .more {{ padding-left:10px; padding-right:10px; }}
  /* The photograph: the way back as an arrow alone, and what is one tap away
     in Details -- the original at full size -- or what a phone cannot do
     -- fill the screen from a page -- left off the bar. */
  .vbar {{ padding:6px 4px; gap:0; }}
  .vbtn {{ min-width:36px; padding:0 8px; }}
  #go-back {{ font-size:0; gap:0; }}
  .vbar .pos {{ white-space:nowrap; margin-left:2px; font-size:13px; }}
  #fs, .vbar a.vbtn[href^="/full/"]:not([href*="download"]) {{ display:none; }}
  .pic {{ padding:0 0 10px; }}
  .pic img {{ border-radius:0; }}
  .arrow {{ width:40px; height:40px; background:rgba(0,0,0,.3); }}
  .arrow.prev {{ left:6px; }}
  .arrow.next {{ right:6px; }}
  .side {{ padding:16px 14px 36px; }}
  .caption {{ left:12px; right:12px; }}
  .nas {{ right:8px; }}
}}
/* A phone on its side: the photograph takes the height, and the header scrolls
   away with the page rather than standing over a third of it. */
@media (max-height:500px) {{
  header {{ position:static; }}
  /* On the photograph the bar above it already leads back; the search box
     would only push the picture off the bottom of the screen. */
  body:has(.viewer) header {{ display:none; }}
  .viewer {{ flex-direction:column; min-height:0; }}
  .stage {{ position:static; height:auto; }}
  .pic {{ padding:0 56px 8px; }}
  .pic img {{ max-height:calc(100vh - 60px); max-height:calc(100dvh - 60px); }}
  .side {{ width:auto; border-left:0; }}
  details.filters form {{ max-height:calc(100vh - 110px); overflow:auto; }}
}}
</style></head><body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
<symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></symbol>
<symbol id="i-x" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></symbol>
<symbol id="i-grid" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></symbol>
<symbol id="i-chev" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></symbol>
<symbol id="i-photo" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 17-5-5-9 8"/></symbol>
<symbol id="i-people" viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5"/><circle cx="17" cy="9" r="2.5"/><path d="M17 14.5c2.4 0 4 1.6 4.5 4"/></symbol>
<symbol id="i-pin" viewBox="0 0 24 24"><path d="M12 22s7-6.2 7-12a7 7 0 1 0-14 0c0 5.8 7 12 7 12z"/><circle cx="12" cy="10" r="2.5"/></symbol>
<symbol id="i-event" viewBox="0 0 24 24"><path d="M4 8h16v12H4z"/><path d="M4 8l3-4h10l3 4"/></symbol>
<symbol id="i-cal" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></symbol>
<symbol id="i-camera" viewBox="0 0 24 24"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></symbol>
<symbol id="i-sliders" viewBox="0 0 24 24"><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></symbol>
<symbol id="i-q" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5M12 17h0"/></symbol>
<symbol id="i-not" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/></symbol>
<symbol id="i-left" viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></symbol>
<symbol id="i-right" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></symbol>
<symbol id="i-back" viewBox="0 0 24 24"><path d="M19 12H5M11 18l-6-6 6-6"/></symbol>
<symbol id="i-down" viewBox="0 0 24 24"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></symbol>
<symbol id="i-full" viewBox="0 0 24 24"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></symbol>
<symbol id="i-pen" viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/></symbol>
<symbol id="i-play" viewBox="0 0 24 24"><path d="M7 4.5v15l12-7.5z"/></symbol>
<symbol id="i-pause" viewBox="0 0 24 24"><path d="M8 5v14M16 5v14"/></symbol>
<symbol id="i-screen" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></symbol>
<symbol id="i-open" viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></symbol>
<symbol id="i-info" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h0"/></symbol>
<symbol id="i-odd" viewBox="0 0 24 24"><path d="M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2z"/></symbol>
</svg>
<header><div class="bar">
  <a class="logo" href="/"><span><svg class="ic"><use href="#i-photo"/></svg></span><b>Kuva-arkisto</b></a>
  <form class="search" action="/" method="get" role="search">
    <svg class="ic"><use href="#i-search"/></svg>
    <input type="search" name="q" value="{q}" placeholder="{placeholder}"{focus}>
    {clear}<button type="submit">Search</button>
  </form>
  <div class="spacer"></div>{mark}
  <details class="pop browse"><summary><svg class="ic"><use href="#i-grid"/></svg><b>Browse</b><svg class="ic"><use href="#i-chev"/></svg></summary>
    <div class="menu">
      <a href="/"><svg class="ic"><use href="#i-photo"/></svg>Photographs</a>
      <a href="/people"><svg class="ic"><use href="#i-people"/></svg>People</a>
      <a href="/places"><svg class="ic"><use href="#i-pin"/></svg>Places</a>
      <a href="/events"><svg class="ic"><use href="#i-event"/></svg>Events</a>
      <hr>
      {review}
      <a href="/odd"><svg class="ic"><use href="#i-odd"/></svg>Odd ones</a>
    </div></details>
</div></header>
{body}
<script>document.addEventListener('click',function(e){{
document.querySelectorAll('details.pop[open]').forEach(function(d){{
if(!d.contains(e.target))d.removeAttribute('open');}});}});
function when(f){{var y=f.elements.y,m=f.elements.m,d=f.elements.d;if(!y||!m||!d)return;
var yes=/^[0-9]{{4}}$/.test(y.value);m.disabled=!yes;d.disabled=!yes||!m.value;}}
document.addEventListener('change',function(e){{if(e.target.form)when(e.target.form);}});
document.querySelectorAll('form').forEach(function(f){{when(f);}});</script>
</body></html>"""


def open_with_default_app(path: str) -> None:
    """Hand a file to whatever this computer opens it with.

    A double-click in Explorer, done from the page. At module level so a test
    can put something harmless in its place.
    """
    if hasattr(os, "startfile"):
        os.startfile(path)                 # Windows: the associated application
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])

class WrongModel(Exception):
    """The text model here is not the one the photographs were indexed with."""

    def __init__(self, have: str, want: str):
        super().__init__(f"text model {have} does not match the index's {want}")
        self.have, self.want = have, want
def create_app(cfg) -> FastAPI:
    app = FastAPI(title="Kuva-arkisto")
    cache_dir = Path(cfg.thumbnails)

    state: dict = {"encoder": None}

    # A viewing copy (§8.3): the same screens with nothing that changes
    # anything. Set by `[ui] read_only` or `serve --read-only`.
    ro = bool(cfg.ui.get("read_only"))

    def connect() -> sqlite3.Connection:
        if ro:
            # Opened so that SQLite itself refuses a write. Hiding the forms
            # and turning the requests away are both promises made by this
            # file; this one is kept by the database.
            con = db.connect_read_only(cfg.database, timeout=30.0,
                                       isolation_level=None)
        else:
            con = db.connect(cfg.database, create=False)
        con.row_factory = sqlite3.Row
        state["vec_ok"] = embed.vec_index_ready(con)
        return con

    if ro:
        @app.middleware("http")
        async def view_only(request: Request, call_next):
            """Everything that changes something arrives as a POST, so one
            refusal here covers every such route, including any added later."""
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                return PlainTextResponse(
                    "This is a viewing copy of the archive. Nothing can be "
                    "changed here.", status_code=403)
            return await call_next(request)

    def on_disk(path: str) -> str:
        """A stored path as this machine opens it: found under its mount
        where there is one (spec 8.3), and long-path safe on Windows."""
        return pathutil.long_path(pathutil.local(path, cfg.mounts))

    # Loaded on the first described search and, for the exported text model,
    # let go again after a quiet spell (spec 7.6): on a 4 GB NAS a gigabyte
    # held for a search somebody made last week is a gigabyte the NAS itself
    # goes without. `[ui] text_model_idle_minutes`, 0 to keep it.
    lock = threading.Lock()
    idle = float(cfg.ui.get("text_model_idle_minutes", 10)) * 60
    state["gen"] = 0

    def _rest(gen: int) -> None:
        with lock:
            if state["gen"] != gen:          # used again since this was set
                return
            state["encoder"] = None
        gc.collect()

    def encoder():
        """What turns a description into a vector: the exported text half
        where `[paths] text_model` names one, the whole model otherwise."""
        with lock:
            if state["encoder"] is None:
                if cfg.text_model:
                    state["encoder"] = textmodel.TextEncoder(
                        cfg.text_model, threads=cfg.ui.get("text_threads"))
                else:
                    state["encoder"] = embed.Encoder(
                        model_name=cfg.scan.get("embed_model", embed.DEFAULT_MODEL),
                        pretrained=cfg.scan.get("embed_pretrained",
                                                embed.DEFAULT_PRETRAINED),
                    )
            if cfg.text_model and idle > 0:
                state["gen"] += 1
                t = threading.Timer(idle, _rest, args=(state["gen"],))
                t.daemon = True
                t.start()
            return state["encoder"]

    def text_vector(con, text: str) -> np.ndarray:
        """A description as a vector, from a model that matches the index.

        An exported text model is a file that outlives the model it was taken
        from. Ranking the photographs against a vector from some other model
        would still return photographs -- the wrong ones, ranked with
        confidence -- so a mismatch stops the search instead.
        """
        enc = encoder()
        if cfg.text_model:
            row = con.execute("SELECT model FROM image_vectors LIMIT 1").fetchone()
            if row is not None and row[0] != enc.tag:
                raise WrongModel(enc.tag, row[0])
        return enc.encode_text([text])[0]

    @app.exception_handler(WrongModel)
    async def wrong_model(request: Request, exc: WrongModel):
        return HTMLResponse(layout(
            "Search model — Kuva-arkisto",
            '<div class="empty"><p>Described search is unavailable here: the '
            f'search model <b>{html.escape(exc.have)}</b> is not the one these '
            f'photographs were indexed with, <b>{html.escape(exc.want)}</b>.</p>'
            '<p>Build it again where the archive is kept: '
            '<code>mediaidx textmodel</code></p></div>'), status_code=503)

    # Said in the box itself, the box being the whole interface (§7.1).
    PLACEHOLDER = "Search people, places, years, or describe a scene"

    def open_questions() -> int:
        """How many questions the review queue holds, for the Browse menu.

        Asked on a connection of its own because every page carries the menu,
        not only the ones that already hold a connection. A database from before
        the queue existed has no such table, which is no questions, not a 500.
        """
        try:
            con = connect()
        except FileNotFoundError:
            return 0
        try:
            return con.execute("SELECT COUNT(*) FROM review_queue "
                               "WHERE status = 'open'").fetchone()[0]
        except sqlite3.OperationalError:
            return 0
        finally:
            con.close()

    # A form that posts, wherever it is drawn. In a viewing copy none of them
    # reaches the page: taken out in the one place every page passes through,
    # so a screen added later cannot forget to leave its buttons off.
    POST_FORM = re.compile(r'<form\b[^>]*method="post".*?</form>', re.S)

    def layout(title: str, body: str, q: str = "", focus: str = "") -> str:
        """Every page in the one frame: the box, the way to clear it, the menu.

        `q` arrives already escaped.
        """
        clear = ('<a class="clear" href="/" title="Clear the search">'
                 '<svg class="ic"><use href="#i-x"/></svg></a>' if q else "")
        if ro:
            return PAGE.format(
                title=title, q=q, focus=focus, clear=clear, review="",
                mark='<span class="viewonly" title="Nothing can be changed '
                     'here">Viewing copy</span>',
                placeholder=PLACEHOLDER, body=POST_FORM.sub("", body))
        n = open_questions()
        badge = f'<span class="badge">{n:,}</span>' if n else ""
        review = ('<a href="/review"><svg class="ic"><use href="#i-q"/></svg>'
                  f'Review{badge}</a>')
        return PAGE.format(title=title, q=q, focus=focus, clear=clear,
                           review=review, mark="", placeholder=PLACEHOLDER,
                           body=body)

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
            f'<figure id="p{row["id"]}"><a href="{to}">'
            f'<img loading="lazy" src="/thumb/s/{row["content_hash"]}" alt="">'
            f'<figcaption><b>{html.escape(when)}</b>'
            f'<span>{html.escape(str(right)[:18])}</span></figcaption></a></figure>')

    def month_of(row) -> str:
        """The heading a photograph files under: its month, or its year alone
        where the year is all that is known, so a photograph is never filed
        under a month nobody gave it."""
        if not row["taken_at"]:
            return "No date"
        dt = datetime.fromtimestamp(row["taken_at"])
        return f"{dt:%Y}" if row["taken_at_precision"] == "year" else f"{dt:%B %Y}"

    def cards_by_month(rows, back) -> str:
        """A date-ordered grid, named wherever the month changes.

        The headings sit inside the grid and span it, so the page stays one
        grid and paging need not know about months. Only for grids in date
        order: a ranked result has no months to speak of.
        """
        out, last = [], None
        for r in rows:
            m = month_of(r)
            if m != last:
                out.append(f'<div class="month">{m}</div>')
                last = m
            out.append(card(r, back=back))
        return "".join(out)

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
        """Cached, because it is now asked on every page rather than per search.

        The walk from one photograph to the next resolves the query again each
        time, and this recursive count was the largest part of that: 202 ms a
        photograph against 16 ms for a page that does not need it. The count of
        placed photographs is the key, so `places` or `derive` putting anything
        anywhere new invalidates it, and a stale answer cannot outlive a change.
        """
        placed = con.execute(
            "SELECT COUNT(*) FROM assets WHERE place_id IS NOT NULL").fetchone()[0]
        if state.get("counts_at") == placed:
            return state["counts"]
        got = _subtree_counts(con)
        state["counts_at"], state["counts"] = placed, got
        return got

    def _subtree_counts(con) -> dict:
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

    FILTERS = ("from", "to", "cam", "dsrc", "psrc")
    # Carried with a search wherever it goes, but not a filter: the order
    # narrows nothing, so it is neither a chip nor counted on the button.
    CARRIED = FILTERS + ("order",)
    ORDERS = ("newest", "oldest")

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

        # Who else (§7.4). Only them: no face in it belongs to anybody else,
        # and a face nobody has named *is* somebody else -- a stranger in the
        # background takes the photograph out. Measured 2026-09-13, Tommi and
        # Ansku are together in 919 photographs and the only people in 270,
        # the same 270 the old count of exactly two faces found. Counted from
        # detected faces, so somebody turned away is not among them.
        if r.mode == "only" and pids:
            marks = ",".join("?" * len(pids))
            preds.append(
                f"""NOT EXISTS (SELECT 1 FROM faces x WHERE x.asset_id = a.id
                                  AND (x.person_id IS NULL
                                       OR x.person_id NOT IN ({marks})))""")
            args.extend(pids)
        elif r.mode == "nobody":
            preds.append("NOT EXISTS (SELECT 1 FROM faces x "
                         "WHERE x.asset_id = a.id)")
        for t in r.excluded:
            preds.append("NOT EXISTS (SELECT 1 FROM faces x "
                         "WHERE x.asset_id = a.id AND x.person_id = ?)")
            args.append(t.ref_id)
        for t in r.places:
            row = con.execute("SELECT path FROM places WHERE id = ?",
                              (t.ref_id,)).fetchone()
            if row is None:
                continue
            # Naming any level matches everything beneath it (§7.2). As a range
            # rather than as `LIKE 'X/%'`, which cannot use the index on `path`
            # -- LIKE is case-insensitive by default, so SQLite scans all
            # 285,144 places instead. That showed up walking a result: four such
            # scans a photograph, 200 ms each time. '/' is 0x2F and '0' is 0x30,
            # so everything under "X/" sorts between "X/" and "X0" and nothing
            # else does.
            preds.append("""a.place_id IN (SELECT id FROM places
                             WHERE path = ? OR (path > ? AND path < ?))""")
            args.extend([row["path"], row["path"] + "/", row["path"] + "0"])

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

    # The icon a chip carries, so the reading chosen shows at a glance: Hartola
    # the event and Hartola the place must not look alike (§7.1).
    CHIP_ICONS = {"person": "people", "place": "pin", "event": "event",
                  "date": "cal", "mode": "people"}
    # Who else, as a chip says it, and as the sentence for an empty result
    # says it: "No photograph has Tommi, Ansku and nobody else."
    MODE_WORDS = {"only": "only these people", "nobody": "no people"}
    MODE_SAID = {"only": "nobody else", "nobody": "nobody in it"}

    def _term_words(t) -> str:
        if t.kind == "mode":
            return MODE_SAID[t.label]
        return f"not {t.label}" if t.negated else t.label

    # What each filter says as a chip. A value not listed is shown as given.
    FILTER_WORDS = {
        "dsrc": {"exif": "date from EXIF", "filename": "date from the filename",
                 "folder": "date from the folder"},
        "psrc": {"gps": "place from the coordinate",
                 "folder": "place from the folder name"},
    }
    FILTER_ICONS = {"from": "cal", "to": "cal", "cam": "camera",
                    "dsrc": "sliders",
                    "psrc": "sliders"}

    def _icon(name: str) -> str:
        return f'<svg class="ic"><use href="#i-{name}"/></svg>'

    def _filter_label(k: str, v: str) -> str:
        if k == "from":
            return f"from {v}"
        if k == "to":
            return f"until {v}"
        if k == "cam":
            return f"camera {v}"
        return FILTER_WORDS.get(k, {}).get(v, f"{k} {v}")

    def _face_of(con, person_id: int):
        return con.execute(
            """SELECT a.content_hash, x.idx FROM faces x
                 JOIN assets a ON a.id = x.asset_id
                WHERE x.person_id = ?
                ORDER BY x.blur_score DESC LIMIT 1""", (person_id,)).fetchone()

    def year_counts(con) -> list:
        """(year, photographs), newest first, for the year list.

        Held until the dates change: the key is the count and the sum of every
        date, so a scan adding photographs or a date put right by hand both
        invalidate it, and nothing else makes the page re-count 40,000 rows.
        """
        key = tuple(con.execute(
            "SELECT COUNT(taken_at), TOTAL(taken_at) FROM assets").fetchone())
        if state.get("years_at") != key:
            state["years"] = [(r[0], r[1]) for r in con.execute(
                """SELECT strftime('%Y', taken_at, 'unixepoch', 'localtime') y,
                          COUNT(*) FROM assets WHERE taken_at IS NOT NULL
                    GROUP BY y ORDER BY y DESC""")]
            state["years_at"] = key
        return state["years"]

    def _picked(con, q: str, f: dict, y: str, m: str, d: str,
                people: str) -> str:
        """The search the filter form asks for, written back into the box.

        The date and who else may be in it are picked from lists but kept as
        the words typing them would leave -- `2016-07`, `only` -- so a picked
        criterion and a typed one are the same chip, removed the same way and
        carried by the same link. They live nowhere but `q` (§7.4).
        """
        r = query.resolve(con, q, place_counts=subtree_counts(con))
        drop, add = [], []
        # "keep" is a date the lists cannot show, `kesä 2016` or a range:
        # changing the camera must not quietly throw it away.
        if y != "keep":
            drop += r.dates
            when = y if re.fullmatch(r"\d{4}", y or "") else ""
            if when and m.isdigit() and query.parse_date(f"{when}-{int(m):02d}"):
                when = f"{when}-{int(m):02d}"
                if d.isdigit() and query.parse_date(f"{when}-{int(d):02d}"):
                    when = f"{when}-{int(d):02d}"
            if when and query.parse_date(when):
                add.append(when)
        if people in ("with", "only", "nobody"):
            drop += [t for t in r.terms if t.kind == "mode"]
            if people == "only" and r.people:
                add.append("only")
            elif people == "nobody":
                add.append("nobody")
        return _qs(" ".join([r.without(*drop), *add]).strip(), f)

    def chip_row(con, q, r, f) -> str:
        """Everything the page is searching for, and how to take any of it back.

        Typed or picked, a criterion is shown the same way: a chip carrying the
        link that removes it. Filters used to show only inside their folded
        row, which left nothing on the page saying which of them were on (§7.4).
        """
        seen = {"person": "/person/", "place": "/place/", "event": "/event/"}
        bits = []
        for t in r.terms:
            gone = _qs(r.without(t), f)
            # The label leads to the thing itself, so a chip is a way through
            # to the person, place or event as well as a criterion.
            label = html.escape(t.label)
            if t.kind in seen and t.ref_id:
                label = f'<a href="{seen[t.kind]}{t.ref_id}">{label}</a>'
            icon = _icon(CHIP_ICONS.get(t.kind, "search"))
            mark, flip = f'<i title="{t.kind}">{icon}</i>', ""
            if t.kind == "person":
                face = _face_of(con, t.ref_id) if t.ref_id else None
                mark = (f'<img src="/face/{face["content_hash"]}/{face["idx"]}" '
                        'alt="" title="person">' if face else
                        f'<i class="av" title="person">{icon}</i>')
                # Out of the photograph and back in, as a plain link: the
                # query says "without" before the name, or it does not.
                other = (t.span.split(" ", 1)[1] if t.negated
                         else f"without {t.span}")
                why = ("in the photograph after all" if t.negated
                       else "not in the photograph")
                flip = (f'<a class="swap" href="{_qs(r.replace(t, other), f)}" '
                        f'title="{why}">{_icon("not")}</a>')
                if t.negated:
                    label = f"not {label}"
            elif t.kind == "mode":
                label = MODE_WORDS[t.label]
            kind = "not" if t.negated else t.kind
            bits.append(
                f'<span class="crit" data-kind="{kind}">{mark}{label}{flip}'
                f'<a class="x" href="{gone}" title="not this">&times;</a></span>')
        for k in FILTERS:
            if not f.get(k):
                continue
            gone = _qs(q, {**f, k: ""})
            bits.append(
                '<span class="crit filter" data-kind="filter">'
                f'{_icon(FILTER_ICONS[k])}{html.escape(_filter_label(k, f[k]))}'
                f'<a class="x" href="{gone}" title="not this">&times;</a></span>')
        if r.text and bits:
            bits.append(f'<span class="rest"><b>{html.escape(r.text)}</b> '
                        'as the description</span>')
        bits.append(filter_row(con, q, r, f))
        return f'<div class="chips">{"".join(bits)}</div>'

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
            # "without Reno" read as a place would leave "without" in its name.
            for alt in (() if t.negated else t.also):
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

    def filter_row(con, q: str, r, f: dict) -> str:
        """Who else, when, and the auditing controls, behind one button (§7.4).

        Who else and the date are criteria like any typed one, and picking
        them writes their words into the query (`_picked`). The rest answer
        "what do I want to check" rather than "what am I looking for", and
        nobody types `place-source is a folder name` as a question. Set, every
        one shows as a chip, so the button stays closed and counts the filters.
        """
        def opt(value, label, current):
            sel = " selected" if current == value else ""
            return f'<option value="{value}"{sel}>{label}</option>'

        on = sum(1 for k in FILTERS if f.get(k))
        count = f' <span class="badge">{on}</span>' if on else ""

        mode = r.mode or "with"

        def choice(value, title, sub, off=False):
            chk = " checked" if mode == value else ""
            dis = " disabled" if off else ""
            return (f'<label class="choice"><input type="radio" name="people" '
                    f'value="{value}"{chk}{dis}><span>{title}'
                    f'<small>{sub}</small></span></label>')

        who = (choice("with", "In the photograph", "anybody else may be too")
               + choice("only", "Only them",
                        "no other face in it" if r.people
                        else "name somebody first", off=not r.people)
               + choice("nobody", "No people", "no face found at all"))

        # A year, a month and a day the lists can show are shown in them; any
        # other date (`kesä 2016`, a range) is kept as it was typed.
        y = mo = dd = keep = ""
        if (len(r.dates) == 1
                and re.fullmatch(r"\d{4}(-\d{2}){0,2}", r.dates[0].label)):
            y, _, rest = r.dates[0].label.partition("-")
            mo, _, dd = rest.partition("-")
            mo, dd = (str(int(mo)) if mo else ""), (str(int(dd)) if dd else "")
        elif r.dates:
            keep = ", ".join(t.label for t in r.dates)
        sel_y = ((f'<option value="keep" selected>{html.escape(keep)}</option>'
                  if keep else "")
                 + opt("", "any year", None if keep else y)
                 + "".join(opt(yr, f"{yr} &middot; {n:,}", y)
                           for yr, n in year_counts(con)))
        sel_m = opt("", "any month", mo) + "".join(
            opt(str(i), datetime(2000, i, 1).strftime("%B"), mo)
            for i in range(1, 13))
        sel_d = opt("", "any day", dd) + "".join(
            opt(str(i), str(i), dd) for i in range(1, 32))

        sel_dsrc = "".join([
            opt("", "any", f.get("dsrc")),
            opt("exif", "EXIF", f.get("dsrc")),
            opt("filename", "filename", f.get("dsrc")),
            opt("folder", "folder", f.get("dsrc"))])
        sel_psrc = "".join([
            opt("", "any", f.get("psrc")),
            opt("gps", "coordinate", f.get("psrc")),
            opt("folder", "folder name", f.get("psrc"))])
        return (
            '<details class="pop filters">'
            f'<summary>{_icon("sliders")}Filters{count}</summary>'
            '<form action="/" method="get">'
            f'<input type="hidden" name="q" value="{html.escape(q)}">'
            '<input type="hidden" name="pick" value="1">'
            f'<input type="hidden" name="order" value="{html.escape(f.get("order", ""))}">'
            f'<fieldset><legend>People</legend>{who}'
            '<p class="hint">Type <b>without</b> before a name to leave '
            'somebody out.</p></fieldset>'
            '<fieldset><legend>When</legend>'
            f'<div class="pickrow"><select name="y" aria-label="year">{sel_y}</select>'
            f'<select name="m" aria-label="month">{sel_m}</select>'
            f'<select name="d" aria-label="day">{sel_d}</select></div>'
            '<p class="hint">A year is enough. Typing <b>2016-07</b> or '
            '<b>kesä 2016</b> works too.</p>'
            '<div class="pickrow">'
            '<label>from<input type="text" name="from" placeholder="2019-06" '
            f'value="{html.escape(f.get("from", ""))}"></label>'
            '<label>to<input type="text" name="to" placeholder="2020" '
            f'value="{html.escape(f.get("to", ""))}"></label></div></fieldset>'
            '<fieldset><legend>Checking</legend>'
            '<label>camera<input type="text" name="cam" placeholder="any" '
            f'value="{html.escape(f.get("cam", ""))}"></label>'
            f'<label>date from<select name="dsrc">{sel_dsrc}</select></label>'
            f'<label>place from<select name="psrc">{sel_psrc}</select></label>'
            '</fieldset>'
            '<div class="factions"><button type="submit">Apply</button>'
            f'<a class="clearall" href="{_qs(q, {"order": f.get("order", "")})}">'
            'Clear filters</a></div>'
            '</form></details>')

    @app.get("/", response_class=HTMLResponse)
    def home(q: str = Query(default=""), page: int = 0, more: int = 0,
             date_from: str = Query(default="", alias="from"),
             to: str = Query(default=""), cam: str = Query(default=""),
             dsrc: str = Query(default=""), psrc: str = Query(default=""),
             pick: int = 0, y: str = "", m: str = "", d: str = "",
             people: str = "", order: str = ""):
        con = connect()
        f = {"from": date_from.strip(), "to": to.strip(), "cam": cam.strip(),
             "dsrc": dsrc.strip(), "psrc": psrc.strip(),
             "order": order if order in ORDERS else ""}
        if pick:
            return RedirectResponse(_picked(con, q, f, y, m, d, people),
                                    status_code=303)
        page = max(0, page)
        total_all = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        embedded = con.execute(
            "SELECT COUNT(*) FROM image_vectors").fetchone()[0]

        r = query.resolve(con, q, place_counts=subtree_counts(con))
        where, args = _where(con, r, f)
        # This exact page, so a photograph opened from it can lead back here
        # rather than to the front -- including the page number and the filters.
        here = _qs(q, f, page=page or None, more=more or None)
        head = chip_row(con, q, r, f) + offer_row(con, r, f)

        cols = ("a.id, a.content_hash, a.taken_at, a.taken_at_precision, "
                "a.camera_model")
        clause = f" WHERE {where}" if where else ""

        # Nothing to rank by: the answer is exact, so it is every photograph
        # that matches, newest first, and the count is the real count.
        if not r.text:
            total = con.execute(
                f"SELECT COUNT(*) FROM assets a{clause}", args).fetchone()[0]
            way = "ASC" if f["order"] == "oldest" else "DESC"
            rows = con.execute(
                f"SELECT {cols} FROM assets a{clause} "
                f"ORDER BY COALESCE(a.taken_at,0) {way}, a.id {way} "
                "LIMIT ? OFFSET ?",
                [*args, PAGE_SIZE, page * PAGE_SIZE]).fetchall()
            # Genuinely nothing indexed -- not merely nothing matching, which a
            # filter can produce on a full library and which must not be
            # reported as an empty archive.
            if not total_all:
                return HTMLResponse(layout(
                    title="Kuva-arkisto", q="", focus=" autofocus",
                    body='<div class="empty"><p>Nothing indexed yet.</p>'
                         '<p><code>mediaidx add &lt;folder&gt;</code></p></div>'))
            if not total:
                # "0 photographs, most recent first" is not an answer. Say what
                # was asked for and, where a word read two ways, that the other
                # reading is a link away -- which is the whole of the Hartola
                # case: three photographs as an event, 1,286 as a municipality.
                # A filter alone leaves no chips to name, so there is nothing
                # to list back and the sentence has to stand without one.
                names = [html.escape(_term_words(t)) for t in r.terms]
                asked = ("all of that" if not names else names[0] if
                         len(names) == 1 else
                         ", ".join(names[:-1]) + " and " + names[-1])
                body = (head + '<div class="empty">'
                        f'<p>No photograph has {asked or "all of that"}.</p>'
                        '</div>')
                return HTMLResponse(layout(
                    title=f"{q} — Kuva-arkisto", q=html.escape(q), body=body,
                    focus=" autofocus"))
            what = ("photographs" if where else
                    f"photographs &middot; {embedded:,} searchable")
            meta = ((f"{total:,} {what}" if where or not q else
                     f"{total_all:,} {what}")
                    + sort_row(q, f, ranked=False, more=more))
            first = rows[0]["id"] if rows else None
            body = (head + f'<div class="meta">{meta}{play_link(first, here)}</div>'
                    f'<div class="grid">'
                    f'{cards_by_month(rows, here)}</div>'
                    + pager(q, f, page, total, PAGE_SIZE))
            return HTMLResponse(layout(
                title=f"{q} — Kuva-arkisto" if q else "Kuva-arkisto",
                q=html.escape(q), body=body, focus=" autofocus"))

        if embedded == 0:
            # The strips still answer, even with nothing to rank: a partial
            # name is exactly what they are for, and having no vectors is no
            # reason to stop finding people by name.
            return HTMLResponse(layout(
                title=f"{q} — Kuva-arkisto", q=html.escape(q), focus=" autofocus",
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
                return HTMLResponse(layout(
                    title=f"{q} — Kuva-arkisto", q=html.escape(q),
                    focus=" autofocus",
                    body=head + strips + '<div class="empty">'
                         '<p>Nothing matches all of that.</p></div>'))

        vec = text_vector(con, r.text)
        hits = embed.search(con, vec, limit=MAX_RANKED,
                            vec_ok=state["vec_ok"], candidate_ids=cand)
        answers = embed.cut([s for _, s in hits])
        keep = _in_order(con, hits if more else hits[:answers], f["order"])
        window = keep[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

        # The strips answer a different question from the chips: a chip needs
        # the whole name, so "emm" resolves to nothing, and the strip is what
        # turns it into Emmi. They are fed the description alone, so anything
        # already resolved into a chip is not also offered as a suggestion.
        strips = (people_matching(con, r.text) + places_matching(con, r.text)
                  + events_matching(con, r.text))

        if not window:
            return HTMLResponse(layout(
                title=f"{q} — Kuva-arkisto", q=html.escape(q),
                focus=" autofocus",
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
                   else "")
                + sort_row(q, f, ranked=True, more=more))
        tail = pager(q, f, page, len(keep), PAGE_SIZE,
                     more=more, held=held if not more else 0)
        body = (head + strips + f'<div class="meta">{meta}'
                f'{play_link(ids[0], here)}</div>'
                f'<div class="grid">{cards}</div>' + tail)
        return HTMLResponse(layout(
            title=f"{q} — Kuva-arkisto", q=html.escape(q), body=body,
            focus=" autofocus"))

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
                 ORDER BY a.taken_at DESC, a.id DESC LIMIT ?""",
            [*args, limit]).fetchall()
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
                    dsrc: str = Query(default=""), psrc: str = Query(default="")):
        con = connect()
        f = {"from": date_from.strip(), "to": to.strip(), "cam": cam.strip(),
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
                return HTMLResponse(layout(
                    title="Download — Kuva-arkisto", q=html.escape(q), focus="",
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
            return HTMLResponse(layout(
                title="Download — Kuva-arkisto", q=html.escape(q), body=body,
                focus=""))

        def stream():
            sink = _Sink()
            with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED) as z:
                for _, path, _, name in items:
                    try:
                        src = open(on_disk(path), "rb")
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
                if Path(on_disk(f["path"])).exists():
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
            on_disk(path), media_type=kind or "image/jpeg",
            filename=name if download else None)

    # Where a coordinate goes when somebody asks to see it on a map, and the
    # only address in this application that is not this machine or the archive.
    # OpenStreetMap by default because it needs no account and sells nothing;
    # `[ui] map_url` in the config points it anywhere else. `{lat}` and `{lon}`
    # are filled in.
    MAP_URL = ("https://www.openstreetmap.org/?mlat={lat}&mlon={lon}"
               "#map=15/{lat}/{lon}")

    # The same map, drawn in the page rather than in another tab. A frame is
    # only ever written into the HTML when the URL asks for one, so the page as
    # it normally stands still fetches nothing from anywhere (§10.4).
    MAP_EMBED = ("https://www.openstreetmap.org/export/embed.html"
                 "?bbox={w},{s},{e},{n}&layer=mapnik&marker={lat},{lon}")

    # How much ground the frame shows around the point, in degrees. Roughly a
    # kilometre across, which is close enough to recognise a beach by and wide
    # enough not to look like surveillance.
    MAP_SPAN = 0.006

    def _map_host(url: str) -> str:
        host = (url.split("/")[2] if "//" in url else url)
        return host[4:] if host.startswith("www.") else host

    def _gps_fact(a, asset_id, back, shown: bool) -> str:
        """The coordinate, and the map only once somebody has asked for one.

        Showing it in the page rather than in another tab costs nothing extra in
        privacy, because the deciding property is *when* anything is fetched,
        not where it is drawn: the frame exists only on the request that asks
        for it. What it saves is the place in the archive -- the photograph is
        still there beside the map, and going back is not a browser button.
        """
        if a["gps_lat"] is None:
            return ""
        lat, lon = a["gps_lat"], a["gps_lon"]
        out = f'<div class="coord">{lat:.5f}, {lon:.5f}</div>'
        away = str(cfg.ui.get("map_url") or MAP_URL).format(
            lat=f"{lat:.6f}", lon=f"{lon:.6f}")
        host = _map_host(away)

        if not shown:
            here = (f"/photo/{asset_id}?map=1&back={quote_plus(back)}#map")
            return (out + f'<a class="maplink" href="{here}">{_icon("pin")}'
                    'see it on a map</a>'
                    f'<div class="note">loads a map from {html.escape(host)}, '
                    'which means sending it this coordinate</div>')

        embed = str(cfg.ui.get("map_embed_url") or MAP_EMBED).format(
            lat=f"{lat:.6f}", lon=f"{lon:.6f}",
            w=f"{lon - MAP_SPAN:.6f}", e=f"{lon + MAP_SPAN:.6f}",
            s=f"{lat - MAP_SPAN:.6f}", n=f"{lat + MAP_SPAN:.6f}")
        gone = f"/photo/{asset_id}?back={quote_plus(back)}#where"
        return (out
                + f'<div class="map" id="map"><iframe src="{html.escape(embed)}" '
                'loading="lazy" referrerpolicy="no-referrer" '
                'title="where this was taken"></iframe></div>'
                + f'<div class="note"><a href="{gone}">hide the map</a> &middot; '
                f'<a href="{html.escape(away)}" target="_blank" '
                f'rel="noreferrer noopener">open it at {html.escape(host)}</a></div>')

    def _fact(k, v, *, note="", edit="", anchor="") -> str:
        """One line of Edit & details: the fact, where it came from, and the
        means to put it right."""
        if v in (None, "") and not edit:
            return ""
        ident = f' id="{anchor}"' if anchor else ""
        return (f'<div class="frow"{ident}><div class="k">{k}</div><div>{v}'
                + (f' <span class="note">&mdash; {note}</span>' if note else "")
                + (f'<div class="fix">{edit}</div>' if edit else "")
                + '</div></div>')

    def _fix_form(asset_id, kind, back, *, value="", placeholder="",
                  extra="") -> str:
        if ro:
            return ""
        return (
            f'<form method="post" action="/photo/{asset_id}/{kind}">'
            f'<input type="hidden" name="back" value="{html.escape(back)}">'
            f'{extra}'
            f'<input type="text" name="value" value="{html.escape(value)}" '
            f'placeholder="{html.escape(placeholder)}">'
            '<button type="submit">put it right</button></form>')

    def _undo_form(asset_id, kind, back, *, idx=None) -> str:
        if ro:
            return ""
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
        if ro:                      # answered where the archive is kept
            return ""
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
        what = ("One open question" if len(rows) == 1
                else f"{len(rows)} open questions")
        return (f'<a class="qlink" href="/review">{_icon("q")}{what} '
                'about this photograph</a>')

    def _in_order(con, hits: list, order: str) -> list:
        """A ranked result put in time order, when that is asked for.

        The cut has already chosen which photographs answer (§7.3); this
        changes only which comes first, in the grid and in the walk alike.
        """
        if order not in ORDERS or not hits:
            return hits
        ids = [a for a, _ in hits]
        when = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for row in con.execute(
                    "SELECT id, COALESCE(taken_at, 0) t FROM assets "
                    f"WHERE id IN ({','.join('?' * len(chunk))})", chunk):
                when[row["id"]] = (row["t"], row["id"])
        return sorted(hits, key=lambda h: when.get(h[0], (0, h[0])),
                      reverse=order == "newest")

    def sort_row(q, f, *, ranked: bool, more) -> str:
        """The order, as a switch beside the count (§7.3).

        An exact result has no order but time, newest first unless asked
        otherwise; a ranked one is best match first and may be put in time
        order instead. Changing it starts again from the first page.
        """
        choices = ((("", "Best match"), ("newest", "Newest"),
                    ("oldest", "Oldest")) if ranked else
                   (("", "Newest"), ("oldest", "Oldest")))
        now = f.get("order", "")
        if not ranked and now == "newest":
            now = ""
        links = "".join(
            f'<a href="{_qs(q, {**f, "order": v}, more=more or None)}"'
            + (' class="on"' if now == v else "") + f'>{label}</a>'
            for v, label in choices)
        return f' <span class="sort">{links}</span>'

    def _to_card(back: str, asset_id: int, at: int) -> str:
        """The way back, landing on this photograph's card rather than the top.

        The browser's own back button returns to where the grid was left, but
        only to the page the photograph was opened from: after walking on with
        the arrows, the card wanted is the one walked to. So the anchor is this
        photograph, and on a search the page is whichever holds it now.
        """
        parts = urlparse(back)
        if at and parts.path in ("", "/"):
            pairs = [(k, v) for k, v in parse_qsl(parts.query) if k != "page"]
            page = (at - 1) // PAGE_SIZE
            if page:
                pairs.append(("page", str(page)))
            back = "/?" + urlencode(pairs) if pairs else "/"
        return f"{back}#p{asset_id}"

    def _search_from(back: str):
        """The search a photograph was opened from, read back out of `back`.

        The way back already carries the whole question -- the words, the
        filters, whether the weaker results were being shown. Reading it again
        here is what lets the arrows follow that result rather than some other
        order, and it means nothing has to be remembered between requests.
        """
        parts = urlparse(back or "/")
        if parts.path not in ("", "/"):
            return None                      # opened from a person, place, event
        qs = parse_qs(parts.query)

        def one(k):
            return (qs.get(k) or [""])[0].strip()

        return (one("q"), {k: one(k) for k in CARRIED},
                1 if one("more") in ("1", "true") else 0)

    def _ranked_ids(con, key, r, where, args, more, order=""):
        """The ranked result, in order, kept for as long as it is being walked.

        Arrowing through fifty photographs would otherwise encode the query and
        search the index fifty times over for an answer that cannot have
        changed. One entry is enough: a person looks at one result set at a time.
        """
        if state.get("ranked_key") == key:
            return state["ranked_ids"]
        cand = None
        if where:
            cand = [x[0] for x in con.execute(
                f"SELECT a.id FROM assets a WHERE {where}", args)]
            if not cand:
                return []
        vec = text_vector(con, r.text)
        hits = embed.search(con, vec, limit=MAX_RANKED,
                            vec_ok=state["vec_ok"], candidate_ids=cand)
        keep = _in_order(con, hits if more else hits[:embed.cut([s for _, s in hits])],
                         order)
        ids = [a for a, _ in keep]
        state["ranked_key"], state["ranked_ids"] = key, ids
        return ids

    def _neighbours(con, asset_id: int, back: str):
        """(previous, next, position, total) within the result this came from.

        An exact result is ordered by date, so its neighbours are two indexed
        comparisons rather than a list -- the whole 39,884 need not be built to
        find the one photograph either side. A ranked result has no such
        shortcut, its order being the scores themselves, so that one is
        recomputed and held.
        """
        got = _search_from(back)
        if got is None:
            return None
        q, f, more = got
        r = query.resolve(con, q, place_counts=subtree_counts(con))
        where, args = _where(con, r, f)
        pred = f" AND {where}" if where else ""

        if not r.text:
            cur = con.execute(
                "SELECT COALESCE(taken_at, 0) t, id FROM assets WHERE id = ?",
                (asset_id,)).fetchone()
            if cur is None:
                return None
            key = (cur["t"], cur["id"])
            # Ordered by date and then id: a burst shares a second, and without
            # the id the order is not total and the walk could loop. "Next" is
            # further down the page: earlier in time newest first, later oldest.
            oldest = f.get("order") == "oldest"
            ahead, behind = (">", "<") if oldest else ("<", ">")
            fwd, rev = ("ASC", "DESC") if oldest else ("DESC", "ASC")
            nxt = con.execute(
                f"""SELECT a.id FROM assets a
                     WHERE (COALESCE(a.taken_at,0), a.id) {ahead} (?, ?){pred}
                     ORDER BY COALESCE(a.taken_at,0) {fwd}, a.id {fwd} LIMIT 1""",
                [*key, *args]).fetchone()
            prv = con.execute(
                f"""SELECT a.id FROM assets a
                     WHERE (COALESCE(a.taken_at,0), a.id) {behind} (?, ?){pred}
                     ORDER BY COALESCE(a.taken_at,0) {rev}, a.id {rev} LIMIT 1""",
                [*key, *args]).fetchone()
            before = con.execute(
                f"""SELECT COUNT(*) FROM assets a
                     WHERE (COALESCE(a.taken_at,0), a.id) {behind} (?, ?){pred}""",
                [*key, *args]).fetchone()[0]
            total = con.execute(
                f"SELECT COUNT(*) FROM assets a"
                + (f" WHERE {where}" if where else ""), args).fetchone()[0]
            return (prv["id"] if prv else None, nxt["id"] if nxt else None,
                    before + 1, total)

        ids = _ranked_ids(con, (q, tuple(sorted(f.items())), more),
                          r, where, args, more, f.get("order", ""))
        if asset_id not in ids:
            return None
        i = ids.index(asset_id)
        return (ids[i - 1] if i > 0 else None,
                ids[i + 1] if i + 1 < len(ids) else None,
                i + 1, len(ids))

    def _when_words(taken_at, precision):
        """(the date as the facts write it, the heading, the weekday and time).

        The heading says the date as far as it is known, and the weekday and
        the time only where they are: a scan dated by its folder has neither.
        """
        if not taken_at:
            return "unknown", "When is not known", ""
        d = datetime.fromtimestamp(taken_at)
        p = precision
        when = (f"{d:%Y}" if p == "year" else f"{d:%B %Y}" if p == "month"
                else f"{d.day} {d:%B %Y}" if p == "day"
                else f"{d.day} {d:%B %Y}, {d:%H:%M}")
        aside = ("" if p in ("year", "month") else f"{d:%A}" if p == "day"
                 else f"{d:%A} &middot; {d:%H:%M}")
        return when, when.split(",")[0], aside

    def _place_short(con, place_id):
        """(id, "Pärnu, Estonia", the whole trail) for a place, or None."""
        if not place_id:
            return None
        pl = con.execute("SELECT id, name, path FROM places WHERE id = ?",
                         (place_id,)).fetchone()
        if pl is None:
            return None
        parts = pl["path"].split("/")
        short = pl["name"] + (f", {parts[0]}" if len(parts) > 1 else "")
        return pl["id"], short, " · ".join(reversed(parts))

    def _slide(con, asset_id: int):
        """One photograph as the slideshow shows it: its thumbnail and a line
        saying when, where and who."""
        a = con.execute("SELECT id, content_hash, taken_at, taken_at_precision, "
                        "place_id FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if a is None:
            return None
        when, _, _ = _when_words(a["taken_at"], a["taken_at_precision"])
        place = _place_short(con, a["place_id"])
        names = [r["name"] for r in con.execute(
            """SELECT p.name FROM faces f JOIN persons p ON p.id = f.person_id
                WHERE f.asset_id = ? GROUP BY p.id ORDER BY MIN(f.idx)""",
            (asset_id,))]
        said = [when if a["taken_at"] else "", place[1] if place else "",
                ", ".join(names)]
        return {"id": a["id"], "hash": a["content_hash"],
                "caption": " · ".join(s for s in said if s)}

    def _following(con, asset_id: int, back: str, n: int):
        """(total, position of the first, ids): the photographs after this one
        in the result it came from, in the order the arrows walk, n at a time.

        A few at a time so that playing the whole library never builds a list
        of all 39,884 of it. The same two orders as `_neighbours`: by date for
        an exact result, by the held ranking for a described one.
        """
        got = _search_from(back)
        if got is None:
            return 0, 0, []
        q, f, more = got
        r = query.resolve(con, q, place_counts=subtree_counts(con))
        where, args = _where(con, r, f)
        pred = f" AND {where}" if where else ""
        if not r.text:
            cur = con.execute(
                "SELECT COALESCE(taken_at, 0) t, id FROM assets WHERE id = ?",
                (asset_id,)).fetchone()
            if cur is None:
                return 0, 0, []
            key = (cur["t"], cur["id"])
            oldest = f.get("order") == "oldest"
            ahead, behind = (">", "<") if oldest else ("<", ">")
            way = "ASC" if oldest else "DESC"
            ids = [x["id"] for x in con.execute(
                f"""SELECT a.id FROM assets a
                     WHERE (COALESCE(a.taken_at,0), a.id) {ahead} (?, ?){pred}
                     ORDER BY COALESCE(a.taken_at,0) {way}, a.id {way} LIMIT ?""",
                [*key, *args, n])]
            before = con.execute(
                f"""SELECT COUNT(*) FROM assets a
                     WHERE (COALESCE(a.taken_at,0), a.id) {behind} (?, ?){pred}""",
                [*key, *args]).fetchone()[0]
            total = con.execute(
                "SELECT COUNT(*) FROM assets a"
                + (f" WHERE {where}" if where else ""), args).fetchone()[0]
            return total, before + 2, ids
        ranked = _ranked_ids(con, (q, tuple(sorted(f.items())), more),
                             r, where, args, more, f.get("order", ""))
        if asset_id not in ranked:
            return 0, 0, []
        i = ranked.index(asset_id)
        return len(ranked), i + 2, ranked[i + 1:i + 1 + n]

    @app.get("/slides")
    def slides(back: str = Query(default="/"), at: int = 0, n: int = 10):
        """What the slideshow plays next, after photograph `at` (§8.2)."""
        con = connect()
        total, first, ids = _following(con, at, safe_back(back),
                                       max(1, min(n, 50)))
        out = []
        for k, aid in enumerate(ids):
            s = _slide(con, aid)
            if s is not None:
                s.update(at=first + k, total=total)
                out.append(s)
        return {"total": total, "slides": out}

    # The largest side the slideshow asks an original for. A screen past it
    # gains nothing, and every extra pixel still crosses the network.
    VIEW_MAX = 2560

    @app.get("/view/{asset_id}")
    def view(asset_id: int, w: int = 1920):
        """The photograph fitted to the screen showing it, read from its original.

        For the slideshow, and only when its setting asks (§8.2). Browsing never
        pulls an original across the network (§7.5); turning the setting on is
        the explicit request that rule waits for. Nothing is kept: a cache at
        this size would add about 13 GB, for photographs mostly seen once.
        """
        con = connect()
        path = _reachable(con, asset_id)
        if path is None:
            return Response(status_code=503)
        try:
            im = images.load(on_disk(path),
                             max_side=max(320, min(w, VIEW_MAX)))
        except OSError:
            return Response(status_code=503)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=86)
        return Response(buf.getvalue(), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})

    def play_link(first_id, here: str) -> str:
        """Play a result as a slideshow, from the first photograph on the page."""
        if first_id is None:
            return ""
        return (f'<a class="play" href="/photo/{first_id}?play=1&back='
                f'{quote_plus(here)}">{_icon("play")}Slideshow</a>')

    def slide_controls() -> str:
        return (
            '<button class="vbtn" id="play" type="button" '
            f'title="Slideshow (Space)">{_icon("play")}</button>'
            '<details class="pop slideopts"><summary title="Slideshow settings">'
            f'{_icon("sliders")}</summary><div class="menu">'
            '<label class="opt">Each photograph for'
            '<select id="speed"><option value="3">3 seconds</option>'
            '<option value="5" selected>5 seconds</option>'
            '<option value="10">10 seconds</option></select></label>'
            '<label class="opt check"><input type="checkbox" id="sharp">'
            '<span>Sharper pictures from the NAS<small>While the slideshow plays, '
            'each original is read over the network and fitted to the screen. '
            'When the NAS is not answering, the preview size is shown instead.'
            '</small></span></label></div></details>'
            '<button class="vbtn" id="fs" type="button" '
            f'title="Full screen (F)">{_icon("screen")}</button>')

    # The slideshow plays inside the page rather than from page to page, so a
    # full screen stays full. It asks `/slides` for what comes next a few at a
    # time, has the next picture before it is due, and on pause reloads the
    # photograph showing so its panel is that photograph's. Speed and the
    # sharper pictures are remembered by the browser. A sharp picture that has
    # not arrived in eight seconds leaves the thumbnail showing, says so once,
    # and asking again waits two minutes: the NAS is not always on.
    SLIDE_JS = """<script>(function(){
var st=document.querySelector('.stage');if(!st||!st.dataset.back)return;
var img=document.querySelector('.pic img'),cap=document.getElementById('cap'),
prog=document.getElementById('prog'),nas=document.getElementById('nas'),
btn=document.getElementById('play'),speed=document.getElementById('speed'),
sharp=document.getElementById('sharp'),pos=document.querySelector('.vbar .pos');
var back=st.dataset.back,home=+st.dataset.id,queue=[],shown=[],timer=null,
idle=null,fetching=false,done=false;
var cur={id:home,hash:st.dataset.hash,caption:st.dataset.caption};
function get(k,d){try{var v=localStorage.getItem(k);return v===null?d:v;}catch(e){return d;}}
function put(k,v){try{localStorage.setItem(k,v);}catch(e){}}
speed.value=get('slides.speed','5');sharp.checked=get('slides.sharp','0')==='1';
speed.onchange=function(){put('slides.speed',speed.value);if(timer)restart();};
sharp.onchange=function(){put('slides.sharp',sharp.checked?'1':'0');if(timer)fetchSharp(cur);};
function secs(){return +speed.value||5;}
function nasDown(){try{return Date.now()-(+sessionStorage.getItem('slides.nasdown')||0)<120000;}catch(e){return false;}}
function down(){try{sessionStorage.setItem('slides.nasdown',String(Date.now()));}catch(e){}nas.hidden=false;}
function url(s){return '/photo/'+s.id+'?back='+encodeURIComponent(back);}
function fetchSharp(s){
 if(!s||s.im||!sharp.checked||nasDown())return;
 var side=Math.round(Math.max(screen.width||0,screen.height||0,innerWidth,innerHeight)
  *(window.devicePixelRatio||1))||1920;
 var im=new Image(),t=setTimeout(function(){if(!s.sharp)down();},8000);
 s.im=im;
 im.onload=function(){clearTimeout(t);s.sharp=im.src;nas.hidden=true;if(cur===s)img.src=im.src;};
 im.onerror=function(){clearTimeout(t);down();};
 im.src='/view/'+s.id+'?w='+side;}
function warm(s){if(!s)return;if(!s.th){s.th=new Image();s.th.src='/thumb/m/'+s.hash;}fetchSharp(s);}
function fill(){
 if(fetching||done||queue.length>2)return;fetching=true;
 var last=queue.length?queue[queue.length-1]:cur;
 fetch('/slides?n=10&at='+last.id+'&back='+encodeURIComponent(back))
 .then(function(r){return r.json();})
 .then(function(d){fetching=false;if(!d.slides.length)done=true;
  queue=queue.concat(d.slides);warm(queue[0]);})
 .catch(function(){fetching=false;});}
function show(s){
 cur=s;img.src=s.sharp||'/thumb/m/'+s.hash;cap.textContent=s.caption;
 if(s.at&&pos)pos.textContent=s.at.toLocaleString('en')+' of '+s.total.toLocaleString('en');
 history.replaceState(null,'',url(s)+'&play=1');
 fetchSharp(s);warm(queue[0]);fill();}
function restart(){
 clearTimeout(timer);timer=setTimeout(step,secs()*1000);
 st.style.setProperty('--slide',secs()+'s');
 prog.classList.remove('run');void prog.offsetWidth;prog.classList.add('run');}
function step(){
 if(!queue.length){if(done){pause();return;}fill();restart();return;}
 shown.push(cur);show(queue.shift());restart();}
function stepBack(){if(!shown.length)return;queue.unshift(cur);show(shown.pop());restart();}
function icon(n){btn.querySelector('use').setAttribute('href','#i-'+n);}
function wake(){document.body.classList.remove('idle');clearTimeout(idle);
 if(timer)idle=setTimeout(function(){document.body.classList.add('idle');},2500);}
function play(){
 document.body.classList.add('playing');window.scrollTo(0,0);
 icon('pause');btn.title='Pause (Space)';cap.textContent=cur.caption;
 fetchSharp(cur);fill();restart();wake();}
function pause(){
 clearTimeout(timer);timer=null;prog.classList.remove('run');
 document.body.classList.remove('playing','idle');icon('play');btn.title='Slideshow (Space)';
 if(cur.id!==home)location.replace(url(cur));else history.replaceState(null,'',url(cur));}
function fullscreen(){
 if(document.fullscreenElement)document.exitFullscreen();
 else if(document.documentElement.requestFullscreen)
  document.documentElement.requestFullscreen().catch(function(){});}
btn.onclick=function(){if(timer)pause();else play();btn.blur();};
document.getElementById('fs').onclick=fullscreen;
document.addEventListener('mousemove',wake);
['go-prev','go-next'].forEach(function(id,i){var a=document.getElementById(id);
 if(a)a.addEventListener('click',function(e){if(!timer)return;e.preventDefault();
  if(i)step();else stepBack();});});
window.addEventListener('keydown',function(e){
 var t=e.target.tagName;if(t==='INPUT'||t==='SELECT'||t==='TEXTAREA')return;
 var k=e.key,hit=true;
 if(k===' '){if(timer)pause();else play();}
 else if(k==='f'||k==='F')fullscreen();
 else if(!timer)hit=false;
 else if(k==='ArrowRight')step();
 else if(k==='ArrowLeft')stepBack();
 else if(k==='Escape')pause();
 else hit=false;
 if(hit){e.preventDefault();e.stopImmediatePropagation();}
},true);
if(st.dataset.play==='1')play();
})();</script>"""

    def _walk(con, asset_id, back):
        """The arrows either side of the photograph and where it sits in the
        result, following whatever brought the reader here. Nothing when it was
        not a search: arriving from a person or a place is not a walk."""
        got = _neighbours(con, asset_id, back)
        if got is None:
            return "", "", "", 0
        prv, nxt, at, total = got
        b = quote_plus(back)

        def step(aid, side, title, icon):
            if aid is None:
                return ""
            return (f'<a class="arrow {side}" href="/photo/{aid}?back={b}" '
                    f'id="go-{side}" rel="{side}" title="{title}">'
                    f'{_icon(icon)}</a>')

        return (step(prv, "prev", "Previous (&larr;)", "left"),
                step(nxt, "next", "Next (&rarr;)", "right"),
                f'<span class="pos">{at:,} of {total:,}</span>', at)

    # Arrow keys, because a result set is walked rather than clicked through,
    # and Esc back to it. No framework, and the links work without it.
    WALK_KEYS = (
        "<script>document.addEventListener('keydown',function(e){"
        "var t=e.target.tagName;"
        "if(t==='INPUT'||t==='TEXTAREA'||t==='SELECT')return;"
        "var k=e.key==='ArrowLeft'?'go-prev':e.key==='ArrowRight'?'go-next':"
        "e.key==='Escape'?'go-back':0;"
        "if(!k)return;var a=document.getElementById(k);"
        "if(a){e.preventDefault();a.click();}});</script>")

    # And a swipe, because on a phone the arrows are where the thumb is not.
    # It presses the same links, so the slideshow, which takes those clicks
    # over while it plays, steps on a swipe too. Mostly sideways and at least
    # 50 px, so scrolling the page or pinching the picture is not a swipe.
    SWIPE_JS = (
        "<script>(function(){var p=document.querySelector('.pic');if(!p)return;"
        "var x=null,y=0;"
        "p.addEventListener('touchstart',function(e){"
        "if(e.touches.length!==1){x=null;return;}"
        "x=e.touches[0].clientX;y=e.touches[0].clientY;},{passive:true});"
        "p.addEventListener('touchend',function(e){if(x===null)return;"
        "var t=e.changedTouches[0],dx=t.clientX-x,dy=t.clientY-y;x=null;"
        "if(Math.abs(dx)<50||Math.abs(dx)<1.5*Math.abs(dy))return;"
        "var a=document.getElementById(dx<0?'go-next':'go-prev');"
        "if(a)a.click();});})();</script>")

    @app.get("/photo/{asset_id}", response_class=HTMLResponse)
    def photo(asset_id: int, back: str = Query(default="/"),
              msg: str = Query(default=""), map: int = 0, edit: int = 0,
              play: int = 0):
        """One photograph (§8.2): the picture, what matters about it beside
        it, and everything for putting it right one click further."""
        con = connect()
        a = con.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if a is None:
            return HTMLResponse("<p>not found</p>", status_code=404)
        back = safe_back(back)
        h = a["content_hash"]
        b = quote_plus(back)
        fixed = {(r["kind"], r["face_idx"]): r
                 for r in corrections.for_asset(con, h)}

        files = con.execute(
            "SELECT path FROM files WHERE asset_id = ? ORDER BY path",
            (asset_id,)).fetchall()

        when, heading, aside = _when_words(a["taken_at"],
                                           a["taken_at_precision"])
        typed = (f'{datetime.fromtimestamp(a["taken_at"]):%Y-%m-%d}'
                 if a["taken_at"] else "")

        # Every fact says where it came from, under Edit & details. A date and
        # a place are guesses of very different quality, and one that arrived
        # from a folder name should never read like one measured by a satellite.
        date_note = f'{a["taken_at_source"]}, {a["taken_at_precision"]}'
        if ("date", -1) in fixed:
            date_note = "put right by hand"
        # 1,294 photographs carry a date conflict, which is worth a line under
        # the date itself rather than only in the details.
        warn = ('<div class="warn">Its dates disagree &mdash; see '
                f'{"Details" if ro else "Edit &amp; details"}</div>'
                if a["conflict_note"] and ("date", -1) not in fixed else "")
        rows = "".join([
            _fact("taken", html.escape(when), note=date_note,
                  edit=(_undo_form(asset_id, "date", back)
                        if ("date", -1) in fixed else
                        _fix_form(asset_id, "date", back, value=typed,
                                  placeholder="1998-07, or kesä 1998"))),
            _place_fact(con, a, asset_id, back, ("place", -1) in fixed),
            _event_row(con, asset_id),
            _fact("source", html.escape(a["source_hint"] or "")),
            _fact("conflict", html.escape(a["conflict_note"] or "")),
            _fact("hash", h[:24] + "&hellip;"),
        ])

        def fact(icon, k, v, anchor=""):
            ident = f' id="{anchor}"' if anchor else ""
            return (f'<div class="fact"{ident}>{_icon(icon)}<div>'
                    f'<div class="k">{k}</div>{v}</div></div>')

        # Who is in it: a face and a name beside the picture, and under Edit &
        # details the means to say otherwise -- one row that outranks the
        # clusterer for good (§4.7).
        in_photo = con.execute(
            """SELECT fa.idx, fa.cluster_id, p.id AS pid, p.name
               FROM faces fa LEFT JOIN persons p ON p.id = fa.person_id
               WHERE fa.asset_id = ? ORDER BY fa.idx""",
            (asset_id,)).fetchall()
        chips, cards = [], []
        for f in in_photo:
            fix = fixed.get(("person", f["idx"]))
            name = fix["value"] if fix else f["name"]
            href = (f'/person/{f["pid"]}' if f["pid"] and not fix else
                    (f'/cluster/{f["cluster_id"]}' if f["cluster_id"]
                     and not fix else None))
            label = html.escape(name or "not named")
            linked = f'<a href="{href}">{label}</a>' if href else label
            img = f'<img src="/face/{h}/{f["idx"]}" alt="">'
            cls = "face-chip" if name else "face-chip unnamed"
            chips.append(f'<a class="{cls}" href="{href}">{img}{label}</a>'
                         if href else f'<span class="{cls}">{img}{label}</span>')
            cards.append(
                f'<figure class="mug">{img}'
                f'<figcaption><b>{linked}</b>'
                + ('<span>by hand</span>' if fix else "")
                + '</figcaption>'
                + (_undo_form(asset_id, "person", back, idx=f["idx"]) if fix
                   else _fix_form(asset_id, "person", back,
                                  placeholder="who is this?",
                                  extra=f'<input type="hidden" name="idx" '
                                        f'value="{f["idx"]}">'))
                + '</figure>')
        who = (fact("people", "In the photograph",
                    f'<div class="faces-in">{"".join(chips)}</div>')
               if chips else "")

        place = ""
        short = _place_short(con, a["place_id"])
        if short:
            place = (f'<a class="link" href="/place/{short[0]}" '
                     f'title="{html.escape(short[2])}">{html.escape(short[1])}</a>')
        gps = _gps_fact(a, asset_id, back, bool(map))
        where = (fact("pin", "Where", place + gps, anchor="where")
                 if place or gps else "")

        e = events_mod.for_asset(con, asset_id)
        event = ""
        if e is not None:
            label = e["name"] or f'{e["asset_count"]:,} photographs, unnamed'
            event = fact("event", "Event",
                         f'<a class="link" href="/event/{e["id"]}">'
                         f'{html.escape(label)}</a>')
        made = html.escape(" ".join(filter(
            None, [a["camera_make"], a["camera_model"]])))
        size = f'{a["width"]}&times;{a["height"]}' if a["width"] else ""
        camera = (fact("camera", "Camera",
                       " &middot; ".join(filter(None, [made, size])))
                  if made or size else "")

        # The pin sits on the picture only where there is somewhere to show,
        # and says what pressing it costs; the map itself still loads nothing
        # until it is pressed (§10.4).
        pin = ""
        if a["gps_lat"] is not None:
            host = _map_host(str(cfg.ui.get("map_url") or MAP_URL))
            pin = (f'<a class="vbtn on" href="/photo/{asset_id}?back={b}#where" '
                   f'title="Hide the map">{_icon("pin")}</a>' if map else
                   f'<a class="vbtn" href="/photo/{asset_id}?map=1&back={b}#map" '
                   f'title="Show it on a map (loads a map from '
                   f'{html.escape(host)})">{_icon("pin")}</a>')

        prev_a, next_a, pos, at = _walk(con, asset_id, back)
        # The slideshow plays from here, and only where there is a result to
        # play through.
        slide = stage = ""
        if pos:
            me = _slide(con, asset_id)
            stage = (f' data-id="{asset_id}" data-hash="{h}" '
                     f'data-back="{html.escape(back)}" '
                     f'data-caption="{html.escape(me["caption"])}" '
                     f'data-play="{1 if play else 0}"')
            slide = slide_controls()
        note = f'<div class="notice">{html.escape(msg)}</div>' if msg else ""
        paths = "".join(f'<div>{html.escape(f["path"])}</div>' for f in files)
        # Open again after a correction, so several can be made in a row. A
        # viewing copy keeps the details -- where each fact came from, the
        # paths, the original -- and leaves out the means of changing them.
        opener = ("" if ro else
                  f'<form class="openfile" method="post" action="/photo/{asset_id}/open">'
                  f'<input type="hidden" name="back" value="{html.escape(back)}">'
                  '<button class="linkish" type="submit">Open it on this computer'
                  '</button></form>')
        details = (
            f'<details class="edit"{" open" if edit or msg else ""}>'
            f'<summary>{_icon("info" if ro else "pen")}'
            f'{"Details" if ro else "Edit &amp; details"}</summary>'
            + (f'<h3>Who is this?</h3><div class="mugs">{"".join(cards)}</div>'
               if cards and not ro else "")
            + f'<h3>What is known, and from where</h3>{rows}'
            + '<h3>The original</h3>'
            f'<div class="orig"><a href="/full/{asset_id}">See it at full size</a>'
            f'<a href="/full/{asset_id}?download=1">Download it</a>{opener}</div>'
            f'<div class="paths">{paths}</div></details>')
        body = (
            f'<div class="viewer"><section class="stage"{stage}>'
            '<div class="progress" id="prog"></div><div class="vbar">'
            f'<a class="vbtn" id="go-back" '
            f'href="{html.escape(_to_card(back, asset_id, at))}" '
            f'title="Back (Esc)">{_icon("back")}'
            f'{"Back to the results" if pos else "Back"}</a>{pos}'
            f'<span class="spacer"></span>{slide}{pin}'
            f'<a class="vbtn" href="/full/{asset_id}" '
            f'title="See the original at full size">{_icon("full")}</a>'
            f'<a class="vbtn" href="/full/{asset_id}?download=1" '
            f'title="Download the original">{_icon("down")}</a>'
            + ("" if ro else
               f'<form class="openfile" method="post" action="/photo/{asset_id}/open">'
               f'<input type="hidden" name="back" value="{html.escape(back)}">'
               '<button class="vbtn" type="submit" '
               'title="Open the file in its application on this computer">'
               f'{_icon("open")}</button></form>')
            + '</div><div class="pic">'
            f'{prev_a}<img src="/thumb/m/{h}" alt="">{next_a}'
            '</div><div class="caption" id="cap"></div>'
            '<div class="nas" id="nas" hidden>The NAS is not answering '
            '&mdash; showing the preview size</div>'
            '<div class="toast" id="opened" role="status" hidden></div>'
            '</section><aside class="side">'
            f'{note}<h1>{html.escape(heading)}</h1>'
            + (f'<div class="sub">{aside}</div>' if aside else "")
            + warn
            + f'<div class="facts">{who}{where}{event}{camera}</div>'
            + _questions(con, asset_id) + details + '</aside></div>'
            + WALK_KEYS + (SWIPE_JS if pos else "") + ("" if ro else OPEN_JS)
            + (SLIDE_JS if pos else ""))
        return HTMLResponse(layout(
            title=f"{when} — Kuva-arkisto", q="", body=body, focus=""))

    def _refuse_open(request: Request):
        """Why this request may not open a file here, or None if it may.

        Opening runs an application on the computer serving the page, so two
        things must hold. The request comes from that computer: served to a
        phone, the file would open on a screen nobody is looking at. And it
        comes from this application's own page rather than from some other
        site the browser has open -- any web page can post a form to
        localhost, and none of them should be able to make this machine open
        files.
        """
        host = request.client.host if request.client else ""
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if not local:
            return ("Opening a file only works on the computer running "
                    "Kuva-arkisto.")
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        if ((site and site not in ("same-origin", "none"))
                or (origin and urlparse(origin).netloc
                    != request.headers.get("host"))):
            return "Refused: that request did not come from Kuva-arkisto's own page."
        return None

    @app.post("/photo/{asset_id}/open")
    def open_original(asset_id: int, request: Request,
                      back: str = Form(default="/")):
        """The original, opened in whatever application this computer opens it
        with (§8.2).

        Like the full-size link, a deliberate click and not browsing (§7.5):
        the file crosses the network only to the application it is handed to.
        """
        con = connect()
        status, said = 200, ""
        refused = _refuse_open(request)
        if refused:
            status, said = 403, refused
        elif con.execute("SELECT 1 FROM assets WHERE id = ?",
                         (asset_id,)).fetchone() is None:
            status, said = 404, "No such photograph."
        else:
            path = _reachable(con, asset_id)
            if path is None:
                status, said = 503, ("Not reachable. Every known copy of this "
                                     "photograph is on a drive that is not "
                                     "answering.")
            else:
                name = path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
                try:
                    open_with_default_app(pathutil.local(path, cfg.mounts))
                    said = f"Opening {name}"
                except OSError as e:
                    status, said = 500, (f"This computer could not open {name}: "
                                         f"{e.strerror or e}")
        if request.headers.get("x-requested-with") == "fetch":
            return JSONResponse({"ok": status == 200, "message": said},
                                status_code=status)
        to = f"/photo/{asset_id}?back={quote_plus(safe_back(back))}"
        if status != 200:
            to += f"&msg={quote_plus(said)}"
        return RedirectResponse(to, status_code=303)

    # Asking to open a file, without leaving the photograph: the answer is a
    # line on the picture for a few seconds. The forms work without it.
    OPEN_JS = (
        "<script>document.querySelectorAll('form.openfile').forEach(function(f){"
        "f.addEventListener('submit',function(e){e.preventDefault();"
        "var n=document.getElementById('opened');"
        "function say(t){n.textContent=t;n.hidden=false;clearTimeout(n.t);"
        "n.t=setTimeout(function(){n.hidden=true;},4000);}"
        "say('Opening…');"
        "fetch(f.action,{method:'POST',headers:{'X-Requested-With':'fetch'},"
        "body:new FormData(f)}).then(function(r){return r.json();})"
        ".then(function(d){say(d.message);})"
        ".catch(function(){say('Kuva-arkisto did not answer.');});});});"
        "</script>")

    def _hash_of(con, asset_id: int):
        r = con.execute("SELECT content_hash FROM assets WHERE id = ?",
                        (asset_id,)).fetchone()
        return None if r is None else r["content_hash"]

    def _back_to(asset_id, back, msg="") -> RedirectResponse:
        # Back with Edit & details open, so several things can be put
        # right in a row.
        to = f"/photo/{asset_id}?back={quote_plus(back)}&edit=1"
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
        if ro:
            return ""
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
                f'{cards_by_month(rows, f"/place/{place_id}")}'
                '</div>')
        return page(f'{p["name"]} — Kuva-arkisto', crumb + listing + grid)

    # ----------------------------------------------------------------------
    # People
    # ----------------------------------------------------------------------

    def page(title, body):
        return HTMLResponse(layout(
            title=title, q="", body=body, focus=""))

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

        if ro:          # naming and merging happen where the archive is kept
            suggestion = shared = panel = strip = ""
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
                f'{cards_by_month(rows, f"/person/{person_id}")}'
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
        return _fact("event", f'<a href="/event/{e["id"]}" '
                     f'style="color:var(--accent)">{html.escape(label)}</a>',
                     note=note)

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
        if ro:
            return ""
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
                + cards_by_month(rows, f"/event/{event_id}")
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

    @app.get("/odd", response_class=HTMLResponse)
    def odd_view(dismissed: int = 0):
        """The photograph in each series that is not like the others (§6.5).

        Ordered by how far out it sits rather than by date, so the striking ones
        are first and the merely unusual are further down. Nothing here is a
        queue to be emptied: most of these are perfectly good photographs that
        happen not to look like the trip they came from.
        """
        con = connect()
        if dismissed:
            rows = con.execute(
                """SELECT a.id, a.content_hash, a.taken_at, a.taken_at_precision,
                          a.camera_model, d.created_at
                     FROM odd_dismissed d JOIN assets a ON a.id = d.asset_id
                    ORDER BY d.created_at DESC""").fetchall()
            if not rows:
                return page("Odd ones out — Kuva-arkisto",
                            '<div class="empty"><p>Nothing has been put back '
                            'yet.</p><p><a href="/odd" '
                            'style="color:var(--accent)">the odd ones</a></p>'
                            '</div>')
            cards = "".join(
                f'<figure class="odd">'
                f'<a href="/photo/{r["id"]}?back=%2Fodd%3Fdismissed%3D1">'
                f'<img loading="lazy" src="/thumb/s/{r["content_hash"]}" alt="">'
                '</a><figcaption>'
                f'<form method="post" action="/odd/{r["id"]}/restore">'
                '<button class="ghost" type="submit">odd after all</button>'
                '</form></figcaption></figure>' for r in rows)
            return page("Odd ones out — Kuva-arkisto",
                        f'<div class="meta">{len(rows):,} put back as belonging '
                        '&middot; <a href="/odd" style="color:var(--accent)">'
                        'the odd ones</a></div>'
                        f'<div class="grid">{cards}</div>')

        found = odd_mod.find(con)
        n_photos = sum(len(v) for v in found.values())
        put_back = con.execute(
            "SELECT COUNT(*) FROM odd_dismissed").fetchone()[0]
        if not found:
            body = ('<div class="empty"><p>Nothing stands out.</p>'
                    '<p>This needs events and search vectors: '
                    '<code>mediaidx embed</code>, then '
                    '<code>mediaidx derive</code>.</p></div>')
            return page("Odd ones out — Kuva-arkisto", body)

        # Worst first, by how far the furthest one in each event has fallen
        # below that event's own middle.
        order = sorted(found.items(),
                       key=lambda kv: kv[1][0][1] - kv[1][0][2])
        parts = []
        for eid, items in order[:60]:
            e = events_mod.get(con, eid)
            if e is None:
                continue
            when = (datetime.fromtimestamp(e["start_at"]).strftime("%B %Y")
                    if e["start_at"] else "")
            title = html.escape(e["name"] or "unnamed")
            cards = []
            for aid, sim, med, spread in items:
                a = con.execute(
                    "SELECT id, content_hash FROM assets WHERE id = ?",
                    (aid,)).fetchone()
                if a is None:
                    continue
                # How far out, in the event's own units -- the number the
                # threshold is actually made of, rather than a raw similarity
                # that means something different in every event.
                how = (med - sim) / spread
                cards.append(
                    '<figure class="odd">'
                    f'<a href="/photo/{a["id"]}?back=%2Fodd">'
                    f'<img loading="lazy" src="/thumb/s/{a["content_hash"]}" '
                    'alt=""></a><figcaption>'
                    f'<span>{how:.0f}&times; out</span>'
                    f'<form method="post" action="/odd/{a["id"]}/dismiss">'
                    '<button class="ghost" type="submit">belongs here</button>'
                    '</form></figcaption></figure>')
            if cards:
                parts.append(
                    f'<div class="meta" style="padding-bottom:0">'
                    f'<a href="/event/{eid}" style="color:var(--accent)">'
                    f'<b>{title}</b></a> &middot; {when} &middot; '
                    f'{e["asset_count"]:,} photographs</div>'
                    f'<div class="grid">{"".join(cards)}</div>')

        head = (f'<div class="meta"><b>{n_photos:,} photographs</b> sit far from '
                f'the middle of their own event, across {len(found):,} of them '
                '&middot; furthest first</div>'
                '<div class="offer" style="padding-left:20px">Most of these are '
                'good photographs that simply do not look like the trip they '
                'came from &mdash; a sign at a museum, a hotel room, a receipt. '
                'Some are in the wrong event because their date is wrong, and '
                'that is fixed on the photograph itself; it moves to the right '
                'event at the next <code>mediaidx derive</code>.'
                + (f' &middot; <a href="/odd?dismissed=1">{put_back:,} put back '
                   'as belonging</a>' if put_back else "") + '</div>')
        return page("Odd ones out — Kuva-arkisto", head + "".join(parts))

    @app.post("/odd/{asset_id}/dismiss")
    def odd_dismiss(asset_id: int):
        con = connect()
        odd_mod.dismiss(con, asset_id)
        con.commit()
        return RedirectResponse("/odd", status_code=303)

    @app.post("/odd/{asset_id}/restore")
    def odd_restore(asset_id: int):
        con = connect()
        odd_mod.restore(con, asset_id)
        con.commit()
        return RedirectResponse("/odd?dismissed=1", status_code=303)

    @app.get("/review", response_class=HTMLResponse)
    def review():
        if ro:
            # Before anything is read: this screen tidies the queue as it
            # goes, and a viewing copy must not try to.
            return page("Review — Kuva-arkisto",
                        '<div class="empty"><p>This is a viewing copy.</p>'
                        '<p>Questions are answered on the computer that '
                        'keeps the archive.</p></div>')
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
    if not cfg.ui.get("read_only"):
        con = db.connect(cfg.database, create=False)
        db.migrate(con)
        con.close()
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")
