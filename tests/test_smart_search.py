"""Plain-English search and captions, with stand-in models (the real ones are checked in test_ai_models.py)."""

import numpy as np
import pytest

from conftest import make_jpeg
from opkit import record
from shoebox.analyze.ai_run import analyze_ai
from shoebox.analyze.smart import names_in, smart_search
from shoebox.analyze.vectors import Index, open_vectors
from shoebox.catalog import open_catalog
from shoebox.catalog.ops import Emitter
from shoebox.catalog.query import search
from shoebox.catalog.store import upsert_record

rng = np.random.default_rng(5)
CONCEPTS = {w: (lambda v: v / np.linalg.norm(v))(rng.normal(size=64)) for w in ("snow", "beach", "dog", "cake")}
MODEL = "test/siglip"


def unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


class FakeModels:
    def __init__(self):
        self.concept = {}

    def embed_images(self, ims):
        return np.stack([unit(CONCEPTS[self.concept[im.info["pid"]]] + rng.normal(scale=0.02, size=64)) for im in ims])

    def embed_text(self, text):
        hits = [CONCEPTS[w] for w in CONCEPTS if w in text.lower()]
        return unit(sum(hits)) if hits else unit(rng.normal(size=64))

    @staticmethod
    def probability(cos):
        return 1 / (1 + np.exp(-(cos * 20 - 10)))

    def caption(self, im):
        return f"A photo of {self.concept[im.info['pid']]} on a sunny day"


@pytest.fixture
def lib(tmp_path, sidecar, device, monkeypatch):
    library = tmp_path / "library"
    conn = open_catalog()
    models = FakeModels()

    from shoebox.analyze import ai_run, image

    real = image.load

    def load(path):
        im = real(path)
        im.info["pid"] = path.stem
        return im
    monkeypatch.setattr(ai_run, "load", load)

    def add(n, concept, **apple):
        apple.setdefault("persons", [])
        apple.setdefault("labels", [])
        apple.setdefault("keywords", [])
        rec = record(sidecar, n, **apple)
        rec["taken_at"] = apple.pop("taken_at", rec["taken_at"]) if "taken_at" in apple else rec["taken_at"]
        upsert_record(conn, rec, device)
        make_jpeg(library / rec["keys"]["compressed"], size=(320, 240))
        models.concept[rec["photo_id"]] = concept
        return rec["photo_id"]

    vdb = open_vectors()

    def run(**kw):
        return analyze_ai(conn, library, device.device_id, embed_images=models.embed_images, search_model=MODEL,
                          caption=models.caption, caption_model="test/florence", vdb=vdb, **kw)

    def smart(q, **filters):
        return smart_search(conn, q, embed_text=models.embed_text, probability=models.probability,
                            index=Index().load(vdb, MODEL), **filters)
    return conn, add, run, smart, device


def ids(res):
    return [i["photo_id"] for i in res["items"]]


def test_finds_photos_by_meaning(lib):
    conn, add, run, smart, _ = lib
    snow = [add(i, "snow") for i in range(1, 4)]
    beach = [add(i, "beach") for i in range(10, 13)]
    counts = run()
    assert counts["embedded"] == 6 and counts["captioned"] == 6
    res = smart("kids playing in the snow")
    assert res["mode"] == "smart"
    assert set(ids(res)) == set(snow)
    assert ids(smart("a plate of spaghetti")) == []                # nothing close: no results, not random ones
    assert run()["embedded"] == 0                                   # done once


def test_captions_are_searchable_everywhere_and_user_edits_win(lib):
    conn, add, run, smart, device = lib
    p = add(1, "dog")
    run()
    assert conn.execute("SELECT caption FROM media WHERE photo_id = ?", (p,)).fetchone()[0] == "A photo of dog on a sunny day"
    assert ids(search(conn, "sunny")) == [p]                        # plain keyword search (works offline on phones)
    Emitter(conn, device.device_id).emit("media.set", {"photo_id": p, "field": "caption", "value": "Bruno, our dog"})
    run()
    assert conn.execute("SELECT caption FROM media WHERE photo_id = ?", (p,)).fetchone()[0] == "Bruno, our dog"
    assert ids(search(conn, "bruno")) == [p]


def test_names_and_dates_are_filters(lib):
    conn, add, run, smart, _ = lib
    a = add(1, "beach", persons=["Asha"])
    b = add(2, "beach", persons=["Ravi"])
    add(3, "snow", persons=["Asha"])
    run()
    res = smart("asha at the beach")
    assert res["people"] == ["Asha"]
    assert ids(res)[0] == a and b not in ids(res)
    found, rest = names_in(conn, "Ravi and Asha in goa")
    assert sorted(found) == ["Asha", "Ravi"] and rest == "and in goa"
    only_names = smart("Asha")
    assert only_names["mode"] == "filters" and len(only_names["items"]) == 2


def test_keyword_matches_are_kept(lib):
    conn, add, run, smart, _ = lib
    cake = add(1, "cake", keywords=["Birthday"])
    others = [add(i, "snow") for i in range(2, 6)]
    run()
    res = smart("birthday")                                         # no concept for "birthday": a tag decides
    assert ids(res)[0] == cake
    assert not set(others) & set(ids(res))


def test_items_without_a_local_copy_are_skipped(lib, tmp_path):
    conn, add, run, smart, _ = lib
    p = add(1, "dog")
    next((tmp_path / "library").rglob(f"{p}.jpg")).unlink()
    assert run()["skipped"] == 1
    assert run()["skipped"] == 0                                    # remembered


def test_vectors_stay_on_this_mac(lib):
    conn, *_ = lib
    import os

    from shoebox import paths

    assert oct(os.stat(paths.home() / "embeddings.sqlite").st_mode & 0o777) == "0o600"
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'vectors'").fetchone() is None


def test_caption_tidying():
    from shoebox.analyze.ai import tidy

    assert tidy("The image shows a dog on a beach. It is sunny. There are three boats and a man in a") == \
        "A dog on a beach. It is sunny."
    assert tidy("In this image we can see a cake") == "A cake."
    assert tidy("") == ""
