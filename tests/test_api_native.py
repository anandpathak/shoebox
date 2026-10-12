import hashlib
import time

import pytest

from test_api_tus import jpeg_with_exif

PHONE = "01NATIVEIPHONEAAAAAAAAAAAA"


@pytest.fixture
def authed(api):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    client.post("/api/devices/register", json={"device_id": PHONE, "name": "iPhone", "platform": "ios"})
    return client, app


def send(client, data, asset="ABC-123/L0/001", sha=None, device=PHONE):
    return client.post("/api/uploads/raw", content=data, headers={
        "X-Device-Id": device, "X-Asset-Id": asset, "X-Filename": "IMG_0001.JPG",
        "X-Sha256": sha if sha is not None else hashlib.sha256(data).hexdigest(), "Content-Type": "image/jpeg"})


def wait(client, n=1, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get("/api/backup/status", params={"device": PHONE}).json()
        if st["archived"] >= n and st["waiting_on_mac"] == 0:   # the inbox is tidied just after the run
            return
        time.sleep(0.2)
    raise AssertionError("not archived")


def test_native_upload_is_archived_with_the_asset_as_source(authed):
    client, _ = authed
    data = jpeg_with_exif()
    assert send(client, data).json()["status"] == "received"
    wait(client)
    item = client.get("/api/search", params={"device": PHONE}).json()["items"][0]
    detail = client.get(f"/api/media/{item['photo_id']}").json()
    assert detail["sources"][0]["source_ref"] == "ios:ABC-123/L0/001"
    st = client.get("/api/backup/status", params={"device": PHONE}).json()
    assert st == {**st, "archived": 1, "waiting_on_mac": 0}
    # sending it again is answered without storing anything
    assert send(client, data).json() == {"status": "already_archived"}


def test_checks(authed):
    client, _ = authed
    data = jpeg_with_exif()
    assert send(client, data, sha="0" * 64).status_code == 422                  # corrupted in transit
    assert send(client, data, device="01UNREGISTERED").status_code == 404
    assert send(client, data, asset="").status_code == 400
    assert send(client, b"", sha="").status_code == 422
