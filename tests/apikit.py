"""Seed a catalog + local library for API tests."""

from conftest import make_jpeg
from opkit import record
from photoctl.catalog import open_catalog
from photoctl.catalog.store import upsert_record


def seed(sidecar, device, library, n=3):
    conn = open_catalog()
    ids = []
    for i in range(1, n + 1):
        rec = record(sidecar, i, labels=["Beach"] if i % 2 else ["Hills"], persons=["Asha"] if i == 1 else [],
                     favorite=False, title=None, keywords=[], albums=[])
        rec["taken_at"] = f"2025-08-{10 + i:02d}T10:00:00+05:30"
        upsert_record(conn, rec, device)
        make_jpeg(library / rec["keys"]["thumb"], (320, 240))
        make_jpeg(library / rec["keys"]["compressed"], (1600, 1200))
        ids.append(rec["photo_id"])
    conn.close()
    return ids
