"""The read-only library copy for phones, and handing them the read-only key."""

import gzip
import json
import sqlite3

from shoebox.catalog import open_catalog
from shoebox.catalog.ops import Emitter
from shoebox.catalog.store import upsert_record
from shoebox.viewer import HEAD_KEY, VIEWER_KEY, load_access, publish_viewer, save_access


def test_published_when_the_library_changes_at_most_every_15_minutes(bucket, sidecar, device, tmp_path):
    conn = open_catalog()
    upsert_record(conn, sidecar, device)
    t = [1000.0]
    head = publish_viewer(conn, bucket, device, now=lambda: t[0])
    assert head["items"] == 1
    db = tmp_path / "v.sqlite"
    db.write_bytes(gzip.decompress(bucket.client.get_object(Bucket=bucket.name, Key=VIEWER_KEY)["Body"].read()))
    got = sqlite3.connect(db)
    assert got.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
    assert got.execute("SELECT COUNT(*) FROM ops").fetchone()[0] == 0           # no op history for phones
    assert json.loads(bucket.client.get_object(Bucket=bucket.name, Key=HEAD_KEY)["Body"].read())["items"] == 1

    Emitter(conn, device.device_id).emit("media.set", {"photo_id": sidecar["photo_id"], "field": "title", "value": "X"})
    t[0] += 60
    assert publish_viewer(conn, bucket, device, now=lambda: t[0]) is None      # changed, but too soon
    t[0] += 15 * 60
    assert publish_viewer(conn, bucket, device, now=lambda: t[0]) is not None  # now it goes
    t[0] += 3600
    assert publish_viewer(conn, bucket, device, now=lambda: t[0]) is None      # nothing changed since


def test_sync_publishes_it(bucket, sidecar, device):
    from shoebox.sync import full_sync

    conn = open_catalog()
    upsert_record(conn, sidecar, device)
    conn.close()
    assert full_sync(bucket, device).viewer["items"] == 1


def test_phones_get_the_key_from_the_mac(api, bucket):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.get("/api/viewer/access").status_code == 409                  # no bucket on this server
    app.state.settings.bucket = bucket.name
    app.state.s3 = bucket
    assert client.get("/api/viewer/access").status_code == 404                  # no key stored yet
    save_access("AKIA-TEST-ONLY", "s" * 40)
    r = client.get("/api/viewer/access")
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["key_id"] == "AKIA-TEST-ONLY" and body["bucket"] == bucket.name
    assert body["viewer"] == "viewer/library.sqlite.gz" and body["region"] == "us-east-1"
    del client.headers["Authorization"]
    assert client.get("/api/viewer/access").status_code == 401                  # never without signing in


def test_key_is_stored_privately():
    import os

    from shoebox import paths

    save_access("AKIA-TEST-ONLY", "s" * 40)
    assert load_access()["key_id"] == "AKIA-TEST-ONLY"
    assert oct(os.stat(paths.home() / "viewer_access.json").st_mode & 0o777) == "0o600"
