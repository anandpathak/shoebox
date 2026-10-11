import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from conftest import make_jpeg
from photoctl.archiver import Options, run_archive
from photoctl.runlock import AlreadyRunning, IngestLock


def photos(tmp_path, n):
    apple = json.dumps({"labels": [], "persons": [], "albums": [], "keywords": []})
    return [SimpleNamespace(uuid=f"U{i}", path=str(make_jpeg(tmp_path / f"src/{i}.jpg", (800, 600), (i * 20, 50, 90))),
                            date=datetime(2025, 1, i + 1, tzinfo=timezone.utc), original_filename=f"{i}.jpg", width=800,
                            height=600, uti_original="public.jpeg", ismovie=False, has_raw=False, path_raw=None,
                            live_photo=False, path_live_photo=None, intrash=False, hidden=False, json=lambda: apple)
            for i in range(n)]


def test_events_and_summary(tmp_path):
    seen = []
    s = run_archive(photos(tmp_path, 3), Options(out=tmp_path / "o", local_only=True, mark_album=False),
                    on_event=lambda kind, **d: seen.append(kind))
    assert s.counts == {"done": 3} and s.todo == 3
    assert seen[0] == "start" and seen.count("item") == 3 and seen[-1] == "end"


def test_cancel_stops_after_items_in_progress(tmp_path):
    cancel = threading.Event()
    done = []

    def on_event(kind, **d):
        if kind == "item":
            done.append(d)
            cancel.set()

    s = run_archive(photos(tmp_path, 12), Options(out=tmp_path / "o", local_only=True, workers=2, mark_album=False),
                    on_event=on_event, cancel=cancel)
    assert s.cancelled and 1 <= len(done) <= 3


def test_refuses_when_a_run_is_going(tmp_path):
    with IngestLock():
        with pytest.raises(AlreadyRunning):
            run_archive(photos(tmp_path, 1), Options(out=tmp_path / "o", local_only=True))
