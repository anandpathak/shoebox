"""Archive runs started from the app: one at a time (the same ingest lock as the CLI), in a thread."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import paths
from ..archiver import Options, Selection, SelectionError, run_archive
from ..runlock import AlreadyRunning


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Run:
    run_id: str
    kind: str
    label: str
    started_at: str = field(default_factory=_now)
    status: str = "running"            # running | done | failed | cancelled
    todo: int | None = None
    matched: int | None = None         # items the selection found
    skipped: int = 0                   # of those, already archived before
    processed: int = 0
    counts: dict = field(default_factory=dict)
    uploaded_bytes: int = 0
    error: str | None = None
    messages: list[str] = field(default_factory=list)
    ended_at: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    results: dict = field(default_factory=dict, repr=False)   # source_ref -> (status, error), per item

    def public(self) -> dict:
        return ({k: v for k, v in self.__dict__.items() if k not in ("cancel", "results")}
                | {"cancellable": self.status == "running"})


class RunManager:
    def __init__(self):
        self.runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        self.on_finished: list = []          # called with the Run after it ends (e.g. analyse new photos)

    def start(self, kind: str, label: str, opts: Options, *, items=None, selection: Selection | None = None,
              device=None, timeout: float = 10.0) -> Run:
        """Start in the background; returns once the run holds the ingest lock. Raises AlreadyRunning."""
        accepted = threading.Event()
        outcome: dict = {}
        holder: dict = {}

        def on_event(kind_: str, **d):
            run = holder.get("run")
            if kind_ == "accepted":
                run = Run(run_id=d["run_id"], kind=kind, label=label)
                holder["run"] = run
                with self._lock:
                    self.runs[run.run_id] = run
                outcome["run"] = run
                accepted.set()
                return
            if run is None:
                return
            if kind_ == "start":
                run.todo = d["todo"]
                run.matched, run.skipped = d.get("matched"), d.get("skipped", 0)
            elif kind_ == "item":
                run.processed += 1
                run.results[d["source_ref"]] = (d["status"], d.get("error"))
                run.counts[d["status"]] = run.counts.get(d["status"], 0) + 1
                run.uploaded_bytes += d.get("uploaded_bytes") or 0
            elif kind_ == "message":
                run.messages.append(d["text"])
            elif kind_ == "end" and d.get("cancelled"):
                run.status = "cancelled"

        def work():
            try:
                run_archive(items, opts, selection=selection, device=device, on_event=on_event,
                            cancel=_cancel_proxy(holder))
                run = holder.get("run")
                if run and run.status == "running":
                    run.status = "done"
            except AlreadyRunning as e:
                outcome["error"] = e
                accepted.set()
            except (SelectionError, Exception) as e:  # noqa: B014 — report anything to the app
                run = holder.get("run")
                if run:
                    run.status, run.error = "failed", str(e)
                else:
                    outcome["error"] = e
                    accepted.set()
            finally:
                run = holder.get("run")
                if run:
                    run.ended_at = _now()
                    _announce(run)
                    for fn in self.on_finished:
                        try:
                            fn(run)
                        except Exception:  # noqa: BLE001 — a follow-up must never break the run
                            pass

        threading.Thread(target=work, name=f"archive-{kind}", daemon=True).start()
        if not accepted.wait(timeout):
            raise TimeoutError("The archive run didn't start in time")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["run"]

    def get(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    def active(self) -> Run | None:
        return next((r for r in self.runs.values() if r.status == "running"), None)


class _cancel_proxy:  # noqa: N801 — behaves like threading.Event, bound to the run once it exists
    def __init__(self, holder: dict):
        self.holder = holder

    def is_set(self) -> bool:
        run = self.holder.get("run")
        return bool(run and run.cancel.is_set())


def history(limit: int = 30) -> list[dict]:
    """Every run on this device (CLI or app), newest first, read from ~/.shoebox/runs/*.jsonl."""
    out = []
    for f in sorted(paths.runs_dir().glob("*.jsonl"), reverse=True)[:limit]:
        out.append(summarize(f))
    return out


def summarize(path: Path) -> dict:
    info = {"run_id": path.stem, "status": "running", "counts": {}, "processed": 0, "todo": None, "messages": [],
            "started_at": None, "ended_at": None, "failed": []}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        kind = e.get("event")
        if kind in ("accepted", "start") and not info["started_at"]:
            info["started_at"] = e.get("ts")
        if kind == "start":
            info["todo"] = e.get("todo")
        elif kind == "item":
            info["processed"] += 1
            info["counts"][e["status"]] = info["counts"].get(e["status"], 0) + 1
            if e["status"] in ("failed", "missing_original"):
                info["failed"].append({"source_ref": e["source_ref"], "status": e["status"], "error": e.get("error")})
        elif kind == "end":
            info["status"] = "cancelled" if e.get("cancelled") else "done"
            info["ended_at"] = e.get("ts")
        elif kind == "message":
            info["messages"].append(e.get("text"))
    return info


def _announce(run: Run) -> None:
    from .push import notify

    failed = run.counts.get("failed", 0) + run.counts.get("missing_original", 0)
    done = sum(run.counts.values()) - failed
    title = {"done": "Archive finished", "cancelled": "Archive cancelled", "failed": "Archive failed"}.get(run.status)
    if not title or (run.status == "done" and not run.counts):
        return
    body = run.error or f"{done} done" + (f", {failed} failed" if failed else "") + f" · {run.label}"
    try:
        notify(title, body, url="/#/uploads")
    except Exception:  # notifications are best effort
        pass
