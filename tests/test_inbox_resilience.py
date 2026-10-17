"""The upload inbox survives bad files, errors and restarts; Apple Photos album marking is retried."""

import json
import time

from test_api_tus import create, jpeg_with_exif, patch


def test_cache_files_are_never_hashed_again(api):
    client, token, _ = api
    client.headers["Authorization"] = f"Bearer {token}"
    from shoebox import paths

    folder = paths.inbox_dir() / "PHONE"
    folder.mkdir(parents=True)
    (folder / "01ABC__x.jpg").write_bytes(b"queued")
    (folder / "01ABC.json").write_text(json.dumps({"id": "01ABC", "filename": "x.jpg", "device_id": "PHONE", "created": 1}))
    for _ in range(3):                                        # the old code grew x.jpg.sha256.sha256… on every call
        client.post("/api/backup/check", json={"hashes": ["0" * 64]})
    assert sorted(f.name for f in folder.iterdir()) == ["01ABC.json", "01ABC__x.jpg", "01ABC__x.jpg.sha256"]


def test_tidy_removes_debris_but_not_uploads():
    from shoebox import paths
    from shoebox.server.routes.tus import tidy_inbox

    folder = paths.inbox_dir() / "PHONE"
    folder.mkdir(parents=True)
    (folder / "01A__x.jpg").write_bytes(b"photo")
    (folder / "01A.json").write_text("{}")
    (folder / "01A__x.jpg.sha256").write_text("h")
    (folder / "01A__x.jpg.sha256.sha256.sha256").write_text("junk")
    (folder / "01GONE__y.jpg.sha256").write_text("orphan")
    assert tidy_inbox()["cache"] == 2
    assert sorted(f.name for f in folder.iterdir()) == ["01A.json", "01A__x.jpg", "01A__x.jpg.sha256"]


def test_a_failing_file_backs_off_then_is_set_aside_until_retry(api, monkeypatch):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    from shoebox import archiver
    from shoebox.archiver import Result

    calls = []
    real = archiver.process

    def broken(item, opts):
        if "bad" in item.original_filename:
            calls.append(time.time())
            return Result(item.source_ref, "failed", error="S3 said no")
        return real(item, opts)
    monkeypatch.setattr(archiver, "process", broken)
    inbox = app.state.inbox
    inbox.MAX_ATTEMPTS = 3

    def wait_for(n):
        for _ in range(150):
            if len(calls) >= n and inbox.pending() and inbox.pending()[0][1].get("attempts", 0) >= n:
                return
            time.sleep(0.1)
        raise AssertionError(f"expected {n} attempts, saw {len(calls)}")

    url = create(client, jpeg_with_exif(), "bad.jpg")
    patch(client, url, 0, jpeg_with_exif())
    wait_for(1)
    time.sleep(1.5)
    assert len(calls) == 1                                    # not retried straight away (no tight loop)
    st = inbox.status()["pending"][0]
    assert st["attempts"] == 1 and st["error"] == "S3 said no" and not st["failed"]
    assert inbox.pending()[0][1]["next_try"] > time.time() + 30   # about a minute later
    for n in (2, 3):                                          # pretend each wait is over
        for _, info, meta in inbox._all():
            info["next_try"] = 0
            meta.write_text(json.dumps(info))
        inbox.kick()
        wait_for(n)
    assert inbox.status()["pending"][0]["failed"] is True     # set aside after MAX_ATTEMPTS
    for _, info, meta in inbox._all():
        info["next_try"] = 0
        meta.write_text(json.dumps(info))
    inbox.kick()
    time.sleep(1.5)
    assert len(calls) == 3                                    # left alone until someone asks

    monkeypatch.setattr(archiver, "process", real)
    assert client.post("/api/uploads/files/retry").status_code == 200   # "Retry failed"
    for _ in range(150):
        if not inbox.pending():
            break
        time.sleep(0.2)
    assert inbox.pending() == []


def test_an_unexpected_error_does_not_stop_the_inbox(api, monkeypatch):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    inbox = app.state.inbox
    real = app.state.runs.start
    boom = {"n": 0}

    def flaky(*a, **kw):
        boom["n"] += 1
        if boom["n"] == 1:
            raise RuntimeError("disk hiccup")
        return real(*a, **kw)
    monkeypatch.setattr(app.state.runs, "start", flaky)
    url = create(client, jpeg_with_exif(), "ok.jpg")
    patch(client, url, 0, jpeg_with_exif())
    for _ in range(200):
        if not inbox.pending():
            break
        time.sleep(0.1)
    assert boom["n"] >= 2 and inbox.pending() == []


def test_album_marking_is_retried_after_a_failure(monkeypatch, tmp_path):
    from shoebox import archiver
    from shoebox.state import State

    st = State()
    st.upsert("applephotos:U1", status="done", album=0)
    st.upsert("applephotos:U2", status="done", album=0)
    st.upsert("applephotos:OLD", status="done")               # from before this existed: counts as added
    st.upsert("upload:x", status="done", album=0)             # not an Apple Photos item
    added = []
    monkeypatch.setattr(archiver, "add_to_album", lambda uuids: (_ for _ in ()).throw(RuntimeError("Photos busy")))
    assert archiver.flush_album(st) == 0
    assert sorted(st.album_pending()) == ["applephotos:U1", "applephotos:U2"]
    monkeypatch.setattr(archiver, "add_to_album", lambda uuids: added.extend(uuids) or [])
    assert archiver.flush_album(st) == 2 and sorted(added) == ["U1", "U2"]
    assert st.album_pending() == []
