"""Seed a throwaway library with synthetic photos and run `shoebox serve` on it (for browser tests/demos).

    uv run python scripts/e2e_server.py --port 8799 --items 300

Nothing here touches ~/.shoebox or real S3: it uses a temp SHOEBOX_HOME, a temp library folder and an
in-process mock S3 (moto) with tiny stand-in originals in Deep Archive. --no-s3 runs without any bucket.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

PLACES = [("Gokarna", "Karnataka"), ("Bengaluru", "Karnataka"), ("Palolem", "Goa"), ("Udaipur", "Rajasthan"),
          ("Mumbai", "Maharashtra"), ("Munnar", "Kerala"), ("Gulmarg", "Kashmir"), ("Hampi", "Karnataka")]
LABELS = [["Beach", "Sea"], ["Cake", "Party"], ["Lake", "Boat"], ["Lights", "Night"], ["Hills", "Tea"],
          ["Snow", "Mountains"], ["Ruins", "Rocks"], ["Rain", "Window"]]
PEOPLE = ["Asha", "Kid", "Grandma", None, None]
PALETTES = [((246, 181, 106), (200, 85, 61)), ((159, 211, 230), (29, 61, 85)), ((207, 229, 154), (31, 64, 39)),
            ((240, 192, 96), (13, 15, 36)), ((255, 255, 255), (91, 117, 144)), ((243, 197, 139), (75, 42, 26))]


def photo_id(n: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    return "".join(alphabet[b % 32] for b in hashlib.sha256(f"e2e{n}".encode()).digest()[:26])


def gradient(path: Path, size: tuple[int, int], colors) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    top, bottom = colors
    img = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        img.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    img.resize((w, h)).save(path, "JPEG", quality=70)


def seed(library: Path, items: int, seed_value: int = 7) -> None:
    from shoebox.catalog import open_catalog
    from shoebox.catalog.store import upsert_record
    from shoebox.device import Device, load_device

    rng = random.Random(seed_value)
    mac = load_device()
    devices = [mac, Device("01E2EIPHONE000000000000000", "My iPhone", "ios", "x"),
               Device("01E2EPIXEL0000000000000000", "Pixel 8", "android", "x")]
    conn = open_catalog()
    thumbs: dict[int, Path] = {}
    for n in range(items):
        pid = photo_id(n)
        year = 2025 - (n * 3 // max(items, 1))
        month = 12 - (n * 37 // max(items, 1)) % 12
        day = 1 + n % 27
        city, region = PLACES[n % len(PLACES)]
        is_video = n % 11 == 5
        part = f"{year}/{year}-{month:02d}"
        keys = {"raw": f"raw/{part}/{pid}.{'mov' if is_video else 'heic'}",
                "compressed": f"compressed/{part}/{pid}.{'mp4' if is_video else 'jpg'}",
                "poster": f"compressed/{part}/{pid}.jpg" if is_video else None,
                "thumb": f"thumb/{part}/{pid}.jpg", "meta": f"meta/{part}/{pid}.json"}
        person = PEOPLE[n % len(PEOPLE)]
        rec = {"schema_version": 1, "photo_id": pid, "media_type": "video" if is_video else "photo", "keys": keys,
               "file": {"sha256": hashlib.sha256(pid.encode()).hexdigest(), "orig_name": f"IMG_{1000 + n}.HEIC",
                        "orig_ext": "mov" if is_video else "heic", "bytes_raw": rng.randint(1_500_000, 4_000_000),
                        "width": 4032, "height": 3024},
               "taken_at": f"{year}-{month:02d}-{day:02d}T{8 + n % 12:02d}:{n % 60:02d}:00+05:30",
               "source": {"type": "applephotos", "ref": f"E2E-{n}"},
               "apple": {"title": None, "description": None, "favorite": n % 9 == 0, "hidden": False,
                         "keywords": [], "labels": LABELS[n % len(LABELS)], "albums": ["Holidays"] if n % 5 == 0 else [],
                         "persons": [person] if person else [], "latitude": 12 + n % 10, "longitude": 74 + n % 5,
                         "exif_info": {"camera_make": "Apple", "camera_model": "iPhone 15",
                                       "duration": 12.0 + n % 30 if is_video else None, "tzname": "GMT+0530"},
                         "place": {"name": f"{city}, {region}", "address": {"city": city, "state_province": region,
                                                                            "country": "India"}}}}
        upsert_record(conn, rec, devices[n % 3] if n % 7 else devices[0], bytes_compressed=rng.randint(200_000, 500_000))
        colors = PALETTES[n % len(PALETTES)]
        thumb = library / keys["thumb"]
        if n < 60 or n % len(PALETTES) not in thumbs:
            gradient(thumb, (320, 240), colors)
            thumbs.setdefault(n % len(PALETTES), thumb)
        else:  # reuse a few rendered images so big seeds stay fast
            thumb.parent.mkdir(parents=True, exist_ok=True)
            thumb.write_bytes(thumbs[n % len(PALETTES)].read_bytes())
        for key in filter(None, (keys["poster"], keys["compressed"])):
            target = library / key
            if not target.exists():  # stand-in bytes: enough for downloads and layout tests
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(thumb.read_bytes())
    conn.close()


def seed_faces(items: int) -> None:
    """Two made-up people's face fingerprints on some seeded photos (no real faces), so the People screen
    has groups to name. Stand-ins only: random vectors, boxes in the middle of the picture."""
    import numpy as np

    from shoebox.analyze.faces import Face
    from shoebox.analyze.facedb import add_faces, assign, open_faces
    from shoebox.catalog import open_catalog

    rng = np.random.default_rng(11)
    people = [rng.normal(size=128) for _ in range(2)]
    conn, fdb = open_catalog(), open_faces()
    for n in range(0, min(items, 40), 4):
        base = people[(n // 4) % 2]
        v = (base + rng.normal(scale=0.01, size=128)).astype(np.float32)
        ids = add_faces(fdb, photo_id(n), [Face((0.35, 0.25, 0.3, 0.4), 0.95, v / np.linalg.norm(v))])
        assign(fdb, conn, ids)
    conn.close()


class FakeEmbedder:
    """Stand-in for SigLIP 2 in browser tests: a word → a fixed random vector; a photo → the sum of its labels."""

    import numpy as _np

    def word(self, w: str):
        import hashlib

        import numpy as np

        seed = int.from_bytes(hashlib.sha256(w.lower().encode()).digest()[:8], "big")
        v = np.random.default_rng(seed).normal(size=256)
        return v / np.linalg.norm(v)

    def vec(self, words):
        import numpy as np

        v = sum((self.word(w) for w in words), np.zeros(256))
        n = np.linalg.norm(v)
        return (v / n if n else v).astype(np.float32)

    def text(self, q: str):
        return self.vec(q.split())

    def probability(self, cos):
        import numpy as np

        return 1 / (1 + np.exp(-(cos * 20 - 14)))     # ~cos 0.35 to count as a match


def fake_ai(items: int) -> None:
    """Plain-English search and captions without downloading models: stand-in vectors and a few captions."""
    from shoebox.analyze import ai
    from shoebox.analyze.vectors import open_vectors, save
    from shoebox.catalog import open_catalog
    from shoebox.catalog.ops import Emitter
    from shoebox.device import load_device

    emb = FakeEmbedder()
    ai.available = lambda: True
    ai.embedder = lambda: emb
    model = ai.settings()["search_model"]
    conn, vdb = open_catalog(), open_vectors()
    if vdb.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 0:
        rows = conn.execute("SELECT m.photo_id, group_concat(t.tag, ' ') FROM media m "
                            "LEFT JOIN media_tags t USING (photo_id) GROUP BY m.photo_id").fetchall()
        save(vdb, model, [(pid, emb.vec((tags or "").split())) for pid, tags in rows])
        em = Emitter(conn, load_device().device_id)
        em.emit_many([("media.caption", {"photo_id": photo_id(n), "caption": f"Palm trees by a quiet lagoon, picture {n}",
                                         "model": "fake"}) for n in range(0, min(items, 30), 10)])
    conn.close()


def fake_photos_library(home: Path, n: int = 8) -> None:
    """Replace Apple Photos with a few generated photos so 'Archive from Apple Photos' works in tests."""
    import json
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from shoebox import archiver

    folder = home / "fake-photos"
    items = []
    for i in range(n):
        f = folder / f"IMG_{9000 + i}.jpg"
        if not f.exists():
            gradient(f, (1200 + i, 900), PALETTES[i % len(PALETTES)])  # unique bytes per photo
        apple = json.dumps({"labels": ["Garden"], "persons": [], "albums": ["Fake album"], "keywords": [],
                            "place": {"name": "Bengaluru", "address": {"city": "Bengaluru", "country": "India"}}})
        items.append(SimpleNamespace(uuid=f"FAKE-{i}", path=str(f), date=datetime(2026, 9, 1 + i, 9, tzinfo=timezone.utc),
                                     original_filename=f.name, width=1200, height=900, uti_original="public.jpeg",
                                     ismovie=False, has_raw=False, path_raw=None, live_photo=False, path_live_photo=None,
                                     intrash=False, hidden=False, json=lambda a=apple: a))
    archiver.select_photos = lambda sel: items
    archiver.add_to_album = lambda *a, **k: None
    real = archiver.process
    delay = float(os.environ.get("E2E_ITEM_DELAY", "0.4"))   # slow enough for tests to see a run in progress

    def slow(item, opts):
        import time

        time.sleep(delay)
        return real(item, opts)
    archiver.process = slow


def start_mock_s3(home: Path, upload_originals: bool) -> str:
    """Mock S3 on a local port; originals uploaded as tiny Deep Archive objects so restores work."""
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    os.environ.update({"AWS_ENDPOINT_URL": f"http://{host}:{port}", "AWS_ACCESS_KEY_ID": "e2e",
                       "AWS_SECRET_ACCESS_KEY": "e2e", "AWS_DEFAULT_REGION": "us-east-1"})
    os.environ.pop("AWS_PROFILE", None)
    from shoebox.catalog import open_catalog
    from shoebox.s3 import Bucket

    name = "e2e-archive"
    b = Bucket(name)
    b.client.create_bucket(Bucket=name)
    if upload_originals:
        conn = open_catalog()
        for (raw,) in conn.execute("SELECT raw_key FROM media"):
            b.client.put_object(Bucket=name, Key=raw, Body=b"original", StorageClass="DEEP_ARCHIVE")
        publish_phone_catalogs(conn, b)
        publish_for_viewer(conn, b, home / "library")
        conn.close()
    return name


def publish_for_viewer(conn, bucket, library: Path) -> None:
    """What phones browse without the Mac: thumbnails and small copies in S3, the viewer library file, and a
    stand-in read-only key (the local S3 doesn't check signatures; sigv4.test.ts checks them against AWS)."""
    from shoebox.device import load_device
    from shoebox.viewer import publish_viewer, save_access

    for thumb, comp in conn.execute("SELECT thumb_key, compressed_key FROM media"):
        for key in filter(None, (thumb, comp)):
            f = library / key
            if f.exists():
                bucket.client.put_object(Bucket=bucket.name, Key=key, Body=f.read_bytes(), ContentType="image/jpeg")
    publish_viewer(conn, bucket, load_device(), force=True)
    save_access("e2e-reader", "e2e-reader-secret")


def publish_phone_catalogs(conn, bucket) -> None:
    """As if the seeded iPhone and Pixel had shared their catalogs: each one's own photos."""
    import json
    from datetime import datetime, timezone

    from shoebox.catalog.export import export_catalog
    from shoebox.publish import catalog_key, manifest_key

    for device_id, dev_name, platform in (("01E2EIPHONE000000000000000", "My iPhone", "ios"),
                                          ("01E2EPIXEL0000000000000000", "Pixel 8", "android")):
        out = Path(tempfile.mkdtemp()) / "c.sqlite.gz"
        export_catalog(conn, out, flavour="contributed", device_id=device_id, strip_ops=True)
        n = conn.execute("SELECT COUNT(DISTINCT photo_id) FROM media_sources WHERE device_id = ?", (device_id,)).fetchone()[0]
        bucket.put_bytes(catalog_key(device_id), out.read_bytes(), content_type="application/gzip")
        bucket.put_bytes(manifest_key(device_id), json.dumps({
            "device_id": device_id, "name": dev_name, "platform": platform, "schema_version": 3,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "max_hlc": "", "checksum": "",
            "bytes": out.stat().st_size, "item_count": n, "contributed_count": n}).encode(), content_type="application/json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--items", type=int, default=300)
    ap.add_argument("--home", help="Reuse this SHOEBOX_HOME (default: a new temp dir)")
    ap.add_argument("--no-s3", action="store_true", help="Run without a (mock) bucket")
    args = ap.parse_args()
    home = Path(args.home or tempfile.mkdtemp(prefix="shoebox-e2e-"))
    os.environ["SHOEBOX_HOME"] = str(home)
    os.environ["SHOEBOX_TOKEN_STORE"] = "file"
    os.environ.setdefault("SHOEBOX_ANALYSIS", "0")   # seeded stand-in images: don't label them or fetch face models
    library = home / "library"
    fake_photos_library(home)  # never read the real Photos library from tests or demos
    fresh = not (home / "catalog.sqlite").exists()
    if fresh:
        seed(library, args.items)
        seed_faces(args.items)
    bucket = None if args.no_s3 else start_mock_s3(home, upload_originals=fresh)
    fake_ai(args.items)

    import uvicorn

    from shoebox.server.app import create_app
    from shoebox.server.config import Settings

    dist = ROOT / "src" / "shoebox" / "web" / "dist"
    app = create_app(Settings(library=library, port=args.port, static_dir=dist, sync_interval=0,
                              bucket=bucket, allowed_logins=["phone-user@example.com"]))
    print(f"e2e server: http://127.0.0.1:{args.port}  home={home}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
