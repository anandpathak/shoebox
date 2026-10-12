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
        self.state = {"running": False, "analyzed": 0, "failed": 0, "last_error": None, "last_run": None}

    @property
    def enabled(self) -> bool:
        return self.app.state.settings.analysis and os.environ.get("PHOTOCTL_ANALYSIS") != "0"

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
                while not self._stop.is_set():
                    counts = analyze_pending(catalog, self.app.state.settings.library, self.app.state.device.device_id,
                                             limit=self.BATCH, stop=self._stop)
                    self.state["analyzed"] += counts["analyzed"]
                    self.state["failed"] += counts["failed"]
                    if counts["analyzed"] + counts["skipped"] + counts["failed"] == 0:
                        break
                    time.sleep(0.2)          # leave room for the app's own requests
                self.state["last_error"] = None
            except Exception as e:  # noqa: BLE001 — e.g. models couldn't be downloaded; try again later
                self.state["last_error"] = f"{type(e).__name__}: {e}"
            finally:
                catalog.close()
                self.state["running"] = False
                self.state["last_run"] = time.time()
