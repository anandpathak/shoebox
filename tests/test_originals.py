from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from opkit import record
from photoctl import originals
from photoctl.catalog import open_catalog
from photoctl.catalog.store import upsert_record


def test_parse_restore_header():
    assert originals._parse_restore_header({"StorageClass": "STANDARD"}) == ("available", None)
    assert originals._parse_restore_header({"StorageClass": "DEEP_ARCHIVE"}) == ("not_requested", None)
    assert originals._parse_restore_header({"StorageClass": "DEEP_ARCHIVE",
                                            "Restore": 'ongoing-request="true"'})[0] == "in_progress"
    later = format_datetime(datetime.now(timezone.utc) + timedelta(days=3), usegmt=True)
    st, until = originals._parse_restore_header({"StorageClass": "DEEP_ARCHIVE",
                                                 "Restore": f'ongoing-request="false", expiry-date="{later}"'})
    assert st == "ready" and until > datetime.now(timezone.utc).isoformat()
    past = format_datetime(datetime.now(timezone.utc) - timedelta(days=1), usegmt=True)
    assert originals._parse_restore_header({"StorageClass": "DEEP_ARCHIVE",
                                            "Restore": f'ongoing-request="false", expiry-date="{past}"'})[0] == "expired"


def test_request_records_and_reports(bucket, tmp_path, sidecar, device):
    conn = open_catalog(tmp_path / "c.sqlite")
    rec = record(sidecar, 1)
    upsert_record(conn, rec, device)
    f = tmp_path / "o.heic"
    f.write_bytes(b"original")
    bucket.create_if_absent(f, rec["keys"]["raw"], storage_class="DEEP_ARCHIVE")
    info = originals.request(bucket, conn, rec["photo_id"], tier="bulk", days=3)
    assert info["status"] in ("in_progress", "ready") and info["tier"] == "Bulk" and info["days"] == 3
    again = originals.request(bucket, conn, rec["photo_id"])     # asking twice is harmless
    assert again["requested_at"] == info["requested_at"]
    assert conn.execute("SELECT COUNT(*) FROM restore_requests").fetchone()[0] == 1
    assert originals.status(bucket, conn, rec["photo_id"])["status"] in ("in_progress", "ready")
