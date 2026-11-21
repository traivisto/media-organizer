"""Configuration loading.

Note what is absent by design (spec P7): no library path, no host, no share, no
person-to-folder mapping. A fresh install indexes nothing until told what to
index.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "mediaidx"

DEFAULTS: dict = {
    "paths": {},
    "scan": {
        "extensions": [".jpg", ".jpeg", ".png", ".heic", ".heif"],
        "exclude_dirs": [
            "_gsdata_", "@eaDir", "#recycle", "$RECYCLE.BIN",
            "System Volume Information", ".git",
        ],
        "exclude_files": ["Thumbs.db", ".DS_Store", "desktop.ini"],
        # Measured: the read ceiling is reached at 4 threads and never moves
        # again (spec §1.2). Eight is a hedge for a faster link, not a lever.
        "reader_threads": 8,
        "chunk_size": 256,
        "folder_patterns": [
            r"^(?P<year>\d{4})(?:\s+(?P<month>\d{2}))?(?:\s+(?P<label>.+))?$",
        ],
        # Conventions belonging to particular *applications* -- what a phone or a
        # messaging app names its files -- are reasonable defaults, because they
        # are the same on everyone's machine.
        #
        # Conventions belonging to a particular *library* are not, and do not
        # appear here (P7). The reference library renames everything to
        # `YYYYMMDD@HHMMSS-Make-Model-NN`, which no other archive would use, so
        # that pattern lives in that user's config.toml and nowhere else.
        "filename_patterns": [
            {"re": r"^IMG-(?P<ymd>\d{8})-WA\d+", "hint": "whatsapp"},
            {"re": r"^(?:IMG|VID)_(?P<ymd>\d{8})_(?P<hms>\d{6})", "hint": "camera"},
            {"re": r"^PXL_(?P<ymd>\d{8})_(?P<hms>\d{6})", "hint": "camera"},
            {"re": r"^Screenshot[ _-](?P<date>\d{4}-\d{2}-\d{2})", "hint": "screenshot"},
            {"re": r"^photo_(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{2}-\d{2}-\d{2})",
             "hint": "telegram"},
            {"re": r"^(?P<ymd>\d{8})_(?P<hms>\d{6})", "hint": "camera"},
        ],
    },
    "models": {
        "face": "buffalo_l",
    },
    "faces": {
        # Both filters exist to keep unnameable clusters out of the review
        # queue rather than to save time (spec §5.2). Measured on a random
        # sample of the reference library, they discard 46% of raw detections.
        "min_det_score": 0.5,
        "min_face_px": 40,
        "det_size": 640,
        "face_thumbnails": True,
        "cluster_min_size": 4,
        # Two bounds, both in force (spec 6.1): near enough is enough on its
        # own, and a little further is allowed when the five nearest named
        # faces agree. Both are read from here by cluster.assign, so both
        # belong here -- a tunable that appears only as a module constant is a
        # tunable nobody finds.
        "assign_threshold": 0.42,
        "assign_agreed": 0.50,
    },
    "events": {
        # A gap longer than this ends an event; so does a jump further than
        # `place_gap_km`, where both photographs carry a coordinate (spec 6.2).
        "time_gap_hours": 4,
        "place_gap_km": 25,
        # Below this many photographs a run is not an event at all. A folder
        # somebody labelled is exempt: the floor exists to keep specks out of
        # the review queue, and a named folder is never asked about.
        "min_event_assets": 5,
        # ...and these are the floors for being *asked* about, which is a
        # different question. Measured on the reference library, five would put
        # 1,209 events and 576 clusters in the queue; twenty and ten put 158
        # and 156 there. See queue.py for why the two floors are separate.
        "queue_min_assets": 20,
        "queue_min_faces": 10,
    },
    "places": {
        # A folder label matching a place's *own* name is accepted whatever its
        # size. One matching only an exonym has to clear this, because an
        # exonym is evidence that people far away talk about the place --
        # without the floor, `Nina` resolves to Neenah, Wisconsin (spec 6.3).
        "exonym_min_population": 50_000,
    },
    "tools": {
        "exiftool": "",  # empty means: find it on PATH
    },
    "datestamp": {
        # Below this, the located box is too uncertain a crop to bother
        # reading at all (spec: measured against the model's own validation
        # set and this library's scans alike).
        "conf_threshold": 0.6,
        # Both run locally through Ollama; a date is kept only when they
        # agree letter for letter (datestamp.py). Add a third to make
        # agreement harder to reach by accident, never to replace either --
        # two independent readings is the whole safety margin.
        "ollama_models": ["gemma4:12b", "qwen3.5:9b"],
    },
}


def _local_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / APP_NAME


@dataclass
class Config:
    database: Path
    thumbnails: Path
    logs: Path
    names_export: Path | None
    # Further copies of that same file, written by every `export`. A backup on
    # storage with its own schedule is worth having, and one that has to be
    # refreshed by hand is one you find stale on the day it matters.
    names_export_extra: list[Path] = field(default_factory=list)
    # Where a stored path prefix is on this machine, for a viewing copy that
    # reaches the archive differently from the computer that indexed it
    # (spec 8.3). Empty on that computer.
    mounts: dict = field(default_factory=dict)
    # The search model's text half, exported by `mediaidx textmodel`, for a
    # machine without room for all of it (spec 7.6). None: the whole model.
    text_model: Path | None = None
    # The Container Manager project folder `mediaidx publish` fills (spec 8.4).
    publish_to: Path | None = None
    # Every search `serve` answers, one json line each; None writes nothing.
    # Loaded configurations default to searches.jsonl in `logs`.
    search_log: Path | None = None
    scan: dict = field(default_factory=dict)
    models: dict = field(default_factory=dict)
    faces: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)
    places: dict = field(default_factory=dict)
    tools: dict = field(default_factory=dict)
    datestamp: dict = field(default_factory=dict)
    ui: dict = field(default_factory=dict)
    # Defaulted so that constructing a Config by hand -- which the tests do --
    # does not have to know about a gazetteer to talk about faces.
    gazetteer: Path = field(default_factory=lambda: _local_data_dir() / "gazetteer")
    source: Path | None = None

    @property
    def extensions(self) -> set[str]:
        return {e.lower() for e in self.scan["extensions"]}

    @property
    def exclude_dirs(self) -> set[str]:
        return {d.lower() for d in self.scan["exclude_dirs"]}

    @property
    def exclude_files(self) -> set[str]:
        return {f.lower() for f in self.scan["exclude_files"]}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def default_config_path() -> Path:
    env = os.environ.get("MEDIAIDX_CONFIG")
    if env:
        return Path(env)
    return _local_data_dir() / "config.toml"


def load(path: str | os.PathLike | None = None) -> Config:
    cfg_path = Path(path) if path else default_config_path()
    raw: dict = {}
    if cfg_path.exists():
        # Windows PowerShell 5.1 writes UTF-8 *with* a BOM under `-Encoding utf8`,
        # and tomllib rejects it as "Invalid statement (at line 1, column 1)".
        # Any config edited on Windows can carry one, so strip it rather than
        # making every author think about it.
        data = cfg_path.read_bytes()
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        try:
            raw = tomllib.loads(data.decode("utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise SystemExit(f"{cfg_path} is not valid TOML:\n  {e}") from None

    merged = _deep_merge(DEFAULTS, raw)

    # Patterns specific to one library are supplied by its owner and tried before
    # the shipped ones, so a user never has to restate the universal defaults in
    # order to add their own (P7).
    extra = merged["scan"].pop("filename_patterns_extra", None) or []
    if extra:
        merged["scan"]["filename_patterns"] = extra + merged["scan"]["filename_patterns"]
    extra_folders = merged["scan"].pop("folder_patterns_extra", None) or []
    if extra_folders:
        merged["scan"]["folder_patterns"] = extra_folders + merged["scan"]["folder_patterns"]

    p = merged.get("paths", {})
    data = _local_data_dir()
    logs = Path(p.get("logs", data / "logs"))

    return Config(
        database=Path(p.get("database", data / "photos.db")),
        thumbnails=Path(p.get("thumbnails", data / "thumbs")),
        logs=logs,
        search_log=Path(p["search_log"]) if p.get("search_log") else logs / "searches.jsonl",
        gazetteer=Path(p.get("gazetteer", data / "gazetteer")),
        names_export=Path(p["names_export"]) if p.get("names_export") else None,
        names_export_extra=[Path(x) for x in p.get("names_export_extra", [])],
        mounts={str(k): str(v) for k, v in (p.get("mounts") or {}).items()},
        text_model=Path(p["text_model"]) if p.get("text_model") else None,
        publish_to=Path(p["publish_to"]) if p.get("publish_to") else None,
        ui=merged.get("ui", {}),
        scan=merged["scan"],
        models=merged["models"],
        faces=merged["faces"],
        events=merged["events"],
        places=merged["places"],
        tools=merged["tools"],
        datestamp=merged["datestamp"],
        source=cfg_path if cfg_path.exists() else None,
    )
