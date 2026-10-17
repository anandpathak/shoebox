"""The Apple Photos "Archived to S3" album: robust adding, retries, and reconciling with the real album."""

from types import SimpleNamespace as NS

import pytest

from shoebox import archiver
from shoebox.state import State


@pytest.fixture
def st():
    s = State()
    for u in ("A", "B", "C", "D"):
        s.upsert(f"applephotos:{u}", status="done", album=0)
    return s


def test_items_photos_cant_find_are_retried_once_then_marked(st, monkeypatch):
    calls = []

    def add(uuids, name=archiver.ARCHIVE_ALBUM):
        calls.append(list(uuids))
        return ["C"] if len(calls) == 1 else (["C"] if "C" in uuids else [])
    monkeypatch.setattr(archiver, "add_to_album", add)
    msgs = []
    assert archiver.flush_album(st, lambda k, **d: msgs.append(d["text"]), retry_wait=0) == 3
    assert calls == [["A", "B", "C", "D"], ["C"]]                      # one more go for the missing one
    s = st.album_summary()
    assert (s["in_album"], s["could_not_add"], s["to_add"]) == (3, 1, 0)
    assert s["problems"][0]["uuid"] == "C" and "couldn't find" in s["problems"][0]["error"]
    assert any("Added 3 items" in m for m in msgs)


def test_photos_unreachable_keeps_everything_pending(st, monkeypatch):
    monkeypatch.setattr(archiver, "add_to_album", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Photos not running")))
    assert archiver.flush_album(st, retry_wait=0) == 0
    assert st.album_summary()["to_add"] == 4


def test_reconcile_fixes_records_against_the_real_album(monkeypatch):
    s = State()
    s.upsert("applephotos:IN", status="done")               # recorded as in the album (old default) and it is
    s.upsert("applephotos:NOT", status="done")              # recorded as in the album, but it isn't
    s.upsert("applephotos:DEL", status="done", album=0)     # deleted from Photos since: nothing to do
    s.upsert("applephotos:TRASH", status="done", album=0)   # in Recently Deleted
    s.upsert("applephotos:BAD", status="done", album=2)     # Photos couldn't find it: stays a problem
    photos = {"IN": NS(intrash=False), "NOT": NS(intrash=False), "TRASH": NS(intrash=True), "BAD": NS(intrash=False)}
    db = NS(album_info=[NS(title="Archived to S3", photos=[NS(uuid="IN")]), NS(title="Videos", photos=[NS(uuid="NOT")])],
            get_photo=photos.get)
    changed = archiver.reconcile_album(s, db)
    assert changed == {"in_album": 0, "to_add": 1, "gone": 2}
    assert sorted(s.album_pending()) == ["applephotos:NOT"]
    summary = s.album_summary()
    assert (summary["in_album"], summary["gone"], summary["could_not_add"]) == (1, 2, 1)


def test_a_run_with_nothing_new_still_adds_leftovers(monkeypatch):
    s = State()
    s.upsert("applephotos:OLD", status="done", album=0)
    added = []
    monkeypatch.setattr(archiver, "add_to_album", lambda uuids, name=None: added.extend(uuids) or [])
    item = NS(uuid="OLD", path=None, date=None, original_filename="x.mov", width=1, height=1, uti_original=None,
              ismovie=True, has_raw=False, path_raw=None, live_photo=False, path_live_photo=None, intrash=False,
              hidden=False, json=lambda: "{}")
    summary = archiver.run_archive([item], archiver.Options(bucket=None, sync=False, backup=False))
    assert summary.todo == 0 and added == ["OLD"]


def test_album_api(api, monkeypatch):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    s = State()
    s.upsert("applephotos:X", status="done", album=0)
    monkeypatch.setattr(archiver, "reconcile_album", lambda state, db=None: {"in_album": 0, "to_add": 0, "gone": 0})
    monkeypatch.setattr(archiver, "add_to_album", lambda uuids, name=None: [])
    assert client.get("/api/album").json()["to_add"] == 1
    assert client.post("/api/album/sync").json()["started"] is True
    import time
    for _ in range(50):
        body = client.get("/api/album").json()
        if not body["running"] and body["last"]:
            break
        time.sleep(0.1)
    assert body["in_album"] == 1 and body["to_add"] == 0 and body["last"]["added"] == 1
