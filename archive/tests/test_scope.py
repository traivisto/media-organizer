"""Scope containment and read-only guarantees (spec P1, P7)."""

import hashlib
import os
from pathlib import Path

import pytest

from mediaidx import config, walker

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


@pytest.fixture
def cfg():
    return config.load(os.devnull)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "target" / "sub").mkdir(parents=True)
    (tmp_path / "sibling").mkdir()
    (tmp_path / "target" / "_gsdata_").mkdir()

    (tmp_path / "target" / "a.jpg").write_bytes(JPEG)
    (tmp_path / "target" / "sub" / "b.JPG").write_bytes(JPEG)
    (tmp_path / "target" / "hyvää-yötä.jpg").write_bytes(JPEG)
    (tmp_path / "target" / "Thumbs.db").write_bytes(b"junk")
    (tmp_path / "target" / "notes.txt").write_bytes(b"junk")
    (tmp_path / "target" / "_gsdata_" / "c.jpg").write_bytes(JPEG)
    (tmp_path / "sibling" / "outside.jpg").write_bytes(JPEG)
    return tmp_path


def names(root, cfg, **kw):
    return sorted(c.name for c in walker.walk(str(root), cfg, **kw))


def test_indexes_only_the_given_subtree(tree, cfg):
    assert names(tree / "target", cfg) == ["a.jpg", "b.JPG", "hyvää-yötä.jpg"]


def test_excluded_directory_is_not_entered(tree, cfg):
    assert "c.jpg" not in names(tree / "target", cfg)


def test_junk_files_and_wrong_extensions_are_skipped(tree, cfg):
    found = names(tree / "target", cfg)
    assert "Thumbs.db" not in found and "notes.txt" not in found


def test_no_recurse_stays_in_the_named_folder(tree, cfg):
    assert names(tree / "target", cfg, recurse=False) == ["a.jpg", "hyvää-yötä.jpg"]


def test_non_ascii_filenames_survive(tree, cfg):
    assert "hyvää-yötä.jpg" in names(tree / "target", cfg)


def _fingerprint(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p)] = hashlib.sha256(
                p.read_bytes() + str(st.st_mtime_ns).encode()).hexdigest()
    return out


def test_walking_does_not_modify_the_tree(tree, cfg):
    """P1: the archive is read-only, and a scan must leave it byte-identical."""
    before = _fingerprint(tree)
    list(walker.walk(str(tree / "target"), cfg))
    assert _fingerprint(tree) == before
