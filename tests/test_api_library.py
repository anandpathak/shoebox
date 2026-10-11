import pytest

from apikit import seed


@pytest.fixture
def authed(api, sidecar, device, tmp_path):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    ids = seed(sidecar, device, app.state.settings.library)
    return client, ids, app


def test_search_and_pagination(authed):
    client, ids, _ = authed
    r = client.get("/api/search", params={"q": "beach"}).json()
    assert [i["photo_id"] for i in r["items"]] == [ids[2], ids[0]]
    page1 = client.get("/api/search", params={"limit": 2}).json()
    page2 = client.get("/api/search", params={"limit": 2, "cursor": page1["next"]}).json()
    assert len(page1["items"]) == 2 and len(page2["items"]) == 1 and page2["next"] is None
    assert client.get("/api/search", params={"person": "asha"}).json()["items"][0]["photo_id"] == ids[0]
    assert client.get("/api/search", params={"type": "gif"}).status_code == 422


def test_detail_people_devices_stats(authed):
    client, ids, app = authed
    d = client.get(f"/api/media/{ids[0]}").json()
    assert d["tags"] == ["beach"] and d["people"][0]["name"] == "Asha"
    assert client.get("/api/media/NOPE").status_code == 404
    assert client.get("/api/people").json()[0] == {"person_id": d["people"][0]["person_id"], "name": "Asha", "photos": 1}
    assert client.get("/api/devices").json()[0]["items"] == 3
    assert client.get("/api/stats").json()["photos"] == 3


def test_files_stream_locally_with_caching(authed):
    client, ids, _ = authed
    r = client.get(f"/api/media/{ids[0]}/thumb")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "immutable" in r.headers["cache-control"]
    dl = client.get(f"/api/media/{ids[0]}/compressed", params={"download": True})
    assert dl.status_code == 200 and "attachment" in dl.headers["content-disposition"]
    rng = client.get(f"/api/media/{ids[0]}/compressed", headers={"Range": "bytes=0-99"})
    assert rng.status_code == 206 and len(rng.content) == 100


def test_files_redirect_to_s3_when_not_local(authed, bucket, monkeypatch):
    client, ids, app = authed
    (app.state.settings.library / "thumb").rename(app.state.settings.library / "gone")
    assert client.get(f"/api/media/{ids[0]}/thumb").status_code == 404   # no S3 configured
    app.state.s3 = bucket
    r = client.get(f"/api/media/{ids[0]}/thumb", follow_redirects=False)
    assert r.status_code == 302 and "X-Amz-Expires=900" in r.headers["location"]


def test_everything_needs_auth(api, sidecar, device):
    client, _, app = api
    seed(sidecar, device, app.state.settings.library, n=1)
    for path in ("/api/search", "/api/people", "/api/devices", "/api/stats"):
        assert client.get(path).status_code == 401, path
