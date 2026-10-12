"""Image analysis: labels → searchable tags; faces → clusters → named people (searchable everywhere)."""

import os
import sys

import numpy as np
import pytest

from conftest import make_jpeg
from opkit import record
from photoctl.analyze import labels as L
from photoctl.analyze.faces import Face
from photoctl.analyze.facedb import clusters, ignore_faces, name_faces, open_faces, rematch, unname_face
from photoctl.analyze.run import analyze_pending
from photoctl.catalog import open_catalog
from photoctl.catalog.ops import Emitter
from photoctl.catalog.store import upsert_record

rng = np.random.default_rng(7)


def person_vec():
    v = rng.normal(size=128).astype(np.float32)
    return v / np.linalg.norm(v)


def like(v, noise=0.15):
    w = v + rng.normal(scale=noise / np.sqrt(128), size=128).astype(np.float32)
    return (w / np.linalg.norm(w)).astype(np.float32)


class Fakes:
    """Stand-ins for Apple Vision and the face engine, per photo."""

    def __init__(self):
        self.labels, self.faces, self.text = {}, {}, {}

    def read_text(self, im):
        return self.text.get(im.info["pid"], "")

    def classify(self, im):
        return self.labels.get(im.info["pid"], [])

    def find_faces(self, im):
        return [Face((0.3, 0.3, 0.2, 0.25), 0.95, e) for e in self.faces.get(im.info["pid"], [])]


@pytest.fixture
def lib(tmp_path, sidecar, device, monkeypatch):
    library = tmp_path / "library"
    conn = open_catalog()
    fakes = Fakes()

    def add(n, labels=(), faces=(), text="", **apple):
        apple.setdefault("persons", [])          # the sample sidecar names "Asha"; only name people when a test says so
        apple.setdefault("labels", [])
        rec = record(sidecar, n, **apple)
        upsert_record(conn, rec, device)
        make_jpeg(library / rec["keys"]["compressed"])
        fakes.labels[rec["photo_id"]] = list(labels)
        fakes.faces[rec["photo_id"]] = list(faces)
        fakes.text[rec["photo_id"]] = text
        return rec["photo_id"]

    from photoctl.analyze import image, run

    real_load = image.load

    def load(path):                      # tag the image with its photo id so the fakes know which photo it is
        im = real_load(path)
        im.info["pid"] = path.stem
        return im
    monkeypatch.setattr(run, "load", load)
    fdb = open_faces()

    def analyze(**kw):
        return analyze_pending(conn, library, device.device_id, fdb=fdb, classify=fakes.classify,
                               find_faces=fakes.find_faces, read_text=fakes.read_text, **kw)

    def emit(ops):
        if ops:
            Emitter(conn, device.device_id).emit_many(ops)
    return conn, fdb, add, analyze, emit


def vtags(conn, pid):
    return sorted(r[0] for r in conn.execute("SELECT DISTINCT tag FROM media_tags WHERE photo_id = ? AND source = 'vision' "
                                             "AND add_op NOT IN (SELECT add_op FROM tombstones)", (pid,)))


def search(conn, q):
    return sorted(r[0] for r in conn.execute("SELECT photo_id FROM media_fts WHERE media_fts MATCH ?", (q,)))


def test_labels_become_searchable_tags_once(lib):
    conn, fdb, add, analyze, emit = lib
    p = add(1, labels=[("mountain", 0.9), ("blue_sky", 0.7), ("structure", 0.9), ("car", 0.1)])
    counts = analyze()
    assert counts["analyzed"] == 1
    assert vtags(conn, p) == ["blue sky", "landscape", "mountain", "nature"]
    assert search(conn, "landscape") == [p] and search(conn, '"blue sky"') == [p]
    assert analyze()["analyzed"] == 0                                        # done once
    # a tag the user removed isn't put back when re-analysing
    adds = [a for (a,) in conn.execute("SELECT add_op FROM media_tags WHERE photo_id = ? AND tag = 'mountain'", (p,))]
    emit([("tag.remove", {"photo_id": p, "tag": "mountain", "observed_add_ops": adds})])
    analyze(redo=True)
    assert "mountain" not in vtags(conn, p)


def test_people_words(lib):
    assert "people" in L.pick([], faces=1) and "portrait" in L.pick([], faces=1, face_area=0.1)
    assert "group" in L.pick([], faces=4)
    assert "landscape" not in L.pick([("mountain", 0.9)], faces=3)          # a group photo, not a landscape
    assert "landscape" not in L.pick([("grass", 0.9)], faces=1, face_area=0.03)
    assert "landscape" in L.pick([("mountain", 0.9)], faces=1, face_area=0.002)   # a tiny hiker on the ridge


def test_faces_cluster_then_naming_makes_people_searchable(lib):
    conn, fdb, add, analyze, emit = lib
    asha, ravi = person_vec(), person_vec()
    a = [add(i, faces=[like(asha)]) for i in range(10, 13)]
    r = [add(i, faces=[like(ravi)]) for i in range(20, 22)]
    analyze()
    groups = clusters(fdb)
    assert sorted(g["faces"] for g in groups) == [2, 3]
    big = groups[0]
    person, ops = name_faces(fdb, conn, [f for (f,) in fdb.execute(
        "SELECT face_id FROM faces WHERE cluster = ?", (big["cluster"],))], "Asha")
    emit(ops)
    assert search(conn, "asha") == sorted(a)
    assert {s for (s,) in conn.execute("SELECT source FROM media_people WHERE person_id = ?", (person,))} == {"face"}
    assert [g["faces"] for g in clusters(fdb)] == [2]                       # Ravi still waiting for a name
    # a new photo of Asha is recognised by itself
    new = add(13, faces=[like(asha)])
    analyze()
    assert new in search(conn, "asha")
    assert search(conn, "ravi") == []
    assert not set(r) & set(search(conn, "asha"))


def test_naming_with_an_existing_name_joins_that_person(lib):
    conn, fdb, add, analyze, emit = lib
    p = add(1, persons=["Kid"])                                              # named in Apple Photos, no face found
    k = person_vec()
    q = add(2, faces=[like(k)])
    analyze()
    person, ops = name_faces(fdb, conn, [f for (f,) in fdb.execute("SELECT face_id FROM faces")], "kid")
    emit(ops)
    assert search(conn, "kid") == sorted([p, q])
    assert conn.execute("SELECT COUNT(*) FROM people WHERE lower(name) = 'kid'").fetchone()[0] == 1


def test_learns_faces_from_apple_photos_names(lib):
    conn, fdb, add, analyze, emit = lib
    g = person_vec()
    add(1, faces=[like(g)], persons=["Grandma"])                             # one face, one name: that's her
    new = add(2, faces=[like(g)])
    analyze()
    assert new in search(conn, "grandma")
    assert clusters(fdb) == []


def test_two_people_on_a_named_photo_are_not_guessed(lib):
    conn, fdb, add, analyze, emit = lib
    add(1, faces=[like(person_vec()), like(person_vec())], persons=["Asha"])
    analyze()
    assert fdb.execute("SELECT COUNT(*) FROM faces WHERE status = 'confirmed'").fetchone()[0] == 0


def test_not_them_and_not_a_face(lib):
    conn, fdb, add, analyze, emit = lib
    a = person_vec()
    p1, p2 = add(1, faces=[like(a)], persons=["Asha"]), add(2, faces=[like(a)])
    analyze()
    assert p2 in search(conn, "asha")
    face = fdb.execute("SELECT face_id FROM faces WHERE photo_id = ?", (p2,)).fetchone()[0]
    emit(unname_face(fdb, conn, face))
    assert search(conn, "asha") == [p1]
    ignore_faces(fdb, [face])
    assert clusters(fdb) == []
    assert rematch(fdb, conn) == []


def test_photos_without_a_local_copy_are_skipped(lib, tmp_path):
    conn, fdb, add, analyze, emit = lib
    p = add(1, labels=[("beach", 0.9)])
    path = next((tmp_path / "library").rglob(f"{p}.jpg"))
    os.remove(path)
    assert analyze()["skipped"] == 1
    assert vtags(conn, p) == []


def test_face_data_stays_private(lib):
    conn, fdb, add, analyze, emit = lib
    from photoctl import paths

    assert oct(os.stat(paths.home() / "faces.sqlite").st_mode & 0o777) == "0o600"
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'faces'").fetchone() is None   # not in the catalog


def test_text_in_the_picture_is_searchable(lib):
    conn, fdb, add, analyze, emit = lib
    p = add(1, text="CROMA Tax Invoice Total 4,299.00")
    q = add(2, text="")
    analyze()
    assert search(conn, "croma") == [p] and search(conn, "invoice") == [p]
    assert conn.execute("SELECT text_in_image FROM media WHERE photo_id = ?", (q,)).fetchone()[0] is None


def test_stray_letters_are_not_text():
    from photoctl.analyze.vision import useful_text

    assert useful_text("A") == "" and useful_text("NIKE") == ""
    assert useful_text("Tax  Invoice") == "Tax Invoice"


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple Vision is macOS only")
def test_real_vision_reads_a_receipt():
    from PIL import Image, ImageDraw, ImageFont

    from photoctl.analyze.vision import read_text

    im = Image.new("RGB", (900, 500), "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default(size=48)
    for i, line in enumerate(["CROMA ELECTRONICS", "Tax Invoice", "Total 4299.00"]):
        d.text((60, 60 + i * 110), line, fill="black", font=font)
    text = read_text(im).lower()
    assert "croma" in text and "invoice" in text


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple Vision is macOS only")
def test_real_vision_labels_and_vocabulary():
    from PIL import Image, ImageDraw

    from photoctl.analyze.vision import classify, known_labels

    known = known_labels()
    unknown = {s for sources in L.DERIVED.values() for s in sources} - known
    assert not unknown, f"DERIVED uses labels Vision doesn't have: {sorted(unknown)}"
    im = Image.new("RGB", (800, 600), (120, 180, 240))
    ImageDraw.Draw(im).rectangle([0, 380, 800, 600], fill=(40, 140, 60))
    got = dict(classify(im))
    assert got and all(0 <= c <= 1 for c in got.values())
