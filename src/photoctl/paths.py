"""Where photoctl keeps its local files. Override the root with $PHOTOCTL_HOME (used by tests)."""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    root = Path(os.environ.get("PHOTOCTL_HOME") or Path.home() / ".photoctl")
    root.mkdir(parents=True, exist_ok=True)
    return root


def state_db() -> Path:
    return home() / "state.sqlite"


def catalog_db() -> Path:
    return home() / "catalog.sqlite"


def device_file() -> Path:
    return home() / "device.json"


def config_file() -> Path:
    return home() / "config.toml"


def ingest_lock() -> Path:
    return home() / "ingest.lock"


def runs_dir() -> Path:
    d = home() / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d
