"""Phones on the same Wi-Fi (`serve --lan`): pairing codes, per-phone tokens, revoking."""

import pytest
from fastapi.testclient import TestClient

LAN = "192.168.1.11"


@pytest.fixture
def lan(tmp_path):
    from photoctl.server.app import create_app
    from photoctl.server.config import Settings

    app = create_app(Settings(library=tmp_path / "library", hostnames=[LAN, "my-mac.local"]))
    phone = TestClient(app, base_url=f"http://{LAN}:8765", client=("192.168.1.50", 40000))
    mac = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))
    mac.headers["Authorization"] = f"Bearer {app.state.token}"
    return app, phone, mac


def test_unpaired_phone_is_refused_and_gets_no_cookie(lan):
    _, phone, _ = lan
    assert phone.get("/api/health").status_code == 401
    r = phone.get("/")
    assert "set-cookie" not in r.headers


def test_pairing_gives_a_token_that_works_as_cookie_and_bearer(lan):
    app, phone, mac = lan
    code = mac.post("/api/pair/code").json()["code"]
    r = phone.post("/api/pair", json={"code": code[:4] + "-" + code[4:].lower(), "name": "Pixel"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert phone.get("/api/health").json()["via"] == "paired"          # cookie set by /api/pair
    other = TestClient(app, base_url=f"http://{LAN}:8765", client=("192.168.1.51", 40000))
    assert other.get("/api/health", headers={"Authorization": f"Bearer {token}"}).json()["user"] == "Pixel"
    assert [d["name"] for d in mac.get("/api/pair/devices").json()] == ["Pixel"]
    # the stored file has no tokens in it
    from photoctl import paths
    assert token not in (paths.home() / "paired_devices.json").read_text()


def test_paired_phone_writes_need_same_origin(lan):
    _, phone, mac = lan
    phone.post("/api/pair", json={"code": mac.post("/api/pair/code").json()["code"]})
    r = phone.post("/api/devices/register", json={"device_id": "01PHONE", "name": "P", "platform": "android"},
                   headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_wrong_codes_burn_the_code(lan):
    _, phone, mac = lan
    code = mac.post("/api/pair/code").json()["code"]
    for _ in range(5):
        assert phone.post("/api/pair", json={"code": "WRONGWRO"}).status_code == 403
    assert phone.post("/api/pair", json={"code": code}).status_code == 403


def test_expired_code(lan, monkeypatch):
    import time

    app, phone, mac = lan
    code = mac.post("/api/pair/code").json()["code"]
    monkeypatch.setattr(time, "time", lambda: app.state.pairing.expires + 1)
    assert phone.post("/api/pair", json={"code": code}).status_code == 403


def test_revoke(lan):
    from photoctl.server.pairing import revoke

    _, phone, mac = lan
    phone.post("/api/pair", json={"code": mac.post("/api/pair/code").json()["code"], "name": "Old phone"})
    assert phone.get("/api/health").status_code == 200
    assert revoke("Old phone") == 1
    assert phone.get("/api/health").status_code == 401


def test_only_the_mac_makes_codes(lan):
    _, phone, mac = lan
    phone.post("/api/pair", json={"code": mac.post("/api/pair/code").json()["code"]})
    assert phone.post("/api/pair/code").status_code == 403
    assert phone.get("/api/pair/devices").status_code == 403


def test_tailscale_header_from_the_lan_is_not_trusted(lan):
    app, _, _ = lan
    app.state.settings.allowed_logins = ["me@example.com"]
    spoof = TestClient(app, base_url=f"http://{LAN}:8765", client=("192.168.1.66", 40000))
    assert spoof.get("/api/health", headers={"Tailscale-User-Login": "me@example.com"}).status_code == 401


def test_unknown_host_names_are_refused(lan):
    app, _, mac = lan
    code = mac.post("/api/pair/code").json()["code"]
    rebind = TestClient(app, base_url="http://attacker.example:8765", client=("192.168.1.50", 40000))
    assert rebind.post("/api/pair", json={"code": code}).status_code == 400


def test_paired_phone_backs_up_with_bearer_only(lan):
    """The native app's background uploads carry only Authorization: Bearer <phone token>."""
    import hashlib

    from test_api_tus import jpeg_with_exif

    app, phone, mac = lan
    token = phone.post("/api/pair", json={"code": mac.post("/api/pair/code").json()["code"]}).json()["token"]
    native = TestClient(app, base_url=f"http://{LAN}:8765", client=("192.168.1.50", 40001))
    native.headers["Authorization"] = f"Bearer {token}"
    assert native.post("/api/devices/register", json={"device_id": "01PAIREDPHONE", "name": "P", "platform": "android"}).status_code == 200
    data = jpeg_with_exif()
    r = native.post("/api/uploads/raw", content=data, headers={
        "X-Device-Id": "01PAIREDPHONE", "X-Asset-Id": "77", "X-Filename": "IMG.JPG",
        "X-Sha256": hashlib.sha256(data).hexdigest()})
    assert r.json()["status"] == "received"
