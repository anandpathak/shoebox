from fastapi.testclient import TestClient


def test_no_credentials_is_401(api):
    client, _, _ = api
    assert client.get("/api/health").status_code == 401


def test_bearer_token(api):
    client, token, _ = api
    r = client.get("/api/health", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["via"] == "token"
    assert client.get("/api/health", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_local_page_load_sets_cookie_and_cookie_works(api):
    client, _, _ = api
    page = client.get("/")
    assert page.status_code == 200
    cookie = page.headers["set-cookie"]
    assert "httponly" in cookie.lower() and "samesite=strict" in cookie.lower()
    assert client.get("/api/health").json()["via"] == "cookie"


def test_cookie_writes_need_same_origin(api):
    client, _, app = api

    @app.post("/api/_probe")
    def probe(u=__import__("fastapi").Depends(__import__("photoctl.server.deps", fromlist=["user"]).user)):
        return {"ok": True}

    client.get("/")
    assert client.post("/api/_probe").status_code == 403
    assert client.post("/api/_probe", headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/_probe", headers={"Origin": "http://127.0.0.1:8765"}).status_code == 200


def test_remote_clients_never_get_the_cookie(api):
    _, _, app = api
    remote = TestClient(app, base_url="http://127.0.0.1:8765", client=("100.101.102.103", 1))
    assert "set-cookie" not in remote.get("/").headers


def test_tailscale_identity(api):
    client, _, app = api
    ok = client.get("/api/health", headers={"Tailscale-User-Login": "me@example.com",
                                            "Host": "my-mac.tail1234.ts.net"})
    assert ok.status_code == 200 and ok.json()["user"] == "me@example.com"
    assert client.get("/api/health", headers={"Tailscale-User-Login": "stranger@example.com"}).status_code == 403
    spoof = TestClient(app, base_url="http://127.0.0.1:8765", client=("100.1.2.3", 1))
    assert spoof.get("/api/health", headers={"Tailscale-User-Login": "me@example.com"}).status_code == 401


def test_dns_rebinding_host_is_rejected(api):
    client, token, _ = api
    r = client.get("/api/health", headers={"Authorization": f"Bearer {token}", "Host": "attacker.example:8765"})
    assert r.status_code == 400


def test_security_headers(api):
    client, _, _ = api
    r = client.get("/")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"


def test_serve_refuses_public_interfaces():
    from click.testing import CliRunner

    from photoctl import cli
    res = CliRunner().invoke(cli.main, ["serve", "--host", "0.0.0.0"])
    assert res.exit_code != 0 and "tailscale serve" in res.output
