from photoctl.catalog import SCHEMA_VERSION, open_catalog, schema_version
from photoctl.catalog.store import person_id, stats, upsert_record

TABLES = {"media", "media_sources", "devices", "media_tags", "media_albums", "people", "media_people",
          "tombstones", "media_fts", "ops", "outbox", "sync_cursor", "meta"}


def test_init_is_idempotent(tmp_path):
    path = tmp_path / "c.sqlite"
    conn = open_catalog(path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table')")}
    assert TABLES <= names
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()
    assert schema_version(open_catalog(path)) == SCHEMA_VERSION


def test_sidecar_maps_to_rows(tmp_path, sidecar, device):
    conn = open_catalog(tmp_path / "c.sqlite")
    pid = upsert_record(conn, sidecar, device, bytes_compressed=420_000)
    m = conn.execute("SELECT * FROM media WHERE photo_id = ?", (pid,)).fetchone()
    assert m["media_type"] == "photo" and m["mime"] == "image/heic"
    assert m["taken_at"] == "2025-08-15T11:52:05+05:30" and m["taken_at_source"] == "applephotos"
    assert (m["lat"], m["lon"]) == (14.5, 74.3)
    assert (m["place_city"], m["place_region"], m["place_country"]) == ("Gokarna", "Karnataka", "India")
    assert m["camera_model"] == "iPhone 15" and m["favorite"] == 1 and m["title"] == "Sunset walk"
    assert m["thumb_key"].startswith("thumb/") and m["bytes_compressed"] == 420_000
    tags = {r[0] for r in conn.execute("SELECT tag FROM media_tags WHERE photo_id = ?", (pid,))}
    assert tags == {"holiday", "beach", "sea", "sky"}
    assert [r[0] for r in conn.execute("SELECT album FROM media_albums")] == ["Goa 2025"]
    assert [r[0] for r in conn.execute("SELECT name FROM people")] == ["Asha"]  # _UNKNOWN_ dropped
    src = conn.execute("SELECT device_id, source_ref FROM media_sources").fetchone()
    assert tuple(src) == (device.device_id, "applephotos:00000000-1111-2222-3333-444444444444")
    assert stats(conn)["photos"] == 1 and stats(conn)["devices"] == 1


def test_fts_finds_place_people_tags(tmp_path, sidecar, device):
    conn = open_catalog(tmp_path / "c.sqlite")
    upsert_record(conn, sidecar, device)
    for q in ("gokarna", "asha", "beach", "goa", "sunset", "karnataka"):
        hits = [r[0] for r in conn.execute("SELECT photo_id FROM media_fts WHERE media_fts MATCH ?", (q,))]
        assert hits == [sidecar["photo_id"]], q


def test_reimport_is_noop_and_keeps_user_edits(tmp_path, sidecar, device):
    conn = open_catalog(tmp_path / "c.sqlite")
    upsert_record(conn, sidecar, device)
    with conn:
        conn.execute("UPDATE media SET title = 'My edit' WHERE photo_id = ?", (sidecar["photo_id"],))
    upsert_record(conn, sidecar, device)
    assert conn.execute("SELECT COUNT(*) FROM media_tags").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM media_fts").fetchone()[0] == 1
    assert conn.execute("SELECT title FROM media").fetchone()[0] == "My edit"


def test_video_record(tmp_path, sidecar, device):
    sidecar.update(photo_id="V" * 26, media_type="video")
    sidecar["file"].update(sha256="bb" * 32, orig_ext="mov")
    sidecar["apple"]["exif_info"]["duration"] = 18.07
    sidecar["keys"]["poster"] = "compressed/2025/2025-08/" + "V" * 26 + ".jpg"
    conn = open_catalog(tmp_path / "c.sqlite")
    upsert_record(conn, sidecar, device)
    m = conn.execute("SELECT * FROM media").fetchone()
    assert m["media_type"] == "video" and m["duration_s"] == 18.07 and m["mime"] == "video/quicktime"
    assert m["poster_key"].endswith(".jpg")


def test_person_id_is_stable():
    assert person_id("Asha") == person_id(" asha ")
