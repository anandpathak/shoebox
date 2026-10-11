import pytest

from apikit import seed


@pytest.fixture
def authed(api, sidecar, device):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    return client, seed(sidecar, device, app.state.settings.library), app


def outbox_kinds(conn_factory=None):
    from photoctl.catalog import open_catalog
    conn = open_catalog()
    return [r[0] for r in conn.execute("SELECT o.kind FROM outbox x JOIN ops o USING (op_id) ORDER BY o.hlc")]


def test_patch_fields_emit_ops(authed):
    client, ids, _ = authed
    before = len(outbox_kinds())
    d = client.patch(f"/api/media/{ids[0]}", json={"title": "Kudle", "favorite": True}).json()
    assert d["title"] == "Kudle" and d["favorite"] == 1
    assert outbox_kinds()[before:] == ["media.set", "media.set"]
    assert client.patch(f"/api/media/{ids[0]}", json={}).status_code == 422
    assert client.patch("/api/media/NOPE", json={"title": "x"}).status_code == 404


def test_tags_albums_add_remove(authed):
    client, ids, _ = authed
    assert "night" in client.post(f"/api/media/{ids[0]}/tags", json={"value": "Night"}).json()["tags"]
    assert "night" not in client.delete(f"/api/media/{ids[0]}/tags/Night").json()["tags"]
    assert "beach" not in client.delete(f"/api/media/{ids[0]}/tags/beach").json()["tags"]   # imported label
    assert client.post(f"/api/media/{ids[0]}/albums", json={"value": "Best"}).json()["albums"] == ["Best"]
    assert client.delete(f"/api/media/{ids[0]}/albums/Best").json()["albums"] == []


def test_people_add_rename_merge_remove(authed):
    client, ids, _ = authed
    d = client.post(f"/api/media/{ids[1]}/people", json={"name": "Kid"}).json()
    kid = next(p for p in d["people"] if p["name"] == "Kid")["person_id"]
    asha = client.get("/api/people").json()
    asha = next(p for p in asha if p["name"] == "Asha")["person_id"]
    assert client.patch(f"/api/people/{kid}", json={"name": "Little one"}).json()["name"] == "Little one"
    assert client.post(f"/api/people/{asha}/merge", json={"into": kid}).status_code == 200
    assert client.post(f"/api/people/{asha}/merge", json={"into": kid}).status_code == 409
    assert [p["name"] for p in client.get(f"/api/media/{ids[0]}").json()["people"]] == ["Little one"]
    assert client.get("/api/search", params={"person": "little one"}).json()["items"]
    left = client.delete(f"/api/media/{ids[0]}/people/{kid}").json()["people"]
    assert left == []


def test_delete_and_restore(authed):
    client, ids, _ = authed
    client.post(f"/api/media/{ids[0]}/delete")
    assert ids[0] not in [i["photo_id"] for i in client.get("/api/search").json()["items"]]
    assert client.post(f"/api/media/{ids[0]}/restore").json()["deleted"] == 0


def test_writes_from_the_browser_need_same_origin(api, sidecar, device):
    client, _, app = api
    ids = seed(sidecar, device, app.state.settings.library, n=1)
    client.get("/")  # cookie
    assert client.patch(f"/api/media/{ids[0]}", json={"title": "x"}).status_code == 403
    ok = client.patch(f"/api/media/{ids[0]}", json={"title": "x"}, headers={"Origin": "http://127.0.0.1:8765"})
    assert ok.status_code == 200


def test_api_edit_reaches_another_device(authed, bucket, tmp_path, device):
    from photoctl.catalog import open_catalog
    from photoctl.device import Device
    from photoctl.sync import pull, sync_once

    client, ids, _ = authed
    client.patch(f"/api/media/{ids[0]}", json={"title": "From the app"})
    sync_once(open_catalog(), bucket, device)
    other = open_catalog(tmp_path / "b.sqlite")
    pull(other, bucket, Device("01BBBBBBBBBBBBBBBBBBBBBBBB", "B", "macos", "x").device_id)
    assert other.execute("SELECT title FROM media WHERE photo_id = ?", (ids[0],)).fetchone()[0] == "From the app"
