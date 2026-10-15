import json
from pathlib import Path

import pytest

from opkit import record
from shoebox.catalog import open_catalog
from shoebox.catalog.ops import Emitter
from shoebox.catalog.query import build_fts, media_detail, parse_query, search, search_catalogs
from shoebox.catalog.store import upsert_record
from shoebox.device import Device

CASES = json.loads((Path(__file__).parent.parent / "spec" / "query_vectors.json").read_text())["cases"]
MAC = Device("01MACMACMACMACMACMACMACMAC", "Mac", "macos", "x")
PIXEL = Device("01PIXELPIXELPIXELPIXELPIXE", "Pixel 8", "android", "x")


@pytest.mark.parametrize("case", CASES, ids=[c["text"] or "(empty)" for c in CASES])
def test_query_vectors(case):
    parsed = parse_query(case["text"])
    assert parsed == case["expect"]
    assert build_fts(parsed["terms"], person=case.get("person"), place=case.get("place"), tag=case.get("tag"),
                     album=case.get("album")) == case["fts"]


@pytest.fixture
def lib(tmp_path, sidecar):
    conn = open_catalog(tmp_path / "c.sqlite")
    specs = [  # n, taken_at, labels, persons, city, type, device
        (1, "2025-08-15T11:52:05+05:30", ["Beach", "Sea"], ["Asha"], "Gokarna", "photo", MAC),
        (2, "2024-12-25T10:00:00+05:30", ["Cake"], ["Asha"], "Bengaluru", "photo", MAC),
        (3, "2024-12-28T09:00:00+05:30", ["Beach"], [], "Palolem", "video", PIXEL),
        (4, "2023-11-12T19:00:00+05:30", ["Lights"], [], "Delhi", "photo", PIXEL),
        (5, "2024-11-01T19:00:00+05:30", ["Lights"], [], "Delhi", "photo", MAC),
    ]
    ids = {}
    for n, taken, labels, persons, city, mtype, dev in specs:
        rec = record(sidecar, n, labels=labels, persons=persons, keywords=[], albums=[], title=None, favorite=False,
                     place={"name": city, "address": {"city": city, "country": "India"}})
        rec["taken_at"], rec["media_type"] = taken, mtype
        upsert_record(conn, rec, dev)
        ids[n] = rec["photo_id"]
    return conn, ids


def found(res):
    return [r["photo_id"] for r in res["items"]]


def test_filters(lib):
    conn, ids = lib
    assert found(search(conn, "beach")) == [ids[1], ids[3]]                       # newest first
    assert found(search(conn, "beach december 2024")) == [ids[3]]
    assert found(search(conn, "lights november")) == [ids[5], ids[4]]            # month in any year
    assert found(search(conn, "videos")) == [ids[3]]
    assert found(search(conn, person="asha")) == [ids[1], ids[2]]
    assert found(search(conn, place="goka")) == [ids[1]]
    assert found(search(conn, device="android")) == [ids[3], ids[4]]
    assert found(search(conn, device="Pixel 8", tag="beach")) == [ids[3]]
    assert found(search(conn, date_from="2024-01-01", date_to="2025-01-01")) == [ids[3], ids[2], ids[5]]
    assert found(search(conn, "nothing-matches-this")) == []


def test_hidden_deleted_favorite(lib):
    conn, ids = lib
    em = Emitter(conn, MAC.device_id)
    em.emit("media.set", {"photo_id": ids[1], "field": "hidden", "value": True})
    em.emit("media.delete", {"photo_id": ids[2]})
    em.emit("media.set", {"photo_id": ids[5], "field": "favorite", "value": True})
    everything = found(search(conn))
    assert ids[1] not in everything and ids[2] not in everything
    assert ids[1] in found(search(conn, include_hidden=True))
    assert found(search(conn, favorite=True)) == [ids[5]]


def test_keyset_pagination_walks_everything_once(lib):
    conn, _ = lib
    seen, cursor = [], None
    while True:
        page = search(conn, cursor=cursor, limit=2)
        seen += found(page)
        cursor = page["next"]
        if not cursor:
            break
    assert seen == found(search(conn, limit=100)) and len(seen) == 5


def test_hostile_text_is_never_fts_syntax(lib):
    conn, _ = lib
    for text in ['beach OR "', "NEAR(a b)", "people:asha", "*", "a AND", '") OR 1=1 --']:
        search(conn, text)  # must not raise


def test_detail(lib):
    conn, ids = lib
    d = media_detail(conn, ids[1])
    assert d["tags"] == ["beach", "sea"] and [p["name"] for p in d["people"]] == ["Asha"]
    assert d["sources"][0]["platform"] == "macos"


def test_search_several_catalogs_dedups(tmp_path, sidecar):
    a, b = open_catalog(tmp_path / "iphone.sqlite"), open_catalog(tmp_path / "pixel.sqlite")
    shared = record(sidecar, 1, labels=["Beach"])
    upsert_record(a, shared, MAC)
    upsert_record(b, shared, PIXEL)
    upsert_record(b, record(sidecar, 2, labels=["Beach"]), PIXEL)
    a.close(), b.close()
    res = search_catalogs([tmp_path / "iphone.sqlite", tmp_path / "pixel.sqlite"], "beach")
    assert len(res["items"]) == 2
    both = next(i for i in res["items"] if i["photo_id"] == shared["photo_id"])
    assert both["devices"] == sorted([MAC.device_id, PIXEL.device_id]) and len(both["catalogs"]) == 2
