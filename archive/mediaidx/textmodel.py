"""The search model's text half, on its own (spec 7.6).

A described search -- `sunset`, `lapset rannalla` -- turns the words into a
vector and ranks the photographs' stored vectors against it. Only the model's
text half is needed for that; the image half is for indexing, which happens on
the computer that keeps the archive. Loaded whole on a CPU, the model holds 4.3
GB and peaks at 7.8 GB on the way in, more than a 4 GB NAS has.

So `build` exports the text half alone and stores its weights at half
precision, widened back as the model runs: the arithmetic stays 32-bit and only
what is kept in memory shrinks. Measured 2026-11-15 over twenty Finnish and
English queries ranked against the real 39,854 vectors, this held about 1.0 GB
and returned the same answers as the full model for every one of them.
Ordinary 8-bit quantization held half that, and was rejected because it did
not: `joulukuusi` kept 2 of its best 12, `linna` 1 of 12.

What runs it needs onnxruntime, `tokenizers` and numpy, and nothing else -- no
torch, no open_clip. `build` needs both, and is run where the archive is kept.
"""

from __future__ import annotations

import json
import os
import string
import time
from pathlib import Path

import numpy as np

MODEL_FILE = "text.onnx"
TOKENIZER_FILE = "tokenizer.json"
META_FILE = "model.json"

# A weight is stored at half precision when it has more elements than this.
# Every matrix of the model is far above it and every bias and norm far below.
BIG = 100_000

# How closely the built model must agree with the full one, as the lowest
# cosine over the probes below. Measured at 0.99999 or above for every probe;
# 8-bit quantization, which was rejected, reached 0.93.
MIN_AGREEMENT = 0.9999

# The probes `build` checks the result against: the queries the choice of
# form was measured with, Finnish and English, plain and compound.
PROBES = [
    "kids on a beach at sunset", "snow", "birthday cake", "a dog", "underwater",
    "auringonlasku", "rippijuhlat", "lapset rannalla", "joulukuusi", "sauna",
    "vene järvellä", "hääkuva", "a red car", "people skiing", "mökki",
    "grilli", "koira lumessa", "swimming pool", "linna", "ruoka lautasella",
]

_NO_PUNCTUATION = str.maketrans("", "", string.punctuation)


def canonical(text: str) -> str:
    """The text as the model was trained to read it.

    The same steps as open_clip's `canonicalize_text`, which the full model
    applies before tokenizing, so that this side does not have to import it:
    underscores become spaces, punctuation goes, case folds, spaces collapse.
    """
    text = text.replace("_", " ").translate(_NO_PUNCTUATION).lower()
    return " ".join(text.split()).strip()


def _session(path: Path, threads: int | None):
    """An onnxruntime session for a model whose weights are stored compactly.

    Three settings, each for a measured reason. Folding constants would widen
    every half-precision weight once at load and keep the wide copy, costing
    the memory the compact form exists to save. With the CPU arena on, the
    widened weights of one query are kept for the next. And at the default
    optimization level onnxruntime 1.29 crashed while creating the session for
    this graph; the basic level does not, and makes no difference to speed.
    """
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    so.enable_cpu_mem_arena = False
    so.inter_op_num_threads = 1
    so.intra_op_num_threads = int(threads or min(4, os.cpu_count() or 1))
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"],
                                disabled_optimizers=["ConstantFolding"])


class TextEncoder:
    """Turns described searches into vectors, with the exported text half.

    Answers `encode_text` exactly as `embed.Encoder` does, so the search does
    not need to know which of them it was given.
    """

    def __init__(self, folder, *, threads: int | None = None):
        from tokenizers import Tokenizer

        folder = Path(folder)
        meta = json.loads((folder / META_FILE).read_text(encoding="utf-8"))
        self.tag = meta["tag"]
        self.folder = folder
        self.tokenizer = Tokenizer.from_file(str(folder / TOKENIZER_FILE))
        width = int(meta["context_length"])
        self.tokenizer.enable_truncation(max_length=width)
        self.tokenizer.enable_padding(length=width, pad_id=int(meta["pad_id"]))
        self.session = _session(folder / MODEL_FILE, threads)

    def tokens(self, texts: list[str]) -> np.ndarray:
        got = self.tokenizer.encode_batch([canonical(t) for t in texts])
        return np.array([e.ids for e in got], dtype=np.int64)

    def encode_text(self, texts: list[str]) -> np.ndarray:
        vec = self.session.run(None, {"ids": self.tokens(texts)})[0]
        vec = vec.astype(np.float32)
        return vec / np.linalg.norm(vec, axis=-1, keepdims=True)


def compact(model) -> int:
    """Store a model's large weights at half precision, in place.

    Each float32 initializer over `BIG` elements is replaced by a float16 one
    under another name, and a Cast back to float32 is put where the original
    name was produced, so nothing that reads it changes. Returns how many were
    replaced.
    """
    from onnx import TensorProto, helper, numpy_helper

    g = model.graph
    kept, widen, n = [], [], 0
    for init in g.initializer:
        w = numpy_helper.to_array(init)
        if w.dtype != np.float32 or w.size <= BIG:
            kept.append(init)
            continue
        stored = init.name + "__half"
        kept.append(numpy_helper.from_array(w.astype(np.float16), stored))
        widen.append(helper.make_node("Cast", [stored], [init.name],
                                      to=TensorProto.FLOAT,
                                      name=init.name + "__widen"))
        n += 1
    del g.initializer[:]
    g.initializer.extend(kept)
    nodes = widen + list(g.node)
    del g.node[:]
    g.node.extend(nodes)
    return n


def build(out, *, model_name: str, pretrained: str, log=print):
    """Export the text half of the search model into `out`, and check it.

    Writes `text.onnx`, `tokenizer.json` and `model.json`. Everything is made in
    a folder beside `out` and moved in only once the check has passed, so a
    failed or interrupted build leaves the previous model where it was.
    Returns what `model.json` says, and the probes' vectors from the full
    model and from the built one, for the caller to rank against the index.
    """
    import shutil

    import onnx
    import torch

    from . import embed

    out = Path(out)
    work = out.with_name(out.name + ".building")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    t = time.time()
    enc = embed.Encoder(model_name=model_name, pretrained=pretrained, device="cpu")
    log(f"  loaded {enc.tag} in {time.time() - t:.0f}s")
    want_ids = enc.tokenizer(PROBES).numpy()
    want = enc.encode_text(PROBES)

    hf = enc.tokenizer.tokenizer
    hf.save_pretrained(str(work))
    meta = {"tag": enc.tag, "context_length": int(want_ids.shape[1]),
            "pad_id": int(hf.pad_token_id), "built": int(time.time()),
            "weights": "float16, widened to float32 as it runs"}

    class Text(torch.nn.Module):
        def __init__(self, model):
            super().__init__()
            self.model = model

        def forward(self, ids):
            return self.model.encode_text(ids, normalize=True)

    # The attention layers' native fast path cannot be traced into a graph.
    torch.backends.mha.set_fastpath_enabled(False)
    enc.model.visual = None                  # the image half is not wanted
    wide = work / "wide.onnx"
    t = time.time()
    with torch.no_grad():
        torch.onnx.export(Text(enc.model).eval(),
                          (torch.from_numpy(want_ids[:2]),), str(wide),
                          dynamo=False, opset_version=17,
                          input_names=["ids"], output_names=["vec"],
                          dynamic_axes={"ids": {0: "n"}, "vec": {0: "n"}})
    del enc
    model = onnx.load(str(wide))
    n = compact(model)
    onnx.save(model, str(work / MODEL_FILE))
    del model
    wide.unlink()
    (work / META_FILE).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log(f"  exported the text half in {time.time() - t:.0f}s, {n} weights at half "
        f"precision, {(work / MODEL_FILE).stat().st_size / 1e6:,.0f} MB")

    light = TextEncoder(work)
    if not (light.tokens(PROBES) == want_ids).all():
        raise RuntimeError("the tokenizer does not read the probes as the full "
                           f"model does; nothing was replaced, see {work}")
    got = light.encode_text(PROBES)
    agree = (got * want).sum(axis=1)
    worst = int(agree.argmin())
    meta["agreement"] = round(float(agree.min()), 6)
    if agree.min() < MIN_AGREEMENT:
        raise RuntimeError(f"{PROBES[worst]!r} agrees only to {agree.min():.5f} "
                           f"(needed {MIN_AGREEMENT}); nothing was replaced, "
                           f"see {work}")
    (work / META_FILE).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    del light

    out.mkdir(parents=True, exist_ok=True)
    for name in os.listdir(work):
        os.replace(work / name, out / name)
    shutil.rmtree(work, ignore_errors=True)
    return meta, want, got
