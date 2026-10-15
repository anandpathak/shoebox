"""Where shoebox keeps its local files. Override the root with $SHOEBOX_HOME (used by tests)."""

from __future__ import annotations

import os
from pathlib import Path


OLD_HOME = ".photoctl"      # the app's earlier name; its folder is moved on first use


def home() -> Path:
    if os.environ.get("SHOEBOX_HOME"):
        root = Path(os.environ["SHOEBOX_HOME"])
    else:
        root = Path.home() / ".shoebox"
        old = Path.home() / OLD_HOME
        if not root.exists() and old.is_dir() and not old.is_symlink():
            old.rename(root)   # same disk, so this is instant and keeps everything (catalog, device id, token…)
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


def last_backup_file() -> Path:
    return home() / "last_backup.json"


def ingest_lock() -> Path:
    return home() / "ingest.lock"


def sync_lock() -> Path:
    return home() / "sync.lock"


def inbox_dir() -> Path:
    d = home() / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir() -> Path:
    d = home() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def runs_dir() -> Path:
    d = home() / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d
