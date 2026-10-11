from apikit import seed


def test_original_request_and_status(api, sidecar, device, bucket, tmp_path):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    ids = seed(sidecar, device, app.state.settings.library, n=1)
    assert client.post(f"/api/media/{ids[0]}/original", json={}).status_code == 503   # no S3 configured
    app.state.s3 = bucket
    from photoctl.catalog import open_catalog
    raw = open_catalog().execute("SELECT raw_key FROM media").fetchone()[0]
    f = tmp_path / "orig.heic"
    f.write_bytes(b"original bytes")
    bucket.create_if_absent(f, raw, storage_class="DEEP_ARCHIVE")

    r = client.post(f"/api/media/{ids[0]}/original", json={"tier": "bulk", "days": 2}).json()
    assert r["status"] in ("in_progress", "ready") and r["tier"] == "Bulk" and r["eta_hours"] == 48
    s = client.get(f"/api/media/{ids[0]}/original").json()
    if s["status"] == "ready":
        assert "X-Amz-Expires=900" in s["url"]
    assert client.get("/api/originals").json()[0]["photo_id"] == ids[0]
    assert client.post(f"/api/media/{ids[0]}/original", json={"tier": "instant"}).status_code == 422
