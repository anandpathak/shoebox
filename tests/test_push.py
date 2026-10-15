import os
import stat
import time

import pytest

from test_archiver import photos


@pytest.fixture
def authed(api):
    client, token, app = api
    client.headers["Authorization"] = f"Bearer {token}"
    return client, app


SUB = {"endpoint": "https://push.example.com/abc", "keys": {"p256dh": "BNc" + "A" * 84, "auth": "AAAAAAAAAAAAAAAAAAAAAA"}}


def test_keys_are_private_and_stable(authed, shoebox_home):
    client, _ = authed
    k1 = client.get("/api/push/key").json()["publicKey"]
    assert k1 == client.get("/api/push/key").json()["publicKey"] and len(k1) == 87
    mode = stat.S_IMODE(os.stat(shoebox_home / "vapid.json").st_mode)
    assert mode == 0o600


def test_subscribe_notify_and_drop_expired(authed, monkeypatch, shoebox_home):
    client, _ = authed
    sent = []

    class Gone(Exception):
        pass

    import pywebpush
    from shoebox.server import push

    def fake_webpush(sub, data, **kw):
        if sub["endpoint"].endswith("gone"):
            raise pywebpush.WebPushException("gone", response=type("R", (), {"status_code": 410})())
        sent.append((sub["endpoint"], data))
    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    assert client.post("/api/push/subscribe", json={"subscription": SUB, "device_id": "PHONE"}).json() == {"subscriptions": 1}
    client.post("/api/push/subscribe", json={"subscription": {**SUB, "endpoint": "https://push.example.com/gone"}})
    assert client.post("/api/push/subscribe", json={"subscription": {"endpoint": "x"}}).status_code == 422
    assert client.post("/api/push/test").json() == {"sent": 1}
    assert [s["endpoint"] for s in push.subscriptions()] == [SUB["endpoint"]]     # expired one removed
    assert stat.S_IMODE(os.stat(shoebox_home / "push_subscriptions.json").st_mode) == 0o600


def test_run_end_sends_a_notification(authed, monkeypatch, tmp_path):
    client, app = authed
    import pywebpush
    from shoebox import archiver

    got = []
    monkeypatch.setattr(pywebpush, "webpush", lambda sub, data, **kw: got.append(data))
    monkeypatch.setattr(archiver, "select_photos", lambda sel: photos(tmp_path, 2))
    monkeypatch.setattr(archiver, "add_to_album", lambda *a, **k: None)
    client.post("/api/push/subscribe", json={"subscription": SUB})
    run = client.post("/api/uploads/apple-photos", json={"mode": "all"}).json()
    end = time.time() + 15
    while not got and time.time() < end:
        time.sleep(0.1)
    assert got and "Archive finished" in got[0] and "2 done" in got[0]
    assert run["run_id"]
