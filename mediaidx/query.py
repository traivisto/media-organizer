"""Turning a typed phrase into search criteria (spec §7.1).

The search box takes one string and the user is told nothing about how it works.
This module is where that string becomes a set of chips -- a person, a place, an
event, a date range -- plus whatever is left over, which is the description the
vector search actually ranks by.

Two rules do most of the work:

* **Longest span first.** "New York" is looked up before "New", and "Kesa 2019"
  before "2019", so the most specific reading of a phrase wins and the words it
  used are not available to anything else.
* **Named things before found things.** Within one span, a person or an event
  beats a place, because people and event names are what *this* user typed into
  *this* library, while place names come from a 285,144-row gazetteer that is
  exactly where the accidental matches come from -- `Kia`, `Loon` and `Vaja` are
  all on record as folder labels wrongly resolved to towns (§6.3).

Nothing here reads a photograph or runs a model. Resolution is table lookup, so
it costs a few hundred microseconds and gives the same answer every time; that
is the whole reason the box does not send the query to a language model first.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

from . import cluster
from . import places as places_mod

# How many words a single chip may span. Place names run to three ("Isle of
# Wight"); four is slack rather than a considered limit.
MAX_SPAN = 4

# The bound on a year in a *query*, which is not the bound on a date in a file
# (§5.1). A query for 1980 is a question about an archive that starts in 1998,
# and answering "no photographs" is better than treating it as a description.
MIN_YEAR = 1900
MAX_YEAR = 2100

PREFIXES = ("person", "place", "event", "date", "text")

# Words that join criteria together and describe nothing on their own. They are
# dropped from the remainder *only* when something else resolved, so "Tommi and
# Emmi" asks an exact question about two people rather than ranking the library
# against the word "and", while "a day at the beach" is left exactly as typed --
# there the small words are the description's own grammar and the encoder wants
# them. Finnish carries most of this in case endings rather than in words, so
# the Finnish half of the list is short by nature.
_JOINERS = {"and", "or", "with", "the", "a", "an", "of", "in", "at", "on",
            "from", "to", "for", "ja", "tai", "seka", "kanssa"}

_PUNCT = " \t\r\n,.;:!?()[]{}<>\"'’“”"


def norm(s: str) -> str:
    """The match key, shared with the gazetteer (places.norm_name).

    Diacritics are stripped, which conflates a and a-umlaut. Finnish makes them
    different letters, but a query typed without umlauts has to work, and the
    span has to match end to end anyway.
    """
    s = unicodedata.normalize("NFKD", s.strip().casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.split())


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

# Written without diacritics because they are matched against norm() output.
_MONTHS_FI = ["tammikuu", "helmikuu", "maaliskuu", "huhtikuu", "toukokuu",
              "kesakuu", "heinakuu", "elokuu", "syyskuu", "lokakuu",
              "marraskuu", "joulukuu"]
_MONTHS_EN = ["january", "february", "march", "april", "may", "june", "july",
              "august", "september", "october", "november", "december"]

# Finnish inflects the month onto the noun -- "kesakuussa", "heinakuun" -- so
# these are matched as stems rather than as whole words. The English three
# letter abbreviations are listed separately because "mar" is not a prefix
# anybody would want matching "march" by accident in a description.
_MONTH_ABBR_EN = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
                  "sep", "oct", "nov", "dec"]

# First and last month, inclusive. Winter runs into the following year, which
# the offset in the third position records.
_SEASONS = {
    "kevat": (3, 5, 0), "spring": (3, 5, 0),
    "kesa": (6, 8, 0), "summer": (6, 8, 0),
    "syksy": (9, 11, 0), "autumn": (9, 11, 0), "fall": (9, 11, 0),
    "talvi": (12, 2, 1), "winter": (12, 2, 1),
}


def _month_of(word: str) -> int | None:
    """1-12 for a month word, allowing Finnish inflection. Longest first."""
    for table in (_MONTHS_FI, _MONTHS_EN):
        for i, name in enumerate(table):
            if word.startswith(name):
                return i + 1
    for i, abbr in enumerate(_MONTH_ABBR_EN):
        if word == abbr:
            return i + 1
    return None


def _season_of(word: str) -> tuple[int, int, int] | None:
    """Months for a season word. Tried after months: kesakuu is not kesa."""
    for stem, months in _SEASONS.items():
        if word.startswith(stem):
            return months
    return None


def _span_epoch(y1: int, m1: int, y2: int, m2: int) -> tuple[int, int]:
    """Inclusive month range as [start, end) in epoch seconds, local time."""
    start = datetime(y1, m1, 1)
    end = (datetime(y2 + 1, 1, 1) if m2 == 12
           else datetime(y2, m2 + 1, 1))
    return int(start.timestamp()), int(end.timestamp())


def _year_ok(y: int) -> bool:
    return MIN_YEAR <= y <= MAX_YEAR


def parse_date(span: str) -> tuple[int, int, str] | None:
    """(start, end, label) for a date expression, or None.

    Understands a bare year, ISO year-month and year-month-day, month/year in
    either order with either separator, a year range, and a month or season
    named in Finnish or English beside a year.
    """
    s = norm(span)
    if not s:
        return None

    # 2019-2021, before 2019-06: a four digit second half is a year, not a month
    m = re.fullmatch(r"(\d{4})\s*[-–]\s*(\d{4})", s)
    if m:
        y1, y2 = int(m[1]), int(m[2])
        if _year_ok(y1) and _year_ok(y2) and y1 <= y2:
            a, b = _span_epoch(y1, 1, y2, 12)
            return a, b, f"{y1}–{y2}"

    m = re.fullmatch(r"(\d{4})", s)
    if m and _year_ok(int(m[1])):
        y = int(m[1])
        a, b = _span_epoch(y, 1, y, 12)
        return a, b, str(y)

    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})", s)
    if m and _year_ok(int(m[1])) and 1 <= int(m[2]) <= 12:
        y, mo = int(m[1]), int(m[2])
        a, b = _span_epoch(y, mo, y, mo)
        return a, b, f"{y}-{mo:02d}"

    m = re.fullmatch(r"(\d{1,2})[/.](\d{4})", s)
    if m and _year_ok(int(m[2])) and 1 <= int(m[1]) <= 12:
        mo, y = int(m[1]), int(m[2])
        a, b = _span_epoch(y, mo, y, mo)
        return a, b, f"{y}-{mo:02d}"

    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m and _year_ok(int(m[1])) and 1 <= int(m[2]) <= 12:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        try:
            start = datetime(y, mo, d)
        except ValueError:
            return None
        return (int(start.timestamp()),
                int(start.timestamp()) + 86400,
                f"{y}-{mo:02d}-{d:02d}")

    # A word beside a year, in either order: "kesa 2019", "2019 heinakuu"
    parts = s.split()
    if len(parts) == 2:
        for word, other in ((parts[0], parts[1]), (parts[1], parts[0])):
            if not re.fullmatch(r"\d{4}", other) or not _year_ok(int(other)):
                continue
            y = int(other)
            mo = _month_of(word)
            if mo:
                a, b = _span_epoch(y, mo, y, mo)
                return a, b, f"{word} {y}"
            season = _season_of(word)
            if season:
                m1, m2, carry = season
                a, b = _span_epoch(y, m1, y + carry, m2)
                return a, b, f"{word} {y}"
    return None


# --------------------------------------------------------------------------
# Terms
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Term:
    """One chip: what it is, what it says, and the words it consumed."""
    kind: str                       # person | place | event | date
    label: str
    span: str                       # as typed, so the chip can be removed again
    ref_id: int | None = None       # persons.id, places.id, events.id
    start: int | None = None        # date only, epoch seconds
    end: int | None = None
    held: int | None = None         # place only: photographs beneath it
    also: tuple = field(default=(), compare=False)   # other readings of the span

    @property
    def sure(self) -> bool:
        """Whether this reading is applied, or only offered (§7.1)."""
        if self.kind != "place" or self.held is None:
            return True
        return self.held >= PLACE_SURE_ASSETS


@dataclass
class Resolved:
    terms: list
    text: str                       # the remainder, for the encoder
    raw: str = ""
    offers: list = field(default_factory=list)   # readings not taken; see Term.sure

    @property
    def people(self):
        return [t for t in self.terms if t.kind == "person"]

    @property
    def places(self):
        return [t for t in self.terms if t.kind == "place"]

    @property
    def events(self):
        return [t for t in self.terms if t.kind == "event"]

    @property
    def dates(self):
        return [t for t in self.terms if t.kind == "date"]

    def without(self, term) -> str:
        """The query with one chip's words removed -- what its x links to.

        Removing a chip is a plain link back to the same search, so the whole
        interface works with no JavaScript at all.
        """
        out, dropped = [], False
        want = norm(term.span).split()
        toks = _tokens(self.raw)
        i = 0
        while i < len(toks):
            if (not dropped
                    and [norm(t) for t in toks[i:i + len(want)]] == want):
                i += len(want)
                dropped = True
                continue
            out.append(toks[i])
            i += 1
        return " ".join(out)


def _tokens(q: str) -> list:
    return [t for t in (w.strip(_PUNCT) for w in q.split()) if t]


def _named_events(con) -> dict:
    """Named events by match key. Unnamed ones cannot be asked for by name."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='events'").fetchone():
        return {}
    out = {}
    for r in con.execute(
            "SELECT id, name, start_at, asset_count FROM events "
            "WHERE name IS NOT NULL AND name != ''"):
        out.setdefault(norm(r["name"]), r)
    return out


# A name-word shorter than this is not a way of referring to anybody. Without
# it "Reetta P" would put `p` in the index and make a stray letter a person.
MIN_NAME_WORD = 3


def _people_index(con) -> tuple[dict, dict]:
    """Named people by whole name, and by any name-word that means only them.

    Nobody types "Anne Raivisto" to look for Anne. Requiring the whole name is
    what sends `totte anne` off to the encoder as a description, so a single
    word of a name resolves too -- but only where it belongs to exactly one
    person, because a chip that silently picks between two people is worse than
    no chip at all. Measured on this library, 2026-09-09: of 79 people, one
    name-word is shared (`reetta`, between Reetta and Reetta P), and that one
    is a whole name as well, so the exact match settles it first.

    Read from `persons` rather than through `cluster.named_people`, which joins
    faces to count them: whether a name is *understood* is a different question
    from how many photographs it will find.
    """
    rows = list(con.execute("SELECT id, name FROM persons"))
    by_name: dict = {}
    for r in rows:
        by_name.setdefault(norm(r["name"]), r)

    holders: dict = {}
    for r in rows:
        for w in set(norm(r["name"]).replace("-", " ").split()):
            if len(w) >= MIN_NAME_WORD:
                holders.setdefault(w, []).append(r)
    by_word = {w: rs[0] for w, rs in holders.items()
               if len(rs) == 1 and w not in by_name}
    return by_name, by_word


# How many photographs a place must hold, itself or beneath it, before its name
# is read as a place rather than as a word.
#
# The gazetteer has 285,144 entries and a great many of them are ordinary words:
# `Sunset`, `Beach` and `On` are all real towns carrying their own primary
# names, so "kids on a beach at sunset" collides with it three times over.
# Nothing about the *name* distinguishes those from `Alicante` -- only this
# library does, and the separation it gives is wide. Measured 2026-09-09:
# Alicante 142 photographs, Helsinki 164, Tenerife 162; `Beach` and `On` nil.
#
# One case sits in between and cannot be settled by any rule over the name.
# `Sunset` resolves to Shandon, California, where this archive genuinely holds
# five photographs -- so the reading is not wrong, merely unlikely, and a person
# typing "sunset" almost always means the light. Rather than choose silently,
# anything in the middle band is *offered* beside the results instead of being
# applied, and the words stay in the description until the offer is accepted.
PLACE_MIN_ASSETS = 5        # below this, not even worth mentioning
PLACE_SURE_ASSETS = 25      # at or above this, applied as a chip without asking


def _place_term(con, span: str, counts) -> Term | None:
    """The place answering to this span that this library knows best.

    Candidates are ranked by how many photographs sit beneath them, not by the
    gazetteer's own significance. Ranking the other way round and then keeping
    the first candidate with any photographs at all is actively worse than not
    filtering: for "sunset" it walks past the well-known Sunsets and lands on
    the one obscure town this archive happens to hold a single picture of.
    """
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='places'").fetchone():
        return None
    rows = con.execute(
        """SELECT p.id, p.path, p.rank_pop, n.own FROM place_names n
             JOIN places p ON p.id = n.place_id
            WHERE n.norm = ? ORDER BY p.rank_pop DESC LIMIT 12""",
        (norm(span),)).fetchall()
    if not rows:
        return None

    if counts is None:                      # no library to weigh it against
        best, held = rows[0], None
    else:
        scored = [(counts.get(r["id"], 0), r["own"], r["rank_pop"], r)
                  for r in rows]
        scored = [s for s in scored if s[0] >= PLACE_MIN_ASSETS]
        if not scored:
            return None
        top = max(scored, key=lambda s: (s[0], s[1], s[2]))
        best, held = top[3], top[0]

    where = " · ".join(reversed(best["path"].split("/")))
    term = Term(kind="place", label=where, span=span, ref_id=best["id"],
                held=held)
    return term


def _resolve_span(con, span, people, first_names, events, counts, force=None):
    """Every reading of one span, best first. Precedence is the order here."""
    found = []
    key = norm(span)

    if force in (None, "person"):
        # The whole name first, so `reetta` is Reetta rather than Reetta P.
        r = people.get(key) or first_names.get(key)
        if r is not None:
            found.append(Term("person", r["name"], span, ref_id=r["id"]))
    if force in (None, "event") and key in events:
        r = events[key]
        found.append(Term("event", r["name"], span, ref_id=r["id"]))
    if force in (None, "place"):
        t = _place_term(con, span, counts)
        if t:
            found.append(t)
    if force in (None, "date"):
        d = parse_date(span)
        if d:
            found.append(Term("date", d[2], span, start=d[0], end=d[1]))
    return found


def resolve(con, q: str, *, place_counts=None) -> Resolved:
    """Read a typed phrase as criteria plus a description.

    `place_counts` maps place id to the number of photographs in its subtree;
    passing it keeps places this library has never been to out of the chips.
    """
    raw = (q or "").strip()
    toks = _tokens(raw)
    if not toks:
        return Resolved(terms=[], text="", raw=raw)

    people, first_names = _people_index(con)
    events = _named_events(con)

    taken = [False] * len(toks)
    forced = [None] * len(toks)
    quoted = [False] * len(toks)
    terms, offers = [], []

    # Explicit prefixes bind one token and are never re-read as anything else,
    # which is the whole point of typing one (§7.1).
    for i, t in enumerate(toks):
        head, sep, rest = t.partition(":")
        if sep and head.casefold() in PREFIXES and rest:
            toks[i] = rest
            if head.casefold() == "text":
                quoted[i] = True
            else:
                forced[i] = head.casefold()

    for n in range(min(MAX_SPAN, len(toks)), 0, -1):
        for i in range(0, len(toks) - n + 1):
            window = range(i, i + n)
            if any(taken[j] or quoted[j] for j in window):
                continue
            force = {forced[j] for j in window if forced[j]}
            if len(force) > 1:            # two different prefixes in one span
                continue
            span = " ".join(toks[i:i + n])
            found = _resolve_span(con, span, people, first_names, events,
                                  place_counts,
                                  force=next(iter(force), None))
            if not found:
                continue
            best = found[0]
            if len(found) > 1:
                best = Term(best.kind, best.label, best.span, best.ref_id,
                            best.start, best.end, best.held, tuple(found[1:]))
            # An unsure reading is shown beside the results rather than acted
            # on, and its words stay in the description meanwhile (§7.1). An
            # explicit prefix is never second-guessed: the user has said which
            # reading they want.
            if not best.sure and not force:
                offers.append(best)
                continue
            terms.append(best)
            for j in window:
                taken[j] = True

    rest = [t for j, t in enumerate(toks) if not taken[j]]
    if terms:
        stripped = [t for t in rest if norm(t) not in _JOINERS]
        rest = stripped
    text = " ".join(rest)
    # Chips come out in the order they were typed, not the order they resolved.
    order = {}
    for t in terms:
        order[t] = raw.casefold().find(norm(t.span).split()[0])
    terms.sort(key=lambda t: order.get(t, 0))
    return Resolved(terms=terms, text=text, raw=raw, offers=offers)
