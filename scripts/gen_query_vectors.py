"""Write spec/query_vectors.json: parse_query/build_fts input → expected output, hand-written.
The Python (photoctl.catalog.query) and TypeScript implementations must both match every case."""

import json
from pathlib import Path

CASES = [
    {"text": "beach", "expect": {"terms": ["beach"], "date_from": None, "date_to": None, "month": None,
                                 "media_type": None}, "fts": '"beach"*'},
    {"text": "Beach  Sunset!", "expect": {"terms": ["beach", "sunset"], "date_from": None, "date_to": None,
                                          "month": None, "media_type": None}, "fts": '"beach"* AND "sunset"*'},
    {"text": "december 2024", "expect": {"terms": [], "date_from": "2024-12-01", "date_to": "2025-01-01",
                                         "month": None, "media_type": None}, "fts": None},
    {"text": "Yahvi Aug 2025 videos", "expect": {"terms": ["yahvi"], "date_from": "2025-08-01", "date_to": "2025-09-01",
                                                 "month": None, "media_type": "video"}, "fts": '"yahvi"*'},
    {"text": "2023", "expect": {"terms": [], "date_from": "2023-01-01", "date_to": "2024-01-01", "month": None,
                                "media_type": None}, "fts": None},
    {"text": "diwali november", "expect": {"terms": ["diwali"], "date_from": None, "date_to": None, "month": "11",
                                           "media_type": None}, "fts": '"diwali"*'},
    {"text": "photos of the sea", "expect": {"terms": ["of", "the", "sea"], "date_from": None, "date_to": None,
                                             "month": None, "media_type": "photo"}, "fts": '"of"* AND "the"* AND "sea"*'},
    {"text": "café \"quoted\" o'neil", "expect": {"terms": ["café", "quoted", "o", "neil"], "date_from": None,
                                                  "date_to": None, "month": None, "media_type": None},
     "fts": '"café"* AND "quoted"* AND "o"* AND "neil"*'},
    {"text": "1999 2005", "expect": {"terms": ["2005"], "date_from": "1999-01-01", "date_to": "2000-01-01",
                                     "month": None, "media_type": None}, "fts": '"2005"*'},
    {"text": "", "expect": {"terms": [], "date_from": None, "date_to": None, "month": None, "media_type": None},
     "fts": None},
    {"text": "sea", "person": "Asha Pathak", "place": "Gok", "tag": "beach", "album": "Goa 2025",
     "expect": {"terms": ["sea"], "date_from": None, "date_to": None, "month": None, "media_type": None},
     "fts": '"sea"* AND people : "asha" AND people : "pathak" AND place : "gok"* AND tags : "beach" AND '
            'albums : "goa" AND albums : "2025"'},
]

out = Path(__file__).resolve().parent.parent / "spec" / "query_vectors.json"
out.write_text(json.dumps({"description": "parse_query(text) and build_fts(terms, person, place, tag, album) "
                                          "must produce exactly these values.", "cases": CASES},
                          indent=2, ensure_ascii=False) + "\n")
print(f"wrote {len(CASES)} cases to {out.name}")
