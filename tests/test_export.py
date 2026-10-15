import gzip
import sqlite3

from shoebox.catalog import open_catalog
from shoebox.catalog.export import export_catalog
from shoebox.catalog.store import upsert_record
from shoebox.device import Device


def _two_device_catalog(tmp_path, sidecar, device):
    conn = open_catalog(tmp_path / "c.sqlite")
    upsert_record(conn, sidecar, device)
    other = Device(device_id="01PHONE00000000000000000000"[:26], name="Pixel 8", platform="android", created_at="x")
    second = {**sidecar, "photo_id": "Z" * 26, "file": {**sidecar["file"], "sha256": "cc" * 32},
              "source": {"type": "upload", "ref": "pixel:1"}}
    upsert_record(conn, second, other)
    return conn, other


def test_full_export_is_gzipped_and_valid(tmp_path, sidecar, device):
    conn, _ = _two_device_catalog(tmp_path, sidecar, device)
    out = export_catalog(conn, tmp_path / "full.sqlite.gz")
    raw = tmp_path / "full.sqlite"
    raw.write_bytes(gzip.decompress(out.read_bytes()))
    c = sqlite3.connect(raw)
    assert c.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 2
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_contributed_export_keeps_only_that_device(tmp_path, sidecar, device):
    conn, other = _two_device_catalog(tmp_path, sidecar, device)
    out = export_catalog(conn, tmp_path / "pixel.sqlite", flavour="contributed", device_id=other.device_id)
    c = sqlite3.connect(out)
    assert [r[0] for r in c.execute("SELECT photo_id FROM media")] == ["Z" * 26]
    assert c.execute("SELECT COUNT(*) FROM media_fts").fetchone()[0] == 1
    assert c.execute("SELECT value FROM meta WHERE key = 'export_flavour'").fetchone()[0] == "contributed"
    # the source catalog is untouched
    assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 2
