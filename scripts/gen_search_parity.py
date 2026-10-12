"""Write spec/search_parity.json: a small catalog (as INSERTs) plus what 20 searches return in Python.
The TypeScript on-device search (web/src/local/query.ts) must return exactly the same ids, in order."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["PHOTOCTL_HOME"] = tempfile.mkdtemp(prefix="photoctl-parity-")

from conftest import SIDECAR  # noqa: E402
from opkit import record  # noqa: E402
from photoctl.catalog import open_catalog  # noqa: E402
from photoctl.catalog.ops import Emitter  # noqa: E402
from photoctl.catalog.query import search  # noqa: E402
from photoctl.catalog.store import upsert_record  # noqa: E402
from photoctl.device import Device  # noqa: E402

MAC = Device("01MACMACMACMACMACMACMACMAC", "Mac", "macos", "x")
PIXEL = Device("01PIXELPIXELPIXELPIXELPIXE", "Pixel 8", "android", "x")
PLACES = [("Gokarna", "Karnataka"), ("Palolem", "Goa"), ("Udaipur", "Rajasthan"), ("Munnar", "Kerala"), ("Delhi", "Delhi")]
LABELS = [["Beach", "Sea"], ["Lake", "Boat"], ["Hills", "Tea"], ["Lights", "Night"], ["Cake"]]

QUERIES = [
    {}, {"q": "beach"}, {"q": "sea gok"}, {"q": "december 2024"}, {"q": "lights november"}, {"q": "videos"},
    {"q": "photos 2023"}, {"person": "asha"}, {"person": "kid"}, {"place": "goa"}, {"tag": "boat"},
    {"album": "holidays"}, {"device": "android"}, {"device": "Pixel 8"}, {"fav": True}, {"hidden": True},
    {"from": "2024-06-01", "to": "2024-12-31"}, {"q": "café"}, {"q": 'tea "hills'}, {"limit": 7},
]


def _data_sql(conn: sqlite3.Connection) -> str:
    """INSERTs for every row (incl. the FTS table); the schema comes from the shared migrations."""
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE "
                                         "'sqlite_%' AND name NOT LIKE 'media_fts_%' AND name != 'meta'")]
    out = []
    for t in tables:
        cur = conn.execute(f"SELECT * FROM {t}")
        cols = [d[0] for d in cur.description]
        for row in cur:
            vals = ", ".join("NULL" if v is None else str(v) if isinstance(v, (int, float)) else
                             "'" + str(v).replace("'", "''") + "'" for v in row)
            out.append(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({vals});")
    return "\n".join(out)


def main() -> None:
    path = Path(os.environ["PHOTOCTL_HOME"]) / "parity.sqlite"
    conn = open_catalog(path)
    for n in range(40):
        city, region = PLACES[n % len(PLACES)]
        rec = record(SIDECAR, n, labels=LABELS[n % len(LABELS)], keywords=[], favorite=n % 6 == 0, title=None,
                     persons=[["Asha"], ["Kid"], [], ["Asha", "Kid"]][n % 4], albums=["Holidays"] if n % 5 == 0 else [],
                     place={"name": f"{city}, {region}", "address": {"city": city, "state_province": region,
                                                                     "country": "India"}})
        rec["taken_at"] = f"{2023 + n % 3}-{1 + (n * 5) % 12:02d}-{1 + n % 27:02d}T10:{n:02d}:00+05:30"
        rec["media_type"] = "video" if n % 7 == 3 else "photo"
        if n == 13:
            rec["apple"]["title"] = "Café by the sea"
        upsert_record(conn, rec, PIXEL if n % 3 == 0 else MAC)
    em = Emitter(conn, MAC.device_id)
    em.emit("media.set", {"photo_id": record(SIDECAR, 4)["photo_id"], "field": "hidden", "value": True})
    em.emit("media.delete", {"photo_id": record(SIDECAR, 8)["photo_id"]})

    cases = []
    for q in QUERIES:
        kwargs = {"text": q.get("q"), "person": q.get("person"), "place": q.get("place"), "tag": q.get("tag"),
                  "album": q.get("album"), "date_from": q.get("from"), "date_to": q.get("to"),
                  "favorite": q.get("fav"), "device": q.get("device"), "include_hidden": q.get("hidden", False),
                  "limit": q.get("limit", 60)}
        res = search(conn, **kwargs)
        cases.append({"params": q, "ids": [r["photo_id"] for r in res["items"]], "has_next": res["next"] is not None})
    dump = _data_sql(conn)
    conn.close()
    out = ROOT / "spec" / "search_parity.json"
    out.write_text(json.dumps({"description": __doc__.strip(), "catalog_sql": dump, "cases": cases}, ensure_ascii=False) + "\n")
    print(f"wrote {len(cases)} cases ({sum(len(c['ids']) for c in cases)} ids) to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
