"""The search log: every search the UI answers, one json line each (spec 8.7).

Written by `serve` -- on the PC, and in the viewing copy on the NAS, which has a
folder of its own for it -- and read on the PC by `mediaidx searches`. It is for
two things: seeing how the archive is used, and finding what search answers
badly (nothing found, or a word that was not understood as the name it was).

A line records when, where (pc or nas), from which address, what was typed,
how it was read -- people, places, events, dates, and the description left over
-- any filters, and how many photographs it found.
"""

from __future__ import annotations

import json
import sys
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path

_lock = threading.Lock()
_warned: set = set()


def client_ip(request) -> str:
    """The address the search came from. Behind the NAS's reverse proxy every
    request arrives from the proxy, which names the real one first in
    X-Forwarded-For."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd.strip():
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def entry(*, where: str, ip: str, q: str, resolved, filters: dict,
          found, ranked: bool) -> dict:
    by_kind: dict = {}
    for t in resolved.terms:
        by_kind.setdefault(t.kind, []).append(t.label)
    return {
        "t": datetime.now().isoformat(timespec="seconds"),
        "where": where,
        "ip": ip,
        "q": q.strip(),
        "people": by_kind.get("person", []),
        "places": by_kind.get("place", []),
        "events": by_kind.get("event", []),
        "dates": by_kind.get("date", []),
        "mode": by_kind.get("mode", []),
        "text": resolved.text,
        "filters": {k: v.strip() for k, v in filters.items() if v and v.strip()},
        "found": found,
        "ranked": ranked,
    }


def record(path, data: dict) -> None:
    """Append one line. A log that cannot be written never stops a search; it
    says so once, on stderr, where the server's own output goes."""
    path = Path(path)
    line = json.dumps(data, ensure_ascii=False) + "\n"
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except OSError as e:
        if str(path) not in _warned:
            _warned.add(str(path))
            print(f"search log not written: {path}: {e}", file=sys.stderr)


def read(paths) -> tuple[list[dict], list[str]]:
    """Every entry in the given logs, oldest first, and the logs not found."""
    out, missing = [], []
    for p in paths:
        p = Path(p)
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            missing.append(str(p))
            continue
        for line in text.splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue            # a line cut short by a full disk, say
    out.sort(key=lambda e: e.get("t", ""))
    return out, missing


def summary(entries: list[dict], *, top: int = 15) -> dict:
    """What `mediaidx searches` prints, worked out apart from the printing."""
    norm = lambda e: e.get("q", "").casefold() or "(filters only)"
    return {
        "count": len(entries),
        "first": entries[0]["t"] if entries else None,
        "last": entries[-1]["t"] if entries else None,
        "by_where": Counter(e.get("where", "?") for e in entries),
        "by_ip": Counter(f"{e.get('ip') or '?'} ({e.get('where', '?')})"
                         for e in entries).most_common(),
        "by_day": Counter(e["t"][:10] for e in entries if e.get("t")),
        "common": Counter(norm(e) for e in entries).most_common(top),
        "nothing": Counter(norm(e) for e in entries
                           if e.get("found") == 0).most_common(top),
        # Typed, but nothing in it was read as a person, place, event or date:
        # where a name the archive does not know, or misspelled, shows up.
        "unread": Counter(norm(e) for e in entries
                          if e.get("q") and e.get("ranked") and not any(
                              e.get(k) for k in ("people", "places", "events",
                                                 "dates", "mode"))
                          ).most_common(top),
        "recent": entries[-top:][::-1],
    }
