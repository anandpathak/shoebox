"""Naming people from their faces through the API; the names become searchable."""

import numpy as np

from conftest import make_jpeg
from opkit import record
from photoctl.analyze.faces import Face
from photoctl.analyze.facedb import open_faces
from photoctl.analyze.run import analyze_pending
from photoctl.catalog import open_catalog
from photoctl.catalog.store import upsert_record

rng = np.random.default_rng(3)


def vec():
    v = rng.normal(size=128).astype(np.float32)
    return v / np.linalg.norm(v)


def like(v):
    w = v + rng.normal(scale=0.02, size=128).astype(np.float32)
    return (w / np.linalg.norm(w)).astype(np.float32)


def seed(app, sidecar, device, people):
    """Photos with one face each; people = list of embeddings. Analysed with stand-in engines."""
    library = app.state.settings.library
    conn = open_catalog()
    by_photo = {}
    for i, emb in enumerate(people):
        rec = record(sidecar, 100 + i, persons=[], labels=[])
        upsert_record(conn, rec, device)
        make_jpeg(library / rec["keys"]["compressed"], size=(800, 600))
        by_photo[rec["photo_id"]] = emb

    from photoctl.analyze import image, run

    real = image.load

    def load(path):
        im = real(path)
        im.info["pid"] = path.stem
        return im
    run.load = load
    try:
        analyze_pending(conn, library, device.device_id, fdb=open_faces(),
                        classify=lambda im: [("beach", 0.9)],
                        find_faces=lambda im: [Face((0.4, 0.3, 0.2, 0.3), 0.95, by_photo[im.info["pid"]])])
    finally:
        run.load = real
    return list(by_photo)


def test_name_a_group_and_search_for_them(api, sidecar, device):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    a, b = vec(), vec()
    photos = seed(app, sidecar, device, [like(a), like(a), like(a), like(b)])
    st = client.get("/api/analysis/status").json()
    assert st["analyzed"] == 4 and st["unnamed_groups"] == 2 and st["enabled"] is False
    groups = client.get("/api/faces/groups").json()
    assert [g["faces"] for g in groups] == [3, 1]
    img = client.get(f"/api/faces/{groups[0]['sample'][0]}.jpg")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"

    r = client.post("/api/faces/name", json={"cluster": groups[0]["cluster"], "name": "Asha"}).json()
    assert r["named"] == 3
    found = client.get("/api/search", params={"q": "asha"}).json()["items"]
    assert sorted(i["photo_id"] for i in found) == sorted(photos[:3])
    assert len(client.get("/api/search", params={"q": "beach"}).json()["items"]) >= 4      # labels searchable too
    assert [g["faces"] for g in client.get("/api/faces/groups").json()] == [1]

    faces = client.get(f"/api/media/{photos[0]}/faces").json()
    assert faces[0]["name"] == "Asha" and faces[0]["status"] == "confirmed"
    client.post(f"/api/faces/{faces[0]['face_id']}/not-them")
    assert photos[0] not in [i["photo_id"] for i in client.get("/api/search", params={"q": "asha"}).json()["items"]]

    rest = client.get("/api/faces/groups").json()
    assert client.post("/api/faces/ignore", json={"cluster": rest[-1]["cluster"]}).json()["ignored"] >= 1


def test_analysis_can_be_off(api):
    client, token, _ = api
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.post("/api/analysis/run").status_code == 409
