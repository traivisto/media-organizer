"""Places: an offline gazetteer, and two ways of reaching it (spec 6.3).

Like clustering, this job reads the database and nothing else. No file is
opened, no model runs, no network is touched -- which is why it is scheduled
independently of the expensive passes: it can be re-run in about a minute
whenever the gazetteer or the matching rules change.

Two sources, and they are never presented as equal:

  gps     GPS binned to ~1 km and looked up by nearest neighbour. Precise, and
          absent from the first two decades of this library.
  folder  A parsed folder label matched against gazetteer names in full. Coarse
          by construction -- no coordinate is invented -- and the only thing
          that reaches 1998.

A coordinate always wins, because the folder says where photographs were filed
and the coordinate says where one was taken.

The gazetteer is a GeoNames extract: `cities500` (localities of 500 people or
more) over the country and admin tables. Its alternate names are the reason to
use a gazetteer rather than a country list -- this library's owner writes
`Lontoo` and `Tukholma`, and both arrive for free in the `cities500`
alternate-name column.
"""

from __future__ import annotations

import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

GAZETTEER_VERSION = 1

# ~1 km: 0.01 degrees of latitude is 1.11 km. Longitude bins are finer than
# that away from the equator, which costs a few cache misses and nothing else.
BIN = 0.01

# What a nearest-neighbour hit is allowed to claim. Within 25 km of a town of
# 500 people, that town is the answer. Beyond it the honest answer is the
# region, and beyond that the country -- a photograph taken mid-Baltic belongs
# to no locality, and saying otherwise would be inventing precision.
LOCALITY_KM = 25.0
REGION_KM = 100.0
GIVE_UP_KM = 500.0

EARTH_KM = 6371.0


def norm_name(s: str) -> str:
    """The match key: casefolded, diacritics stripped, whitespace collapsed.

    This conflates a and a-umlaut, which are different letters in Finnish. That
    is accepted because the alternative is that a folder typed without umlauts
    matches nothing, and because a match has to cover the whole label anyway.
    """
    s = unicodedata.normalize("NFKD", s.strip().casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.split())


def _useful(norm: str, *, own: bool = False) -> bool:
    """Whether a normalised name is worth matching on.

    The alternate-name column carries postal codes and stray initials, so a
    short or purely numeric one is dropped. A place's *own* name is only held
    to the numeric rule: Ii is a Finnish municipality, and dropping it to be
    rid of two-letter noise would be losing the real thing to save from the
    fake one.
    """
    if norm.replace(" ", "").isdigit():
        return False
    return own or len(norm) > 2


# --------------------------------------------------------------------------
# loading the gazetteer
# --------------------------------------------------------------------------

@dataclass
class GazetteerStats:
    countries: int = 0
    admin1: int = 0
    admin2: int = 0
    localities: int = 0
    names: int = 0
    collisions: int = 0
    started: float = field(default_factory=time.time)

    @property
    def places(self) -> int:
        return self.countries + self.admin1 + self.admin2 + self.localities

    @property
    def elapsed(self) -> float:
        return time.time() - self.started


def _lines(path: Path):
    """GeoNames dumps are tab-separated UTF-8, with `#` comments in some."""
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            line = line.rstrip("\n")
            if line:
                yield line.split("\t")


def _cities_lines(gaz_dir: Path):
    txt = gaz_dir / "cities500.txt"
    if txt.exists():
        yield from _lines(txt)
        return
    zp = gaz_dir / "cities500.zip"
    if not zp.exists():
        raise FileNotFoundError(f"no cities500.txt or cities500.zip in {gaz_dir}")
    with zipfile.ZipFile(zp) as z, z.open("cities500.txt") as fh:
        for raw in fh:
            line = raw.decode("utf-8").rstrip("\n")
            if line:
                yield line.split("\t")


def state(con) -> dict | None:
    row = con.execute(
        "SELECT * FROM gazetteer_state ORDER BY loaded_at DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def load_gazetteer(con, gaz_dir: str | Path, *, progress=None) -> GazetteerStats:
    """Build the place tree. Replaces whatever was there.

    Everything is assembled in memory and written in one transaction: the tree
    is ~236,000 places over ~1.2 million names, and inserting those a statement
    at a time is minutes rather than seconds.
    """
    gaz_dir = Path(gaz_dir)
    st = GazetteerStats()

    rows: list[tuple] = []          # (id, parent, kind, name, path, lat, lon, pop, gnid)
    names: list[tuple] = []         # (place_id, norm, own)
    taken: set[tuple] = set()       # (parent_id, name) -- the spec's uniqueness
    next_id = 1

    def add(parent_id, kind, name, parent_path, lat, lon, pop, gnid,
            own=(), alts=()):
        nonlocal next_id
        name = name.replace("/", " ").strip()
        if not name:
            return None, None
        key = (parent_id, name)
        if key in taken:
            st.collisions += 1
            return None, None
        taken.add(key)
        pid = next_id
        next_id += 1
        path = parent_path + "/" + name if parent_path else name
        rows.append((pid, parent_id, kind, name, path, lat, lon, pop, pop, gnid))
        seen = set()
        # A place's own name and an exonym are not equally safe to match on, so
        # which one a row came from is kept. See `match_folders`.
        for candidate in (name, *own):
            n = norm_name(candidate)
            if n and n not in seen and _useful(n, own=True):
                seen.add(n)
                names.append((pid, n, 1))
        for candidate in alts:
            n = norm_name(candidate)
            if n and n not in seen and _useful(n):
                seen.add(n)
                names.append((pid, n, 0))
        return pid, path

    # Countries. The header line is `#`-commented, so `_lines` skips it for us.
    country_id: dict[str, int] = {}
    country_path: dict[str, str] = {}
    for c in _lines(gaz_dir / "countryInfo.txt"):
        if len(c) < 17:
            continue
        iso = c[0]
        pid, path = add(None, "country", c[4], "", None, None,
                        int(c[7]) if c[7] else 0, int(c[16]) if c[16] else None)
        if pid:
            country_id[iso], country_path[iso] = pid, path
            st.countries += 1

    # admin1: "ES.60 <tab> Valencia <tab> Valencia <tab> 2593113"
    a1_id: dict[str, int] = {}
    a1_path: dict[str, str] = {}
    for c in _lines(gaz_dir / "admin1CodesASCII.txt"):
        iso = c[0].split(".")[0]
        if iso not in country_id or len(c) < 3:
            continue
        pid, path = add(country_id[iso], "admin1", c[1], country_path[iso],
                        None, None, 0, int(c[3]) if len(c) > 3 and c[3] else None,
                        own=(c[2],))
        if pid:
            a1_id[c[0]], a1_path[c[0]] = pid, path
            st.admin1 += 1

    # admin2: "ES.60.03 <tab> Alicante <tab> Alicante <tab> 2521976"
    a2_id: dict[str, int] = {}
    a2_path: dict[str, str] = {}
    for c in _lines(gaz_dir / "admin2Codes.txt"):
        parent_code = ".".join(c[0].split(".")[:2])
        if parent_code not in a1_id or len(c) < 3:
            continue
        pid, path = add(a1_id[parent_code], "admin2", c[1], a1_path[parent_code],
                        None, None, 0, int(c[3]) if len(c) > 3 and c[3] else None,
                        own=(c[2],))
        if pid:
            a2_id[c[0]], a2_path[c[0]] = pid, path
            st.admin2 += 1

    # Localities, most populous first: where two share a name under the same
    # parent, UNIQUE(parent_id, name) keeps one, and this is what makes it the
    # one anybody meant.
    cities = []
    for c in _cities_lines(gaz_dir):
        if len(c) >= 15:
            cities.append((int(c[14]) if c[14] else 0, c))
    cities.sort(key=lambda t: -t[0])

    for pop, c in cities:
        iso, a1, a2 = c[8], c[10], c[11]
        parent = ppath = None
        if a2:
            parent, ppath = a2_id.get(iso + "." + a1 + "." + a2), \
                            a2_path.get(iso + "." + a1 + "." + a2)
        if parent is None and a1:
            parent, ppath = a1_id.get(iso + "." + a1), a1_path.get(iso + "." + a1)
        if parent is None:
            parent, ppath = country_id.get(iso), country_path.get(iso)
        if parent is None:
            continue
        alts = tuple(x for x in c[3].split(",") if x) if c[3] else ()
        pid, _ = add(parent, "locality", c[1], ppath, float(c[4]), float(c[5]),
                     pop, int(c[0]) if c[0] else None, own=(c[2],), alts=alts)
        if pid:
            st.localities += 1

    st.names = len(names)

    con.execute("BEGIN")
    try:
        con.execute("UPDATE assets SET place_id=NULL, place_source=NULL "
                    "WHERE place_id IS NOT NULL")
        con.execute("DELETE FROM geo_cache")
        con.execute("DELETE FROM folder_places")
        con.execute("DELETE FROM place_names")
        con.execute("DELETE FROM places")
        con.executemany(
            "INSERT INTO places (id, parent_id, kind, name, path, lat, lon, "
            "population, rank_pop, geonameid) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        con.executemany(
            "INSERT INTO place_names (place_id, norm, own) VALUES (?,?,?)", names)
        # `rank_pop` is how two places of the same name are told apart, and the
        # gazetteer gives no population at all for a region: Florida the state
        # arrives as 0 and loses to Floridablanca, Colombia. So a region counts
        # what is inside it. This is a ranking weight and not a fact about the
        # place -- only `cities500` localities are summed, so it undercounts
        # everywhere by the same kind of margin.
        for kind in ("admin2", "admin1", "country"):
            con.execute(
                """UPDATE places SET rank_pop = MAX(
                     population,
                     COALESCE((SELECT SUM(c.rank_pop) FROM places c
                                WHERE c.parent_id = places.id), 0))
                   WHERE kind = ?""", (kind,))
        # A representative point for the levels that have none of their own:
        # the coordinates of the largest place inside them. Deepest first, so a
        # country inherits from its regions rather than from nothing.
        for kind in ("admin2", "admin1", "country"):
            con.execute(
                """UPDATE places SET (lat, lon) =
                     (SELECT c.lat, c.lon FROM places c
                       WHERE c.parent_id = places.id AND c.lat IS NOT NULL
                       ORDER BY c.population DESC LIMIT 1)
                   WHERE kind = ? AND lat IS NULL""", (kind,))
        con.execute("DELETE FROM gazetteer_state")
        con.execute(
            "INSERT INTO gazetteer_state (version, loaded_at, places, names, source) "
            "VALUES (?,?,?,?,?)",
            (GAZETTEER_VERSION, int(time.time()), st.places, st.names, str(gaz_dir)))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    if progress:
        progress(st)
    return st


# --------------------------------------------------------------------------
# source 1: coordinates
# --------------------------------------------------------------------------

@dataclass
class PlaceStats:
    with_gps: int = 0
    gps_placed: int = 0
    bins: int = 0
    bins_computed: int = 0
    gps_unplaced: int = 0
    labels: int = 0
    labels_matched: int = 0
    folder_placed: int = 0
    corrected: int = 0            # put back by hand after the machine's guess
    started: float = field(default_factory=time.time)

    @property
    def placed(self) -> int:
        return self.gps_placed + self.folder_placed

    @property
    def elapsed(self) -> float:
        return time.time() - self.started


def _bin(v: float) -> float:
    """Snap to the ~1 km grid.

    Rounded a second time to kill binary-float noise, so the same coordinate
    always produces a byte-identical cache key.
    """
    return round(round(v / BIN) * BIN, 6)


def _plausible(lat: float, lon: float) -> bool:
    # Null Island is not a place anyone photographed; it is what a receiver
    # writes when it has no fix.
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0)


def _ancestry(con) -> tuple[dict, dict]:
    parent, kind = {}, {}
    for r in con.execute("SELECT id, parent_id, kind FROM places"):
        parent[r[0]], kind[r[0]] = r[1], r[2]
    return parent, kind


def _coarsen(pid: int, km: float, parent: dict, kind: dict) -> int | None:
    """What a nearest-neighbour hit is entitled to claim at that distance.

    The gazetteer holds points, not borders, so distance is the only evidence
    there is about whether the nearest town is really where this was taken.
    """
    if km > GIVE_UP_KM:
        return None
    if km <= LOCALITY_KM:
        return pid
    want = ("admin2", "admin1") if km <= REGION_KM else ("country",)
    node = parent.get(pid)
    while node is not None:
        if kind[node] in want:
            return node
        node = parent.get(node)
    return None


def geocode(con, st: PlaceStats | None = None, *, progress=None) -> PlaceStats:
    """Bin every coordinate, look up the misses, write the places back."""
    import numpy as np
    from sklearn.neighbors import BallTree

    st = st or PlaceStats()
    rows = con.execute(
        "SELECT id, gps_lat, gps_lon FROM assets "
        "WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL").fetchall()
    wanted: dict[int, tuple] = {}
    for aid, lat, lon in rows:
        if _plausible(lat, lon):
            wanted[aid] = (_bin(lat), _bin(lon))
    st.with_gps = len(wanted)
    if not wanted:
        return st

    cache = {(r[0], r[1]): r[2] for r in
             con.execute("SELECT lat_bin, lon_bin, place_id FROM geo_cache")}
    bins = set(wanted.values())
    st.bins = len(bins)
    missing = sorted(bins - cache.keys())
    st.bins_computed = len(missing)

    if missing:
        prows = con.execute(
            "SELECT id, lat, lon FROM places "
            "WHERE kind = 'locality' AND lat IS NOT NULL").fetchall()
        if not prows:
            raise RuntimeError("no gazetteer loaded -- run `mediaidx places --load`")
        ids = np.fromiter((r[0] for r in prows), np.int64, len(prows))
        pts = np.radians(np.array([(r[1], r[2]) for r in prows], np.float64))
        # Haversine over a ball tree, queried once per distinct 1 km bin rather
        # than once per photograph: a week in one town costs a handful of
        # lookups however many pictures it produced.
        tree = BallTree(pts, metric="haversine")
        dist, idx = tree.query(np.radians(np.array(missing, np.float64)), k=1)
        parent, kind = _ancestry(con)
        fresh = []
        for (lat_bin, lon_bin), d, i in zip(missing, dist[:, 0], idx[:, 0]):
            place = _coarsen(int(ids[i]), float(d) * EARTH_KM, parent, kind)
            cache[(lat_bin, lon_bin)] = place
            fresh.append((lat_bin, lon_bin, place))
        con.executemany(
            "INSERT OR REPLACE INTO geo_cache (lat_bin, lon_bin, place_id) "
            "VALUES (?,?,?)", fresh)

    updates = []
    for aid, key in wanted.items():
        place = cache.get(key)
        if place is None:
            st.gps_unplaced += 1
        else:
            updates.append((place, aid))
    con.executemany(
        "UPDATE assets SET place_id = ?, place_source = 'gps' WHERE id = ?", updates)
    st.gps_placed = len(updates)
    if progress:
        progress(st)
    return st


# --------------------------------------------------------------------------
# source 2: folder labels
# --------------------------------------------------------------------------

def _parent_path(path: str) -> str | None:
    cut = max(path.rfind("\\"), path.rfind("/"))
    return path[:cut] if cut > 0 else None


def resolve_label(con, label: str, *, exonym_floor: int,
                  rejected: set, blocked: set) -> tuple[int | None, int]:
    """(place_id, candidates considered) for one folder label.

    Two rules do the work. A label must match a gazetteer name *in full* -- no
    substring, no fuzz -- so `Hennan rippijuhlat` matches nothing and is left
    alone. And a match on a place's own name is accepted at any size, while a
    match on an exonym has to clear a population floor: an exonym exists
    because people far away talk about the place, so `Lontoo` and `Hampuri`
    are real, and a village that happens to be spelled like a Finnish given
    name is not.
    """
    norm = norm_name(label)
    if not norm or norm in blocked:
        return None, 0
    cands = con.execute(
        """SELECT p.id, p.rank_pop, n.own FROM place_names n
             JOIN places p ON p.id = n.place_id
            WHERE n.norm = ?""", (norm,)).fetchall()
    eligible = [c for c in cands
                if (c[2] or c[1] >= exonym_floor) and (norm, c[0]) not in rejected]
    if not eligible:
        return None, 0
    best = max(eligible, key=lambda c: (c[1], -c[0]))
    return best[0], len(eligible)


def match_folders(con, cfg, st: PlaceStats | None = None, *,
                  now: int | None = None) -> PlaceStats:
    """Resolve folder labels, then hand each label down its own subtree.

    A photograph filed under `2001 01 Thaimaa\\skannatut` is in Thailand as
    much as one filed directly under the labelled folder, so the nearest
    labelled ancestor wins.
    """
    st = st or PlaceStats()
    now = now or int(time.time())
    floor = int(cfg.places.get("exonym_min_population", 50_000))

    # Exceptions are stored against a place's path, so they are translated to
    # the ids of whichever gazetteer is loaded right now. A path that no longer
    # exists simply stops matching anything, which is the correct outcome.
    rejected, blocked = set(), set()
    for label_norm, place_path in con.execute(
            "SELECT label_norm, place_path FROM place_exceptions"):
        if not place_path:
            blocked.add(label_norm)
            continue
        row = con.execute("SELECT id FROM places WHERE path = ?",
                          (place_path,)).fetchone()
        if row:
            rejected.add((label_norm, row[0]))

    folders = con.execute(
        "SELECT id, path, COALESCE(user_label, parsed_label) AS label "
        "FROM folders").fetchall()
    by_path = {r["path"]: r["id"] for r in folders}
    path_of = {r["id"]: r["path"] for r in folders}

    own: dict[int, int] = {}
    con.execute("DELETE FROM folder_places")
    for r in folders:
        if not r["label"]:
            continue
        st.labels += 1
        place, n = resolve_label(con, r["label"], exonym_floor=floor,
                                 rejected=rejected, blocked=blocked)
        if place is None:
            continue
        st.labels_matched += 1
        own[r["id"]] = place
        con.execute(
            "INSERT INTO folder_places (folder_id, place_id, label, candidates, "
            "matched_at) VALUES (?,?,?,?,?)", (r["id"], place, r["label"], n, now))

    # Inheritance is resolved once per folder, not once per photograph.
    inherited: dict[int, int | None] = {}

    def for_folder(fid: int, path: str) -> int | None:
        if fid in inherited:
            return inherited[fid]
        inherited[fid] = None           # cycles cannot happen, but recursion
        place = own.get(fid)            # over user-supplied paths shouldn't hang
        if place is None:
            parent = _parent_path(path)
            while parent is not None and parent not in by_path:
                parent = _parent_path(parent)
            if parent is not None:
                place = for_folder(by_path[parent], parent)
        inherited[fid] = place
        return place

    updates: dict[int, int] = {}
    for asset_id, folder_id in con.execute(
            """SELECT f.asset_id, f.folder_id FROM files f
                 JOIN assets a ON a.id = f.asset_id
                WHERE f.missing_since IS NULL AND a.place_id IS NULL
                ORDER BY f.id"""):
        if asset_id in updates or folder_id not in path_of:
            continue
        place = for_folder(folder_id, path_of[folder_id])
        if place is not None:
            updates[asset_id] = place
    con.executemany(
        "UPDATE assets SET place_id = ?, place_source = 'folder' WHERE id = ?",
        [(p, a) for a, p in updates.items()])
    st.folder_placed = len(updates)
    return st


def reject(con, label: str, place_id: int | None, *, now: int | None = None) -> None:
    """Record that a folder match was wrong.

    Permanent, survives a re-run, and survives a reloaded gazetteer -- which is
    why what gets written down is the place's path rather than its id.
    `place_id=None` rejects the label itself, whatever it might match.
    """
    path = ""
    if place_id is not None:
        row = con.execute("SELECT path FROM places WHERE id = ?",
                          (place_id,)).fetchone()
        if row is None:
            return
        path = row[0]
    con.execute(
        "INSERT OR IGNORE INTO place_exceptions (label_norm, place_path, created_at) "
        "VALUES (?,?,?)", (norm_name(label), path, now or int(time.time())))


def rematch_folders(con, cfg) -> PlaceStats:
    """Redo the folder source alone, after a match has been rejected.

    The coordinates are left where they are: nothing about a rejected folder
    label changes what a GPS receiver recorded, and re-running the geocoder
    would be ten seconds spent confirming it.
    """
    con.execute("UPDATE assets SET place_id = NULL, place_source = NULL "
                "WHERE place_source = 'folder'")
    return match_folders(con, cfg)


def run(con, cfg, *, progress=None) -> PlaceStats:
    """Both sources, in precedence order. Re-runnable at any time."""
    st = PlaceStats()
    con.execute("BEGIN")
    try:
        con.execute("UPDATE assets SET place_id = NULL, place_source = NULL")
        geocode(con, st, progress=progress)
        match_folders(con, cfg, st)
        # Inside the run, not in its callers: `mediaidx places` recomputes the
        # whole library outside `derive`, and a correction that depended on
        # which command was used would not be one (spec 4.7).
        from . import corrections as corrections_mod
        st.corrected = corrections_mod.apply_places(con)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    if progress:
        progress(st)
    return st


# --------------------------------------------------------------------------
# reading it back
# --------------------------------------------------------------------------

def subtree_ids(con, place_id: int) -> list[int]:
    """A place and everything beneath it, so `Finland` means the whole country."""
    rows = con.execute(
        """WITH RECURSIVE sub(id) AS (
             SELECT ? UNION ALL
             SELECT p.id FROM places p JOIN sub ON p.parent_id = sub.id)
           SELECT id FROM sub""", (place_id,)).fetchall()
    return [r[0] for r in rows]


def lookup(con, text: str, *, limit: int = 5) -> list:
    """Places answering to this text, most significant first."""
    return con.execute(
        """SELECT p.* FROM place_names n JOIN places p ON p.id = n.place_id
            WHERE n.norm = ? ORDER BY p.rank_pop DESC LIMIT ?""",
        (norm_name(text), limit)).fetchall()
