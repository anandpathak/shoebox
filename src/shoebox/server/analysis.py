"""Analyses new photos in the background (labels → tags, faces → people), a batch at a time."""

from __future__ import annotations

import os
import threading
import time


class AnalysisWorker:
    BATCH = 25

    def __init__(self, app):
        self.app = app
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.state = {"running": False, "analyzed": 0, "failed": 0, "last_error": None, "last_run": None,
                      "step": None, "embedded": 0, "captioned": 0}

    @property
    def enabled(self) -> bool:
        return self.app.state.settings.analysis and os.environ.get("SHOEBOX_ANALYSIS") != "0"

    def kick(self, *_args) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._wake.set()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="analysis", daemon=True)
                self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _loop(self) -> None:
        from ..analyze.run import analyze_pending
        from ..catalog import open_catalog

        while not self._stop.is_set():
            self._wake.wait(timeout=600)
            self._wake.clear()
            if self._stop.is_set():
                return
            self.state["running"] = True
            catalog = open_catalog(any_thread=True)
            try:
                self.state["step"] = "place names"
                from ..places import backfill
                from .deps import app_bucket
                backfill(catalog, self.app.state.settings.library, self.app.state.device.device_id,
                         bucket=app_bucket(self.app), stop=self._stop)
                self.state["step"] = "labels and faces"
                while not self._stop.is_set():
                    counts = analyze_pending(catalog, self.app.state.settings.library, self.app.state.device.device_id,
                                             limit=self.BATCH, stop=self._stop)
                    self.state["analyzed"] += counts["analyzed"]
                    self.state["failed"] += counts["failed"]
                    if counts["analyzed"] + counts["skipped"] + counts["failed"] == 0:
                        break
                    time.sleep(0.2)          # leave room for the app's own requests
                self._ai_pass(catalog)
                self.state["last_error"] = None
            except Exception as e:  # noqa: BLE001 — e.g. models couldn't be downloaded; try again later
                self.state["last_error"] = f"{type(e).__name__}: {e}"
            finally:
                catalog.close()
                self.state["running"] = False
                self.state["step"] = None
                self.state["last_run"] = time.time()

    def _ai_pass(self, catalog) -> None:
        """Photo vectors (plain-English search) and captions, if the AI extras are installed."""
        from ..analyze import ai
        from ..analyze.ai_run import analyze_ai

        if not ai.available():
            return
        cfg = ai.settings()
        self.state["step"] = "loading models"
        emb, cap = ai.embedder(), ai.captioner()
        self.state["step"] = "search and captions"
        while not self._stop.is_set():
            counts = analyze_ai(catalog, self.app.state.settings.library, self.app.state.device.device_id,
                                embed_images=emb.images, search_model=cfg["search_model"],
                                caption=cap.caption, caption_model=cfg["caption_model"], limit=16, stop=self._stop)
            self.state["embedded"] += counts["embedded"]
            self.state["captioned"] += counts["captioned"]
            if sum(counts[k] for k in ("embedded", "captioned", "skipped", "failed")) == 0:
                break
            time.sleep(0.2)
