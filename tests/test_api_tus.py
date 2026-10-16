import base64
import time
from io import BytesIO

import pytest
from PIL import Image


def jpeg_with_exif(gps=True) -> bytes:
    im = Image.new("RGB", (1200, 900), (40, 120, 160))
    ex = Image.Exif()
    ex[271], ex[272] = "Google", "Pixel 8"
    ex.get_ifd(0x8769)[36867] = "2025:08:15 11:52:05"
    ex.get_ifd(0x8769)[36881] = "+05:30"
    if gps:
        g = ex.get_ifd(0x8825)
        g[1], g[2], g[3], g[4] = "N", (14.0, 32.0, 2.0), "E", (74.0, 19.0, 1.0)
    buf = BytesIO()
    im.save(buf, "JPEG", exif=ex.tobytes(), quality=90)
    return buf.getvalue()


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


@pytest.fixture
def authed(api):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    return client, app


def create(client, data: bytes, name: str, device="01PIXELPIXELPIXELPIXELPIXE"):
    r = client.post("/api/uploads/files", headers={"Tus-Resumable": "1.0.0", "Upload-Length": str(len(data)),
                    "Upload-Metadata": f"filename {b64(name)},filetype {b64('image/jpeg')},device_id {b64(device)}"})
    assert r.status_code == 201
    return r.headers["location"]


def patch(client, url, offset, chunk):
    return client.patch(url, content=chunk, headers={"Tus-Resumable": "1.0.0", "Upload-Offset": str(offset),
                                                     "Content-Type": "application/offset+octet-stream"})


def wait_archived(client, n, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        items = client.get("/api/search").json()["items"]
        if len(items) >= n and not client.get("/api/uploads/files").json()["pending"]:
            return items
        time.sleep(0.2)
    raise AssertionError("uploads weren't archived")


def test_resumable_upload_then_archive(authed):
    client, app = authed
    data = jpeg_with_exif()
    url = create(client, data, "PXL_20250815.jpg")
    half = len(data) // 2
    assert patch(client, url, 0, data[:half]).headers["upload-offset"] == str(half)
    # connection drops; client asks where to resume
    assert client.head(url).headers["upload-offset"] == str(half)
    assert patch(client, url, 0, data[half:]).status_code == 409          # wrong offset is refused
    assert patch(client, url, half, data[half:]).status_code == 204
    items = wait_archived(client, 1)
    detail = client.get(f"/api/media/{items[0]['photo_id']}").json()
    assert detail["taken_at"].startswith("2025-08-15T11:52:05")
    assert round(detail["lat"], 2) == 14.53 and detail["camera_model"] == "Pixel 8"
    assert detail["sources"][0]["device_id"] == "01PIXELPIXELPIXELPIXELPIXE"
    assert client.get("/api/search", params={"device": "01PIXELPIXELPIXELPIXELPIXE"}).json()["items"]


def test_missing_gps_is_flagged(authed, tmp_path):
    client, app = authed
    data = jpeg_with_exif(gps=False)
    url = create(client, data, "no-location.jpg")
    patch(client, url, 0, data)
    items = wait_archived(client, 1)
    import json
    meta = next(app.state.settings.library.rglob("meta/**/*.json"))
    rec = json.loads(meta.read_text())
    assert rec["metadata_incomplete"] is True and rec["metadata_missing"] == ["location"]
    assert rec["metadata_fidelity"] == "browser" and items


def test_without_s3_the_upload_is_kept_not_deleted(authed):
    client, app = authed
    data = jpeg_with_exif()
    patch(client, create(client, data, "keep.jpg"), 0, data)
    wait_archived(client, 1)
    from shoebox import paths
    kept = list(paths.inbox_dir().glob("*/archived-locally/*__keep.jpg"))
    assert len(kept) == 1 and kept[0].read_bytes() == data


def test_protocol_details(authed):
    client, _ = authed
    assert client.options("/api/uploads/files").headers["tus-extension"] == "creation,termination"
    assert client.post("/api/uploads/files", headers={"Tus-Resumable": "1.0.0"}).status_code == 400
    url = create(client, b"x" * 10, "x.jpg")
    assert patch(client, url, 0, b"y" * 11).status_code == 400            # longer than declared
    assert client.patch(url, content=b"x", headers={"Upload-Offset": "0"}).status_code == 415
    assert client.delete(url).status_code == 204 and client.head(url).status_code == 404
    assert client.head("/api/uploads/files/../../etc").status_code in (404, 405)


def test_with_s3_inbox_is_cleared_after_upload(authed, bucket):
    import os
    client, app = authed
    app.state.s3 = bucket
    app.state.settings.bucket = "test-archive"
    data = jpeg_with_exif()
    patch(client, create(client, data, "to-s3.jpg"), 0, data)
    wait_archived(client, 1)
    from shoebox import paths
    end = time.time() + 10
    while list(paths.inbox_dir().glob("*/*__to-s3.jpg")) and time.time() < end:
        time.sleep(0.2)
    assert not list(paths.inbox_dir().glob("*/*__to-s3.jpg"))
    assert any(k.startswith("raw/2025/2025-08/") for k in bucket.list_keys("raw/"))

    # a big file in 8 MB chunks, interrupted halfway
    big = os.urandom(30 * 1024 * 1024)
    url = create(client, big, "big-video.bin")
    chunk = 8 * 1024 * 1024
    offset = 0
    while offset < len(big):
        if offset == 2 * chunk:
            offset = int(client.head(url).headers["upload-offset"])   # resume after a "drop"
        r = patch(client, url, offset, big[offset:offset + chunk])
        offset = int(r.headers["upload-offset"])
    assert offset == len(big)
    end = time.time() + 30
    while time.time() < end and not any(bucket.head(k)["ContentLength"] == len(big) for k in bucket.list_keys("raw/")):
        time.sleep(0.3)
    assert any(bucket.head(k)["ContentLength"] == len(big) for k in bucket.list_keys("raw/"))


def test_inbox_never_misses_a_file_that_lands_while_it_is_stopping(authed, monkeypatch):
    """Regression: a kick arriving as the processor decided the inbox was empty used to be lost."""
    import threading

    client, app = authed
    inbox = app.state.inbox
    real_pending = inbox.pending
    calls = {"n": 0}
    arrived = threading.Event()

    def pending_with_a_late_arrival(**kw):
        calls["n"] += 1
        if calls["n"] == 1:                      # first look: empty… and a file lands right then
            threading.Thread(target=lambda: (inbox.kick(), arrived.set())).start()
            arrived.wait(2)
            return []
        return real_pending(**kw)
    monkeypatch.setattr(inbox, "pending", pending_with_a_late_arrival)
    data = jpeg_with_exif()
    url = create(client, data, "late.jpg")
    patch(client, url, 0, data)
    wait_archived(client, 1)
