"""Daily database backup via a macOS LaunchAgent (LOCAL_APP_PLAN.md §12.2)."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths

LABEL = "app.shoebox.backup"


def agents_dir() -> Path:
    return Path(os.environ.get("SHOEBOX_LAUNCHAGENTS_DIR") or Path.home() / "Library" / "LaunchAgents")


def plist_path() -> Path:
    return agents_dir() / f"{LABEL}.plist"


def build_plist(bucket: str, hour: int = 3, minute: int = 30, profile: str | None = None) -> dict:
    shoebox = shutil.which("shoebox") or str(Path(sys.executable).with_name("shoebox"))
    env = {"SHOEBOX_BUCKET": bucket, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
    if profile:
        env["AWS_PROFILE"] = profile
    if os.environ.get("SHOEBOX_HOME"):
        env["SHOEBOX_HOME"] = os.environ["SHOEBOX_HOME"]
    logs = paths.home() / "logs"
    logs.mkdir(exist_ok=True)
    return {
        "Label": LABEL,
        "ProgramArguments": [shoebox, "db", "backup"],
        "EnvironmentVariables": env,
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "RunAtLoad": False,
        "StandardOutPath": str(logs / "backup.log"),
        "StandardErrorPath": str(logs / "backup.err.log"),
        "ProcessType": "Background",
    }


def install(bucket: str, *, hour: int = 3, minute: int = 30, profile: str | None = None, load: bool = True) -> Path:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(build_plist(bucket, hour, minute, profile), f)
    if load:
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(path)], check=True, capture_output=True)
    return path


def uninstall(load: bool = True) -> bool:
    path = plist_path()
    if not path.exists():
        return False
    if load:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True)
    path.unlink()
    return True
