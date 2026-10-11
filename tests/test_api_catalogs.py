import gzip
import io
import sqlite3
import zipfile

import pytest

from apikit import seed
from opkit import record
from photoctl.catalog import open_catalog
from photoctl.catalog.store import upsert_record
from photoctl.device import Device
from photoctl.publish import publish_catalog

PIXEL = Device("01PIXELPIXELPIXELPIXELPIXE", "Pixel 8", "android", "x")


@pytest.fixture
def authed(api, sidecar, device):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    return client, seed(sidecar, device, app.state.settings.library, n=2), app


def as_db(tmp_path, data, name="dl.sqlite"):
    p = tmp_path / name
    p.write_bytes(gzip.decompress(data))
    return sqlite3.connect(p)


def test_library_download_with_cursor_resume_and_cache(authed, tmp_path):
    client, ids, _ = authed
    r = client.get("/api/catalogs/library")
    assert r.status_code == 200 and int(r.headers["x-ops-cursor"]) > 0 and r.headers["x-catalog-epoch"]
    assert as_db(tmp_path, r.content).execute("SELECT COUNT(*) FROM media").fetchone()[0] == 2
    half = len(r.content) // 2
    part = client.get("/api/catalogs/library", headers={"Range": f"bytes={half}-"})
    assert part.status_code == 206 and r.content[:half] + part.content == r.content    # resumable
    # cursor + incremental pull = everything
    nxt = client.get("/api/sync/ops", params={"since": r.headers["x-ops-cursor"],
                                               "epoch": r.headers["x-catalog-epoch"]}).json()
    assert nxt["ops"] == []
    client.patch(f"/api/media/{ids[0]}", json={"title": "new"})
    again = client.get("/api/catalogs/library")
    assert int(again.headers["x-ops-cursor"]) > int(r.headers["x-ops-cursor"])


def test_contributed_published_and_zip(authed, bucket, tmp_path, sidecar):
    client, ids, app = authed
    app.state.s3 = bucket
    conn = open_catalog()
    upsert_record(conn, record(sidecar, 9), PIXEL)
    publish_catalog(conn, bucket, PIXEL, force=True)
    listing = client.get("/api/catalogs").json()
    assert listing["library"]["item_count"] == 3 and listing["devices"][0]["platform"] == "android"
    contributed = client.get(f"/api/catalogs/{PIXEL.device_id}", params={"flavour": "contributed"})
    assert as_db(tmp_path, contributed.content).execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
    full = client.get(f"/api/catalogs/{PIXEL.device_id}")
    assert as_db(tmp_path, full.content, "f.sqlite").execute("SELECT COUNT(*) FROM media").fetchone()[0] == 3
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/catalogs/all.zip").content))
    names = z.namelist()
    assert "library.sqlite.gz" in names and any("Pixel 8 (android)" in n for n in names)
    assert client.get("/api/catalogs/NOPE", params={"flavour": "contributed"}).status_code == 404


def test_phone_uploads_its_catalog(authed, bucket, tmp_path, sidecar):
    client, _, app = authed
    app.state.s3 = bucket
    phone_db = open_catalog(tmp_path / "phone.sqlite")
    upsert_record(phone_db, record(sidecar, 7), PIXEL)
    phone_db.close()
    body = gzip.compress((tmp_path / "phone.sqlite").read_bytes())
    assert client.put(f"/api/catalogs/{PIXEL.device_id}", content=body).status_code == 404   # not registered
    client.post("/api/devices/register", json={"device_id": PIXEL.device_id, "name": "Pixel 8", "platform": "android"})
    m = client.put(f"/api/catalogs/{PIXEL.device_id}", content=body).json()
    assert m["item_count"] == 1 and m["contributed_count"] == 1 and m["platform"] == "android"
    assert client.put(f"/api/catalogs/{PIXEL.device_id}", content=b"junk").status_code == 422
