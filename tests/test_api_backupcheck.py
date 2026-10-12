import hashlib

from apikit import seed


def test_check_reports_missing_and_archived(api, sidecar, device):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    seed(sidecar, device, app.state.settings.library, n=2)
    from photoctl.catalog import open_catalog
    have = [r[0] for r in open_catalog().execute("SELECT sha256 FROM media")]
    new = hashlib.sha256(b"new photo").hexdigest()
    r = client.post("/api/backup/check", json={"hashes": [have[0], new, have[1].upper()]}).json()
    assert r["missing"] == [new] and sorted(r["archived"]) == sorted(have)
    assert client.post("/api/backup/check", json={"hashes": ["nope"]}).status_code == 422
    assert client.post("/api/backup/check", json={"hashes": [new] * 501}).status_code == 422


def test_uploads_waiting_in_the_inbox_count_as_in_progress(api):
    client, token, _ = api
    client.headers["Authorization"] = f"Bearer {token}"
    from photoctl import paths
    folder = paths.inbox_dir() / "PHONE"
    folder.mkdir(parents=True)
    (folder / "01ABC__x.jpg").write_bytes(b"queued bytes")
    h = hashlib.sha256(b"queued bytes").hexdigest()
    assert client.post("/api/backup/check", json={"hashes": [h]}).json()["in_progress"] == [h]
