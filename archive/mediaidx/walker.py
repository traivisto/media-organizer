"""Directory traversal.

Scope containment (spec P7): the walk covers the folders it is given and
nothing else. It never ascends to a parent, never indexes a sibling, and does
not follow a symlink or junction that leads out of the given subtree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import paths as pathutil


@dataclass
class Candidate:
    path: str          # UNC, as stored
    walk_path: str     # extended-length, for opening
    folder: str        # UNC of the containing folder
    name: str
    size: int
    mtime: int


def _inside(child: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(child),
                                   os.path.normcase(root)]) == os.path.normcase(root)
    except ValueError:      # different drives
        return False


def walk(root: str, cfg, *, recurse: bool = True):
    """Yield every indexable file beneath `root`."""
    root_unc = pathutil.to_unc(root)
    root_long = pathutil.long_path(root_unc)
    real_root = os.path.realpath(root_long)

    exts = cfg.extensions
    skip_dirs = cfg.exclude_dirs
    skip_files = cfg.exclude_files

    for dirpath, dirnames, filenames in os.walk(root_long):
        dirnames[:] = [
            d for d in dirnames
            if d.lower() not in skip_dirs
            and not d.startswith(".")
            # a junction pointing outside the subtree is not ours to index
            and _inside(os.path.realpath(os.path.join(dirpath, d)), real_root)
        ]
        if not recurse:
            dirnames[:] = []

        folder_unc = pathutil.display(dirpath)

        for name in filenames:
            if name.lower() in skip_files:
                continue
            if os.path.splitext(name)[1].lower() not in exts:
                continue
            full_long = os.path.join(dirpath, name)
            try:
                st = os.stat(full_long)
            except OSError:
                continue
            yield Candidate(
                path=os.path.join(folder_unc, name),
                walk_path=full_long,
                folder=folder_unc,
                name=name,
                size=st.st_size,
                mtime=int(st.st_mtime),
            )
