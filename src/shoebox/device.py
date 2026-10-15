"""Stable identity for this machine: a ULID device_id plus a human name and platform."""

from __future__ import annotations

import json
import os
import platform
import secrets
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from . import paths

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(ms: int | None = None) -> str:
    """26-char ULID: 48-bit millisecond timestamp + 80 random bits, Crockford base32 (sortable by time)."""
    ms = int(time.time() * 1000) if ms is None else ms
    n = (ms << 80) | secrets.randbits(80)
    return "".join(_CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))


@dataclass
class Device:
    device_id: str
    name: str
    platform: str
    created_at: str


def _computer_name() -> str:
    try:
        out = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return socket.gethostname()


def _platform() -> str:
    return {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(platform.system(), "unknown")


def load_device(create: bool = True) -> Device | None:
    path = paths.device_file()
    if path.exists():
        return Device(**json.loads(path.read_text()))
    if not create:
        return None
    dev = Device(
        device_id=new_ulid(),
        name=_computer_name(),
        platform=_platform(),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    save_device(dev)
    return dev


def save_device(dev: Device) -> None:
    path = paths.device_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(dev), indent=2))
    os.replace(tmp, path)
