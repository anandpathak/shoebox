import sqlite3

from click.testing import CliRunner

from opkit import record
from shoebox import cli
from shoebox.catalog import open_catalog
from shoebox.catalog.store import upsert_record
from shoebox.device import Device
from shoebox.publish import publish_catalog
from shoebox.snapshot import publish_snapshot

IPHONE = Device("01IPHONEIPHONEIPHONEIPHONE", "My iPhone", "ios", "x")
PIXEL = Device("01PIXELPIXELPIXELPIXELPIXE", "Pixel 8", "android", "x")


def count(path):
    return sqlite3.connect(path).execute("SELECT COUNT(*) FROM media").fetchone()[0]


def test_download_every_flavour(bucket, tmp_path, sidecar, monkeypatch):
    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    lib = open_catalog(tmp_path / "lib.sqlite")
    upsert_record(lib, record(sidecar, 1), IPHONE)
    upsert_record(lib, record(sidecar, 2), PIXEL)
    upsert_record(lib, record(sidecar, 3), PIXEL)
    publish_catalog(lib, bucket, IPHONE, force=True)
    publish_catalog(lib, bucket, PIXEL, force=True)
    publish_snapshot(lib, bucket, IPHONE)

    out = tmp_path / "dl"
    res = CliRunner().invoke(cli.main, ["catalog", "download", "--bucket", "b", "-o", str(out)])
    assert res.exit_code == 0, res.output
    assert {p.name for p in out.iterdir()} == {f"{IPHONE.device_id}.sqlite", f"{PIXEL.device_id}.sqlite"}
    assert "Pixel 8 (android)" in res.output
    CliRunner().invoke(cli.main, ["catalog", "download", "--bucket", "b", "--device", PIXEL.device_id,
                                  "--flavour", "contributed", "-o", str(out)])
    assert count(out / f"{PIXEL.device_id}.contributed.sqlite") == 2
    CliRunner().invoke(cli.main, ["catalog", "download", "--bucket", "b", "--device", "library", "-o", str(out)])
    assert count(out / "library.sqlite") == 3
    listing = CliRunner().invoke(cli.main, ["catalog", "list-published", "--bucket", "b"])
    assert "(2 from this device)" in listing.output
