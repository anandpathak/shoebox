"""End to end: `photoctl archive` with fake Photos items, real sips compression and a mocked S3 bucket."""
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from conftest import make_jpeg
from photoctl import cli, paths

pytestmark = pytest.mark.skipif(shutil.which("sips") is None, reason="needs macOS sips")


def fake_photo(path, uuid, day):
    apple = {"title": None, "description": None, "favorite": False, "hidden": False, "ismovie": False,
             "keywords": [], "labels": ["Beach"], "albums": ["Test"], "persons": [], "latitude": None,
             "longitude": None, "exif_info": {"camera_make": "Apple", "camera_model": "iPhone 15"},
             "place": {"name": "Gokarna", "address": {"city": "Gokarna", "country": "India"}}}
    return SimpleNamespace(
        uuid=uuid, path=str(path), date=datetime(2025, 8, day, 10, tzinfo=timezone.utc), original_filename=path.name,
        width=4032, height=3024, uti_original="public.jpeg", ismovie=False, has_raw=False, path_raw=None,
        live_photo=False, path_live_photo=None, intrash=False, hidden=False, json=lambda: json.dumps(apple))


@pytest.fixture
def photos(tmp_path, monkeypatch):
    items = [fake_photo(make_jpeg(tmp_path / f"src/IMG_{i}.jpg", (4032, 3024), (40 * i, 90, 160)), f"UUID-{i}", i)
             for i in range(1, 4)]
    monkeypatch.setattr(cli, "_select_photos", lambda *a, **k: items)
    monkeypatch.setattr(cli, "_add_to_album", lambda *a, **k: None)
    return items


def test_local_only_builds_catalog_next_to_output(tmp_path, photos):
    out = tmp_path / "out"
    res = CliRunner().invoke(cli.main, ["archive", "--all", "--local-only", "--out", str(out)])
    assert res.exit_code == 0, res.output
    conn = sqlite3.connect(out / "catalog.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM media WHERE thumb_key IS NOT NULL").fetchone()[0] == 3
    assert len(list((out / "thumb").rglob("*.jpg"))) == 3
    assert not paths.catalog_db().exists()  # the real catalog stays untouched by test runs


def test_archive_uploads_catalogs_backs_up_and_is_idempotent(tmp_path, photos, bucket, monkeypatch):
    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    out = tmp_path / "lib"
    res = CliRunner().invoke(cli.main, ["archive", "--all", "--bucket", "test-archive", "--out", str(out)])
    assert res.exit_code == 0, res.output
    keys = bucket.list_keys("")
    for prefix in ("raw/2025/2025-08/", "compressed/2025/2025-08/", "thumb/2025/2025-08/", "meta/2025/2025-08/"):
        assert sum(k.startswith(prefix) for k in keys) == 3, prefix
    assert any(k.endswith("/catalog.sqlite.gz") for k in keys if k.startswith("backups/"))
    raw = next(k for k in keys if k.startswith("raw/"))
    assert bucket.client.head_object(Bucket="test-archive", Key=raw)["StorageClass"] == "DEEP_ARCHIVE"

    conn = sqlite3.connect(paths.catalog_db())
    assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM media_fts WHERE media_fts MATCH 'gokarna'").fetchone()[0] == 3

    runs = list((paths.home() / "runs").glob("*.jsonl"))
    events = [json.loads(x)["event"] for x in runs[0].read_text().splitlines()]
    assert events[0] == "start" and events[-1] == "end" and events.count("item") == 3

    again = CliRunner().invoke(cli.main, ["archive", "--all", "--bucket", "test-archive", "--out", str(out),
                                          "--no-backup"])
    assert "3 already archived · 0 to process" in again.output


def test_archive_refuses_while_another_run_holds_the_lock(tmp_path, photos):
    from photoctl.runlock import IngestLock

    with IngestLock():
        res = CliRunner().invoke(cli.main, ["archive", "--all", "--local-only", "--out", str(tmp_path / "o")])
    assert res.exit_code != 0 and "already in progress" in res.output


def test_catalog_rebuilds_from_sidecars_after_loss(tmp_path, photos, bucket, monkeypatch):
    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    out = tmp_path / "lib"
    CliRunner().invoke(cli.main, ["archive", "--all", "--bucket", "test-archive", "--out", str(out), "--no-backup"])
    before = sqlite3.connect(paths.catalog_db()).execute("SELECT COUNT(*) FROM media").fetchone()[0]
    for suffix in ("", "-wal", "-shm"):
        (paths.home() / f"catalog.sqlite{suffix}").unlink(missing_ok=True)

    local = CliRunner().invoke(cli.main, ["catalog", "backfill", "--from-dir", str(out)])
    assert local.exit_code == 0, local.output
    assert sqlite3.connect(paths.catalog_db()).execute("SELECT COUNT(*) FROM media").fetchone()[0] == before == 3

    for suffix in ("", "-wal", "-shm"):
        (paths.home() / f"catalog.sqlite{suffix}").unlink(missing_ok=True)
    remote = CliRunner().invoke(cli.main, ["catalog", "backfill", "--from-s3", "--bucket", "test-archive"])
    assert remote.exit_code == 0, remote.output
    assert sqlite3.connect(paths.catalog_db()).execute("SELECT COUNT(*) FROM media").fetchone()[0] == 3
