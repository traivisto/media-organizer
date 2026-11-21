"""`mediaidx update`: the routine sequence after new photographs arrive, as one step.

scan, embed, faces, faces cluster, derive, and publish when there is somewhere
to publish to -- the same commands, in the order the README gives them. This
module holds the two things the commands themselves do not: finding a new year
folder that nobody has passed to `add` yet, and counting what a run changed.

A caller that is not a person (Media Organizer) reads lines that begin with
EVENT_PREFIX; everything else is the ordinary human output of each stage.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, asdict

from . import paths as pathutil

EVENT_PREFIX = "@@mediaidx "

# Four digits and nothing else: the shape of the folders `add` was given on
# this library (`1998` .. `2026`). A sibling named so is the next year arriving;
# `Unsorted` or `Scans` beside them were left out on purpose and stay out.
_YEAR = re.compile(r"^(19|20)\d\d$")


def new_year_folders(roots: list[str], *, listdir=os.listdir,
                     isdir=os.path.isdir) -> list[str]:
    """Year folders beside the year roots that are not roots themselves.

    Only a parent that already holds at least one year root is looked in, so
    a library organised some other way is never added to by guesswork.
    Raises OSError when a parent cannot be listed: a NAS that is asleep must
    not look like a year with nothing new in it.
    """
    norm = {r.rstrip("\\/").lower() for r in roots}
    parents: dict[str, str] = {}
    for r in roots:
        r = r.rstrip("\\/")
        parent, _, name = r.rpartition("\\")
        if parent and _YEAR.match(name):
            parents.setdefault(parent.lower(), parent)

    found = []
    for parent in sorted(parents.values()):
        for name in sorted(listdir(pathutil.long_path(parent))):
            if not _YEAR.match(name):
                continue
            path = f"{parent}\\{name}"
            if path.lower() in norm:
                continue
            if isdir(pathutil.long_path(path)):
                found.append(path)
    return found


@dataclass
class Counts:
    photographs: int = 0
    missing_files: int = 0
    faces: int = 0
    named_faces: int = 0


def counts(con) -> Counts:
    one = lambda q: con.execute(q).fetchone()[0]
    has_faces = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='faces'").fetchone()
    return Counts(
        photographs=one("SELECT COUNT(*) FROM assets"),
        missing_files=one("SELECT COUNT(*) FROM files WHERE missing_since IS NOT NULL"),
        faces=one("SELECT COUNT(*) FROM faces") if has_faces else 0,
        named_faces=(one("SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL")
                     if has_faces else 0),
    )


def difference(before: Counts, after: Counts) -> dict:
    a, b = asdict(after), asdict(before)
    return {k: a[k] - b[k] for k in a}


def emit(**event) -> None:
    """One machine-readable line, flushed at once so a reader sees it live."""
    print(EVENT_PREFIX + json.dumps(event, ensure_ascii=False), flush=True)
    sys.stderr.flush()
