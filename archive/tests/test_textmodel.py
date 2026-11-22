"""Described search with the text half of the model alone (spec 7.6).

The real model is 3.5 GB and is exercised by `mediaidx textmodel`, which checks
what it builds against the full model before keeping it. These tests are about
the parts that do not need it: that the text is read the way the full model
reads it, that compacting a model keeps its answer, that the light encoder runs
a folder built that way, and that the search uses it -- and lets it go.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mediaidx import db, embed, serve, textmodel
from mediaidx.config import Config

onnx = pytest.importorskip("onnx")
from onnx import TensorProto, helper, numpy_helper  # noqa: E402


# --------------------------------------------------------------------------
# Reading the text
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "kids on a beach at sunset", "Lapset RANNALLA!", "vene_järvellä",
    "  koira,  lumessa. ", "rock'n'roll (live)", "Äiti & Öljy-maalaus",
])
def test_the_text_is_read_as_the_full_model_reads_it(text):
    tok = pytest.importorskip("open_clip.tokenizer")
    assert textmodel.canonical(text) == tok.canonicalize_text(text)


# --------------------------------------------------------------------------
# A small model of the same shape: ids -> table -> mean -> matrix -> vector
# --------------------------------------------------------------------------

VOCAB, WIDTH, OUT, CONTEXT = 500, 300, 400, 8


def _tiny(path):
    rng = np.random.default_rng(3)
    table = rng.normal(size=(VOCAB, WIDTH)).astype(np.float32)     # 150k: big
    proj = rng.normal(size=(WIDTH, OUT)).astype(np.float32)        # 120k: big
    bias = rng.normal(size=(OUT,)).astype(np.float32)              # small
    g = helper.make_graph(
        [helper.make_node("Gather", ["table", "ids"], ["emb"]),
         helper.make_node("ReduceMean", ["emb"], ["pooled"], axes=[1], keepdims=0),
         helper.make_node("MatMul", ["pooled", "proj"], ["raw"]),
         helper.make_node("Add", ["raw", "bias"], ["vec"])],
        "tiny",
        [helper.make_tensor_value_info("ids", TensorProto.INT64, ["n", CONTEXT])],
        [helper.make_tensor_value_info("vec", TensorProto.FLOAT, ["n", OUT])],
        [numpy_helper.from_array(table, "table"),
         numpy_helper.from_array(proj, "proj"),
         numpy_helper.from_array(bias, "bias")])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 8
    onnx.save(m, str(path))
    return m


def _run(path, ids):
    return textmodel._session(path, 1).run(None, {"ids": ids})[0]


def test_compacting_halves_the_large_weights_and_keeps_the_answer(tmp_path):
    wide, half = tmp_path / "wide.onnx", tmp_path / "half.onnx"
    m = _tiny(wide)
    assert textmodel.compact(m) == 2
    onnx.save(m, str(half))
    kinds = {i.name: i.data_type for i in m.graph.initializer}
    assert kinds["table__half"] == kinds["proj__half"] == TensorProto.FLOAT16
    assert kinds["bias"] == TensorProto.FLOAT, "a small weight is left as it was"
    assert half.stat().st_size < 0.55 * wide.stat().st_size

    ids = np.random.default_rng(4).integers(0, VOCAB, size=(5, CONTEXT))
    a, b = _run(wide, ids), _run(half, ids)
    cos = (a * b).sum(1) / np.linalg.norm(a, axis=1) / np.linalg.norm(b, axis=1)
    assert cos.min() > 0.9999


def _folder(tmp_path, tag="m/t"):
    """A built folder: compacted model, tokenizer, and what it was built from."""
    from tokenizers import Tokenizer, models, pre_tokenizers

    folder = tmp_path / "textmodel"
    folder.mkdir()
    m = _tiny(tmp_path / "wide.onnx")
    textmodel.compact(m)
    onnx.save(m, str(folder / textmodel.MODEL_FILE))
    words = ["<pad>", "<unk>", "a", "dog", "in", "the", "snow", "lapset",
             "rannalla", "sunset"]
    tk = Tokenizer(models.WordLevel({w: i for i, w in enumerate(words)},
                                    unk_token="<unk>"))
    tk.pre_tokenizer = pre_tokenizers.Whitespace()
    tk.save(str(folder / textmodel.TOKENIZER_FILE))
    (folder / textmodel.META_FILE).write_text(json.dumps(
        {"tag": tag, "context_length": CONTEXT, "pad_id": 0}), encoding="utf-8")
    return folder


def test_the_light_encoder_runs_a_built_folder(tmp_path):
    enc = textmodel.TextEncoder(_folder(tmp_path), threads=1)
    assert enc.tag == "m/t"
    ids = enc.tokens(["A dog, in the SNOW!", "a dog in the snow",
                      "a dog in the snow at sunset with lapset rannalla"])
    assert ids.shape == (3, CONTEXT)
    assert (ids[0] == ids[1]).all(), "read as the full model reads it"
    assert ids[0][-1] == 0, "padded to the model's width"
    v = enc.encode_text(["A dog, in the SNOW!", "a dog in the snow", "sunset"])
    assert v.shape == (3, OUT) and v.dtype == np.float32
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0)
    assert np.allclose(v[0], v[1]) and not np.allclose(v[0], v[2])


# --------------------------------------------------------------------------
# Through the search
# --------------------------------------------------------------------------

TAG = "ViT-SO400M-14-SigLIP-384/webli"


@pytest.fixture
def searched(tmp_path, monkeypatch):
    """Twenty photographs with vectors; the light encoder replaced by one that
    counts how often it is made, and the full model by one that must not be."""
    rng = np.random.default_rng(0)
    probe = rng.normal(size=embed.DIM)
    probe /= np.linalg.norm(probe)
    dbp = tmp_path / "photos.db"
    con = db.connect(dbp)
    db.migrate(con)
    for i in range(20):
        v = probe + rng.normal(scale=0.02 if i < 5 else 1.0, size=embed.DIM)
        aid = db.insert_asset(con, f"{i:064d}", {"format": "jpeg",
                                                 "taken_at": 1_600_000_000 + i})
        embed.store(con, aid, TAG, (v / np.linalg.norm(v)).astype(np.float32),
                    vec_ok=False)
    con.commit()
    con.close()

    made = []

    class Light:
        tag = TAG

        def __init__(self, folder, *, threads=None):
            made.append(str(folder))

        def encode_text(self, texts):
            return np.array([probe] * len(texts), dtype=np.float32)

    def no_full_model(**kw):
        raise AssertionError("the full model was loaded")

    monkeypatch.setattr(serve.textmodel, "TextEncoder", Light)
    monkeypatch.setattr(serve.embed, "Encoder", no_full_model)

    def client(**ui):
        cfg = Config(database=dbp, thumbnails=tmp_path / "t", logs=tmp_path,
                     names_export=None, text_model=tmp_path / "textmodel")
        cfg.ui.update(read_only=True, **ui)
        return TestClient(serve.create_app(cfg))

    return client, made, Light


def _found(body):
    return body.count('href="/photo/')


def test_a_described_search_uses_the_text_model(searched):
    client, made, _ = searched
    c = client()
    body = c.get("/", params={"q": "text:sunset"}).text
    assert _found(body) >= 5
    c.get("/", params={"q": "text:snow"})
    assert len(made) == 1, "loaded once, then kept while in use"


def test_a_search_with_no_description_does_not_load_it(searched):
    client, made, _ = searched
    client().get("/", params={"q": "2020"})
    assert made == []


def test_it_is_let_go_after_a_quiet_spell(searched):
    client, made, _ = searched
    c = client(text_model_idle_minutes=0.003)            # about 0.2 s
    c.get("/", params={"q": "text:sunset"})
    time.sleep(0.6)
    c.get("/", params={"q": "text:sunset"})
    assert len(made) == 2


def test_it_can_be_kept(searched):
    client, made, _ = searched
    c = client(text_model_idle_minutes=0)
    c.get("/", params={"q": "text:sunset"})
    time.sleep(0.3)
    c.get("/", params={"q": "text:sunset"})
    assert len(made) == 1


def test_a_model_that_does_not_match_the_index_stops_the_search(searched):
    """The wrong model would still rank photographs: the wrong ones."""
    client, _, Light = searched
    Light.tag = "ViT-B-16-SigLIP/webli"
    r = client().get("/", params={"q": "text:sunset"})
    assert r.status_code == 503
    assert "ViT-B-16-SigLIP/webli" in r.text and TAG in r.text
    assert "mediaidx textmodel" in r.text
    assert 'href="/photo/' not in r.text
