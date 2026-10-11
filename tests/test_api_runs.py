import json
import time

import pytest

from test_archiver import photos


@pytest.fixture
def authed(api, tmp_path, monkeypatch):
    from photoctl import archiver

    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    items = photos(tmp_path, 6)
    monkeypatch.setattr(archiver, "select_photos", lambda sel: items)
    monkeypatch.setattr(archiver, "add_to_album", lambda *a, **k: None)
    return client, app, monkeypatch


def wait_done(client, run_id, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/api/runs/{run_id}").json()
        if r["status"] != "running":
            return r
        time.sleep(0.1)
    raise AssertionError("run didn't finish")


def test_start_watch_and_history(authed):
    client, app, _ = authed
    r = client.post("/api/uploads/apple-photos", json={"mode": "all"})
    assert r.status_code == 202
    run = wait_done(client, r.json()["run_id"])
    assert run["status"] == "done" and run["counts"] == {"done": 6}
    assert client.get("/api/runs").json()[0]["run_id"] == run["run_id"]
    assert client.get("/api/search").json()["items"]                     # archived items show up
    events = client.get(f"/api/runs/{run['run_id']}/events")
    kinds = [json.loads(line[6:])["event"] for line in events.text.splitlines() if line.startswith("data: {\"")]
    assert kinds[:2] == ["accepted", "start"] and kinds.count("item") == 6 and "end" in kinds


def test_second_run_is_refused_and_cancel_works(authed):
    client, app, monkeypatch = authed
    from photoctl import archiver

    real = archiver.process
    monkeypatch.setattr(archiver, "process", lambda item, opts: (time.sleep(0.3), real(item, opts))[1])
    first = client.post("/api/uploads/apple-photos", json={"mode": "all"}).json()
    busy = client.post("/api/uploads/apple-photos", json={"mode": "all"})
    assert busy.status_code == 409 and busy.json()["detail"]["run"]["run_id"] == first["run_id"]
    assert client.post(f"/api/runs/{first['run_id']}/cancel").json()["cancelling"]
    run = wait_done(client, first["run_id"])
    assert run["status"] == "cancelled" and run["processed"] < 6
    assert client.post(f"/api/runs/{first['run_id']}/cancel").status_code == 409


def test_validation(authed):
    client, _, _ = authed
    assert client.post("/api/uploads/apple-photos", json={"mode": "album"}).status_code == 422
    assert client.post("/api/uploads/apple-photos", json={"mode": "range"}).status_code == 422
    assert client.post("/api/uploads/apple-photos", json={"mode": "everything"}).status_code == 422
    assert client.get("/api/runs/NOPE").status_code == 404


def test_retry_failed_items_and_active(authed):
    client, app, monkeypatch = authed
    assert client.get("/api/runs/active").json() is None
    r = client.post("/api/uploads/apple-photos", json={"mode": "uuids", "uuids": ["U1", "U2"]})
    assert r.status_code == 202 and r.json()["label"] == "Retry 2 item(s)"
    wait_done(client, r.json()["run_id"])
    assert client.post("/api/uploads/apple-photos", json={"mode": "uuids"}).status_code == 422
