"""Reach shoebox from phones through Tailscale (LOCAL_APP_PLAN.md §9.1).

`tailscale serve --bg <port>` publishes https://<mac>.<tailnet>.ts.net with a real certificate,
only inside your tailnet, and adds Tailscale-User-Login to each request.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field

CANDIDATES = ("tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale")


class TailnetError(RuntimeError):
    pass


def _cli() -> str:
    for c in CANDIDATES:
        path = shutil.which(c) or (c if c.startswith("/") and os.path.exists(c) else None)
        if path:
            return path
    raise TailnetError("Tailscale isn't installed. Install it from https://tailscale.com/download (Mac App Store or "
                       "standalone), sign in, and install the Tailscale app on your phones with the same account.")


def _run(*args: str) -> str:
    proc = subprocess.run([_cli(), *args], capture_output=True, text=True, timeout=20)
    if proc.returncode != 0:
        raise TailnetError(f"tailscale {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout


@dataclass
class Tailnet:
    hostname: str               # my-mac.tail1234.ts.net
    login: str | None           # the signed-in user's login
    serving: bool               # tailscale serve already proxies to our port
    notes: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://{self.hostname}"


def check(port: int) -> Tailnet:
    status = json.loads(_run("status", "--json"))
    if status.get("BackendState") != "Running":
        raise TailnetError("Tailscale is installed but not connected. Open Tailscale and sign in.")
    me = status.get("Self") or {}
    host = (me.get("DNSName") or "").rstrip(".")
    if not host:
        raise TailnetError("Tailscale has no MagicDNS name for this Mac. Turn on MagicDNS and HTTPS certificates in the "
                           "Tailscale admin console (DNS page).")
    login = ((status.get("User") or {}).get(str(me.get("UserID"))) or {}).get("LoginName")
    serving = _serving(port)
    return Tailnet(hostname=host, login=login, serving=serving)


def _serving(port: int) -> bool:
    try:
        cfg = json.loads(_run("serve", "status", "--json") or "{}")
    except (TailnetError, ValueError):
        return False
    for site in (cfg.get("Web") or {}).values():
        for handler in (site.get("Handlers") or {}).values():
            proxy = handler.get("Proxy", "")
            if proxy.rstrip("/").endswith(f":{port}"):
                return True
    return False


def setup(port: int) -> None:
    _run("serve", "--bg", str(port))


def qr(url: str) -> str:
    import io

    import qrcode

    q = qrcode.QRCode(border=1)
    q.add_data(url)
    out = io.StringIO()
    q.print_ascii(out=out, invert=True)
    return out.getvalue()
