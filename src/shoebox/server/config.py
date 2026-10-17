"""Server settings: ~/.shoebox/config.toml plus command-line options.

    [server]
    allowed_logins = ["you@example.com", "partner@example.com"]   # Tailscale logins allowed in
    hostnames      = ["my-mac.tail1234.ts.net"]                    # extra Host names to accept
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths


@dataclass
class Settings:
    library: Path = Path("library")
    bucket: str | None = None
    profile: str | None = None
    port: int = 8765
    allowed_logins: list[str] = field(default_factory=list)
    hostnames: list[str] = field(default_factory=list)
    sync_interval: int = 60
    static_dir: Path | None = None
    workers: int = 4             # files archived at the same time ([server] workers = 6 in config.toml)
    album_catch_up: bool = True  # at start, add archived Apple Photos items missing from "Archived to S3"
    analysis: bool = True        # analyse new photos (labels, faces) in the background; SHOEBOX_ANALYSIS=0 turns it off

    @classmethod
    def load(cls, **overrides) -> "Settings":
        data = {}
        cfg = paths.config_file()
        if cfg.exists():
            data = tomllib.loads(cfg.read_text()).get("server", {})
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        known.update({k: v for k, v in overrides.items() if v is not None})
        s = cls(**known)
        if os.environ.get("SHOEBOX_ALBUM_CATCH_UP") == "0":
            s.album_catch_up = False
        if os.environ.get("SHOEBOX_ANALYSIS") == "0":
            s.analysis = False
        s.library = Path(s.library)
        return s
