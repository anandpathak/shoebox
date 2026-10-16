"""Place names from coordinates (offline), for photos Apple Photos didn't name."""

import json

import pytest

from shoebox import places
from shoebox.places import Index, backfill, fill_place, lookup

ROWS = [  # lat, lon, town, region, country, population
    (14.5479, 74.3188, "Gokarna", "Karnataka", "India", 25_000),
    (14.4253, 74.4189, "Kumta", "Karnataka", "India", 30_000),
    (51.5085, -0.1257, "London", "England", "United Kingdom", 8_900_000),
    (51.4975, -0.1357, "City of Westminster", "England", "United Kingdom", 250_000),
    (40.7143, -74.0060, "New York City", "New York", "United States", 8_800_000),
    (40.7685, -74.0204, "Weehawken", "New Jersey", "United States", 14_000),
]


@pytest.fixture
def geo():
    places.use_index(Index.build(ROWS))
    yield
    places.use_index(None)


def test_nearest_town_and_big_cities(geo):
    assert lookup(14.5339, 74.3169)["name"] == "Gokarna, Karnataka"
    times_square = lookup(40.7580, -73.9855)
    assert times_square["address"]["city"] == "New York City"                     # not the town across the river
    westminster = lookup(51.5007, -0.1246)
    assert westminster["name"] == "City of Westminster, London" and westminster["address"]["country"] == "United Kingdom"
    assert lookup(0.0, -30.0) is None and lookup(None, 1) is None and lookup(0, 0) is None


def test_existing_place_names_are_kept(geo):
    rec = {"apple": {"latitude": 14.53, "longitude": 74.32, "place": {"name": "Om Beach"}}}
    assert fill_place(rec) is False and rec["apple"]["place"] == {"name": "Om Beach"}
    rec = {"apple": {"latitude": 14.53, "longitude": 74.32, "place": {}}}
    assert fill_place(rec) and rec["apple"]["place"]["address"]["city"] == "Gokarna"


def test_uploads_get_a_place_name(api, geo):
    import hashlib
    import time

    from test_api_tus import jpeg_with_exif

    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    client.post("/api/devices/register", json={"device_id": "01PLACEPHONE", "name": "P", "platform": "android"})
    data = jpeg_with_exif()                                                           # GPS near Gokarna
    client.post("/api/uploads/raw", content=data, headers={"X-Device-Id": "01PLACEPHONE", "X-Asset-Id": "1",
                "X-Filename": "IMG.JPG", "X-Sha256": hashlib.sha256(data).hexdigest()})
    for _ in range(100):
        items = client.get("/api/search", params={"q": "gokarna"}).json()["items"]
        if items:
            break
        time.sleep(0.2)
    assert items, "the upload wasn't found by its place"
    detail = client.get(f"/api/media/{items[0]['photo_id']}").json()
    assert detail["place_city"] == "Gokarna" and detail["place_country"] == "India"


def test_backfill_names_existing_items(geo, sidecar, device, bucket, tmp_path):
    from shoebox.catalog import open_catalog
    from shoebox.catalog.store import upsert_record

    library = tmp_path / "library"
    rec = json.loads(json.dumps(sidecar))
    rec["apple"]["place"] = {}
    rec["apple"]["latitude"], rec["apple"]["longitude"] = 51.5007, -0.1246
    meta = library / rec["keys"]["meta"]
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps(rec))
    conn = open_catalog()
    upsert_record(conn, rec, device)
    sources = conn.execute("SELECT COUNT(*) FROM media_sources").fetchone()[0]
    assert backfill(conn, library, device.device_id, bucket=bucket)["named"] == 1
    row = conn.execute("SELECT place_city, place_locality FROM media WHERE photo_id = ?", (rec["photo_id"],)).fetchone()
    assert tuple(row) == ("London", "City of Westminster")
    assert conn.execute("SELECT COUNT(*) FROM media_sources").fetchone()[0] == sources    # nobody newly credited
    assert json.loads(meta.read_text())["apple"]["place"]["address"]["city"] == "London"
    in_s3 = json.loads(bucket.client.get_object(Bucket=bucket.name, Key=rec["keys"]["meta"])["Body"].read())
    assert in_s3["apple"]["place"]["source"] == "geonames"
    assert backfill(conn, library, device.device_id)["named"] == 0                     # done once
