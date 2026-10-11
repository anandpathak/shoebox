import time

from fastapi.testclient import TestClient

from apikit import seed
from photoctl.runlock import SyncLock


def test_sync_now_and_status(api, sidecar, device, bucket):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    seed(sidecar, device, app.state.settings.library, n=2)
    assert client.get("/api/sync/status").json()["pending_ops"] > 0
    assert client.post("/api/sync").status_code == 503          # no S3 yet
    app.state.s3 = bucket
    r = client.post("/api/sync").json()
    assert r["error"] is None and r["last_result"]["pushed_ops"] > 0
    st = client.get("/api/sync/status").json()
    assert st["pending_ops"] == 0 and st["last_ok"] and st["s3"]
    with SyncLock():
        assert client.post("/api/sync").status_code == 409


def test_background_loop_syncs(tmp_path, sidecar, device, bucket):
    from photoctl.server.app import create_app
    from photoctl.server.config import Settings

    seed(sidecar, device, tmp_path, n=1)
    app = create_app(Settings(library=tmp_path, bucket="test-archive", sync_interval=1))
    app.state.s3 = bucket
    with TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 1)) as client:
        client.headers["Authorization"] = f"Bearer {app.state.token}"
        for _ in range(50):
            if client.get("/api/sync/status").json()["last_ok"]:
                break
            time.sleep(0.1)
        assert client.get("/api/sync/status").json()["pending_ops"] == 0


def test_verify_endpoint(api, sidecar, device, bucket, tmp_path):
    from photoctl.catalog import open_catalog
    from photoctl.device import Device
    from photoctl.sync import sync_once

    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    seed(sidecar, device, app.state.settings.library, n=1)
    assert client.get("/api/sync/verify").json()["s3"] is False
    app.state.s3 = bucket
    client.post("/api/sync")
    other = open_catalog(tmp_path / "o.sqlite")
    peer = Device("01PEERPEERPEERPEERPEERPEER", "Mini", "macos", "x")
    sync_once(other, bucket, peer)
    client.post("/api/sync")
    sync_once(other, bucket, peer)
    r = client.get("/api/sync/verify").json()
    assert r["converged"] is True and {d["name"] for d in r["devices"]} == {"Mini", device.name}
