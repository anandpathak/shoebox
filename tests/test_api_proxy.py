import pytest

from apikit import seed
from opkit import Dev


@pytest.fixture
def authed(api, sidecar, device):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    return client, seed(sidecar, device, app.state.settings.library, n=1), app


def test_phone_pushes_ops_and_they_reach_s3(authed, bucket, tmp_path):
    from shoebox.catalog import open_catalog
    from shoebox.sync import pull, sync_once

    client, ids, app = authed
    phone = Dev("01PHONEPHONEPHONEPHONEPHON")
    first = client.get("/api/sync/ops").json()
    phone.see(first["ops"])
    op = phone.op("media.set", {"photo_id": ids[0], "field": "title", "value": "From my phone"})
    r = client.post("/api/sync/ops", json={"device_id": phone.device_id, "ops": [op]}).json()
    assert r == {"accepted": 1, "already_had": 0}
    assert client.post("/api/sync/ops", json={"device_id": phone.device_id, "ops": [op]}).json()["already_had"] == 1
    assert client.get(f"/api/media/{ids[0]}").json()["title"] == "From my phone"

    sync_once(open_catalog(), bucket, app.state.device)   # the server pushes on the phone's behalf
    assert bucket.list_keys(f"sync/ops/{phone.device_id}/")
    other = open_catalog(tmp_path / "other.sqlite")
    pull(other, bucket, "01OTHER")
    assert other.execute("SELECT title FROM media").fetchone()[0] == "From my phone"


def test_push_rejects_bad_or_spoofed_ops(authed):
    client, ids, app = authed
    phone, mallory = Dev("01PHONE"), Dev("01MALLORY")
    op = mallory.op("media.set", {"photo_id": ids[0], "field": "title", "value": "x"})
    assert client.post("/api/sync/ops", json={"device_id": "01PHONE", "ops": [op]}).status_code == 422
    bad = phone.op("media.set", {"photo_id": ids[0], "field": "title", "value": "x"})
    bad["body"]["field"] = "raw_key"
    assert client.post("/api/sync/ops", json={"device_id": "01PHONE", "ops": [bad]}).status_code == 422
    me = app.state.device.device_id
    assert client.post("/api/sync/ops", json={"device_id": me, "ops": []}).status_code == 409


def test_pull_pages_with_cursor_and_epoch(authed):
    client, ids, _ = authed
    page = client.get("/api/sync/ops", params={"limit": 2}).json()
    assert len(page["ops"]) == 2 and page["more"]
    rest = client.get("/api/sync/ops", params={"since": page["cursor"], "epoch": page["epoch"]}).json()
    assert not rest["more"] and rest["ops"]
    assert client.get("/api/sync/ops", params={"since": 999999}).status_code == 410
    assert client.get("/api/sync/ops", params={"epoch": "OLD"}).status_code == 410


def test_register_device(authed):
    client, _, _ = authed
    r = client.post("/api/devices/register", json={"device_id": "01IPHONE", "name": "My iPhone",
                                                  "platform": "ios"})
    assert r.status_code == 200
    assert {"name": "My iPhone", "platform": "ios"}.items() <= next(
        d for d in client.get("/api/devices").json() if d["device_id"] == "01IPHONE").items()
    assert client.post("/api/devices/register", json={"device_id": "x", "name": "y",
                                                      "platform": "amiga"}).status_code == 422
