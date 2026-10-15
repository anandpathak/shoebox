"""This Mac's address on the local network, for `shoebox serve --lan`."""

from __future__ import annotations

import socket


def address() -> tuple[str | None, str | None]:
    """(LAN IPv4 address, Bonjour name like 'my-mac.local'). Nothing is sent: UDP connect only picks a route."""
    ip = None
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))      # TEST-NET address; picks the interface the default route uses
        ip = s.getsockname()[0]
    except OSError:
        pass
    finally:
        s.close()
    if ip and (ip.startswith("127.") or ip == "0.0.0.0"):
        ip = None
    return ip, bonjour_name()


def bonjour_name() -> str | None:
    import subprocess

    try:   # macOS: the name other devices see, e.g. "My-MacBook-Pro" → my-macbook-pro.local
        name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ""
    if not name:
        name = socket.gethostname().removesuffix(".local")
    if not name or name.replace(".", "").isdigit():
        return None
    return f"{name}.local".lower()
