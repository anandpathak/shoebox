from fastapi.testclient import TestClient


def test_serves_built_web_app(tmp_path):
    from shoebox.server.app import create_app
    from shoebox.server.config import Settings

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Shoebox</title><div id=root></div>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    app = create_app(Settings(library=tmp_path, static_dir=dist))
    c = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 1))
    page = c.get("/")
    assert "Shoebox" in page.text and "set-cookie" in page.headers
    assert c.get("/assets/app.js").status_code == 200
    assert c.get("/api/health").json()["ok"]                 # cookie from the page load works
    assert c.get("/openapi.json").status_code == 404         # schema isn't published
