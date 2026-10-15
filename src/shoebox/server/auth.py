"""Who may use the API (LOCAL_APP_PLAN.md §8 Security).

* Local browser: a random per-install token (macOS Keychain, or a 0600 file) set as an HttpOnly,
  SameSite=Strict cookie when the page is opened from 127.0.0.1. Scripts use `Authorization: Bearer`.
* Phones: through `tailscale serve`, which proxies from 127.0.0.1 and adds Tailscale-User-Login;
  only logins in config.toml `allowed_logins` get in.
* Phones on the same Wi-Fi (`serve --lan`): a per-phone token from pairing (server/pairing.py), sent as a
  cookie by the web app and as `Authorization: Bearer` by the native app's background uploads.
* DNS-rebinding guard: the Host header must be localhost, 127.0.0.1, *.ts.net or a configured name.
* CSRF guard: cookie-authenticated writes must carry a same-origin Origin header.
"""

from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .. import paths
from . import pairing

COOKIE = "shoebox_token"
SERVICE = "shoebox"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
LOCAL = {"127.0.0.1", "::1"}


def _token_file():
    return paths.home() / "api_token"


def get_or_create_token() -> str:
    if os.environ.get("SHOEBOX_TOKEN_STORE", "keychain") == "keychain":
        try:
            import keyring

            token = keyring.get_password(SERVICE, "api-token")
            if not token:                                   # saved under the app's earlier name?
                token = keyring.get_password("photoctl", "api-token")
                if token:
                    keyring.set_password(SERVICE, "api-token", token)
            if not token:
                token = secrets.token_urlsafe(32)
                keyring.set_password(SERVICE, "api-token", token)
            return token
        except Exception:
            pass  # no keychain available (CI, Linux): fall back to a private file
    f = _token_file()
    if f.exists():
        return f.read_text().strip()
    token = secrets.token_urlsafe(32)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(token)
    return token


@dataclass
class User:
    name: str
    via: str  # "token" | "cookie" | "tailscale" | "paired"


def host_allowed(request: Request, hostnames: list[str]) -> bool:
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
    return host in {"localhost", "127.0.0.1", "::1", *[h.lower() for h in hostnames]} or host.endswith(".ts.net")


def authenticate(request: Request) -> User:
    settings = request.app.state.settings
    token = request.app.state.token
    if not host_allowed(request, settings.hostnames):
        raise HTTPException(400, "Unrecognised host name")
    client = request.client.host if request.client else ""
    login = request.headers.get("tailscale-user-login")
    if login:
        if client not in LOCAL:
            raise HTTPException(401, "Tailscale identity headers are only trusted from tailscale serve")
        if login.lower() not in {x.lower() for x in settings.allowed_logins}:
            raise HTTPException(403, f"{login} isn't allowed. Add it to allowed_logins in ~/.shoebox/config.toml")
        return User(login, "tailscale")
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if bearer and hmac.compare_digest(bearer, token):
        return User("owner", "token")
    if bearer and (name := pairing.device_for(bearer)):
        return User(name, "paired")
    cookie = request.cookies.get(COOKIE, "")
    if cookie and hmac.compare_digest(cookie, token):
        if request.method in UNSAFE and not _same_origin(request):
            raise HTTPException(403, "Cross-site request blocked")
        return User("owner", "cookie")
    paired = request.cookies.get(pairing.DEVICE_COOKIE, "")
    if paired and (name := pairing.device_for(paired)):
        if request.method in UNSAFE and not _same_origin(request):
            raise HTTPException(403, "Cross-site request blocked")
        return User(name, "paired")
    raise HTTPException(401, "Sign in by opening the app from this Mac (http://127.0.0.1), over Tailscale, "
                             "or pair this phone (shoebox pair)")


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return False
    host = request.headers.get("host", "")
    return origin.split("://", 1)[-1].rstrip("/") == host


def may_set_cookie(request: Request) -> bool:
    """Only a browser on this Mac, talking to it directly, gets the token cookie."""
    client = request.client.host if request.client else ""
    return client in LOCAL and "tailscale-user-login" not in request.headers
