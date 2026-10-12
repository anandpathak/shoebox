"""Pairing phones on the same Wi-Fi (`photoctl serve --lan`).

The Mac shows a short code (and a QR code with the address). A phone that sends the right code gets its
own random token, kept by the phone and used as a cookie by the web app and as `Authorization: Bearer`
by the native app's background uploads. Only a SHA-256 of each token is stored, in
~/.photoctl/paired_devices.json, so `photoctl paired revoke` can cut one phone off.

Codes expire after 30 minutes and are thrown away after 5 wrong guesses.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import paths

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I
CODE_TTL = 30 * 60
MAX_TRIES = 5
DEVICE_COOKIE = "photoctl_device"
_lock = threading.Lock()


def _file():
    return paths.home() / "paired_devices.json"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def load() -> dict[str, dict]:
    try:
        return json.loads(_file().read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save(data: dict) -> None:
    tmp = _file().with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _file())


def add_device(name: str) -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        data = load()
        data[_digest(token)] = {"name": name, "paired_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _save(data)
    return token


def device_for(token: str) -> str | None:
    """The paired device's name if this token belongs to one."""
    if not token:
        return None
    d = _digest(token)
    for key, info in load().items():
        if hmac.compare_digest(key, d):
            return info.get("name") or "phone"
    return None


def revoke(name: str) -> int:
    with _lock:
        data = load()
        keep = {k: v for k, v in data.items() if v.get("name") != name}
        _save(keep)
    return len(data) - len(keep)


class Code:
    """The current pairing code (one at a time, in memory)."""

    def __init__(self) -> None:
        self.value: str | None = None
        self.expires = 0.0
        self.tries = 0

    def new(self) -> str:
        self.value = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        self.expires = time.time() + CODE_TTL
        self.tries = 0
        return self.value

    def check(self, guess: str) -> bool:
        if not self.value or time.time() > self.expires:
            return False
        ok = hmac.compare_digest(guess.strip().upper().replace("-", ""), self.value)
        if not ok:
            self.tries += 1
            if self.tries >= MAX_TRIES:
                self.value = None
        return ok


router = APIRouter(prefix="/api/pair")


class Pair(BaseModel):
    code: str = Field(max_length=32)
    name: str = Field(default="Phone", max_length=60)


@router.post("")
def pair(body: Pair, request: Request, response: Response):
    from .auth import host_allowed

    if not host_allowed(request, request.app.state.settings.hostnames):
        raise HTTPException(400, "Unrecognised host name")
    code: Code = request.app.state.pairing
    with _lock:
        ok = code.check(body.code)
    if not ok:
        raise HTTPException(403, "That code isn't right or has expired. Run `photoctl pair` on the Mac for a new one.")
    token = add_device(body.name.strip() or "Phone")
    response.set_cookie(DEVICE_COOKIE, token, httponly=True, samesite="strict", max_age=60 * 60 * 24 * 365 * 5)
    return {"token": token}


def _owner(request: Request):
    from .deps import user

    u = user(request)
    if u.via == "paired":
        raise HTTPException(403, "Only this Mac can do that")
    return u


@router.post("/code")
def new_code(request: Request, _=Depends(_owner)):
    """A fresh code (photoctl pair calls this on the running server)."""
    return {"code": request.app.state.pairing.new(), "expires_in": CODE_TTL}


@router.get("/devices")
def devices(_=Depends(_owner)):
    return [{"name": v.get("name"), "paired_at": v.get("paired_at")} for v in load().values()]
