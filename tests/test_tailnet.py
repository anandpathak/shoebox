import json
import os
import stat

import pytest
from click.testing import CliRunner

from photoctl import cli
from photoctl.tailnet import TailnetError, check

STATUS = {"BackendState": "Running", "Self": {"DNSName": "studio-mac.tail1234.ts.net.", "UserID": 7},
          "User": {"7": {"LoginName": "owner@example.com"}}}
SERVE = {"Web": {"studio-mac.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}}}


@pytest.fixture
def fake_tailscale(tmp_path, monkeypatch):
    def install(status=STATUS, serve=SERVE):
        (tmp_path / "status.json").write_text(json.dumps(status))
        (tmp_path / "serve.json").write_text(json.dumps(serve))
        script = tmp_path / "bin" / "tailscale"
        script.parent.mkdir(exist_ok=True)
        script.write_text(f"""#!/bin/sh
case "$1 $2" in
  "status --json") cat {tmp_path}/status.json ;;
  "serve status") cat {tmp_path}/serve.json ;;
  "serve --bg") echo '{json.dumps(SERVE)}' > {tmp_path}/serve.json ;;
  *) exit 2 ;;
esac
""")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{script.parent}{os.pathsep}{os.environ['PATH']}")
    return install


def test_check_reads_hostname_login_and_serve(fake_tailscale):
    fake_tailscale()
    net = check(8765)
    assert net.url == "https://studio-mac.tail1234.ts.net" and net.login == "owner@example.com" and net.serving
    assert not check(9999).serving


def test_not_connected_and_missing(fake_tailscale, monkeypatch):
    fake_tailscale(status={**STATUS, "BackendState": "NeedsLogin"})
    with pytest.raises(TailnetError, match="sign in"):
        check(8765)
    monkeypatch.setenv("PATH", "/nonexistent")
    import photoctl.tailnet as t
    monkeypatch.setattr(t, "CANDIDATES", ("tailscale",))
    with pytest.raises(TailnetError, match="isn't installed"):
        check(8765)


def test_serve_tailscale_sets_up_and_prints_qr(fake_tailscale, monkeypatch):
    fake_tailscale(serve={})
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    res = CliRunner().invoke(cli.main, ["serve", "--tailscale"])
    assert res.exit_code != 0 and "tailscale serve --bg 8765" in res.output
    res = CliRunner().invoke(cli.main, ["serve", "--setup-tailscale"])
    assert res.exit_code == 0, res.output
    assert "https://studio-mac.tail1234.ts.net" in res.output and "Allowing owner@example.com" in res.output
    assert "█" in res.output or "▀" in res.output   # the QR code
