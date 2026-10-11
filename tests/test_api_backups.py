from apikit import seed


def test_backup_from_the_app(api, sidecar, device, bucket):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    seed(sidecar, device, app.state.settings.library, n=1)
    assert client.post("/api/db/backup").status_code == 503
    app.state.s3 = bucket
    r = client.post("/api/db/backup").json()
    assert "catalog.sqlite.gz" in r["files"] and r["prefix"].startswith(f"backups/{device.device_id}/")
    listing = client.get("/api/db/backups").json()
    assert listing["last"]["prefix"] == r["prefix"] and len(listing["backups"]) == 1
    assert listing["backups"][0]["catalog_checksum"]
