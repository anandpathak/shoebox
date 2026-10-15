import json

import pytest

from shoebox.runlock import AlreadyRunning, IngestLock, RunLog


def test_second_run_is_refused_with_holder_details():
    with IngestLock() as first:
        with pytest.raises(AlreadyRunning) as err:
            with IngestLock():
                pass
        assert err.value.holder["run_id"] == first.run_id
    with IngestLock():  # released after exit
        pass


def test_run_log_writes_jsonl(shoebox_home):
    log = RunLog("RUN1")
    log.event("start", todo=3)
    log.event("item", status="done")
    log.close()
    lines = [json.loads(x) for x in (shoebox_home / "runs" / "RUN1.jsonl").read_text().splitlines()]
    assert [x["event"] for x in lines] == ["start", "item"] and lines[0]["todo"] == 3


def test_sync_lock_is_separate_from_ingest_lock():
    from shoebox.runlock import SyncLock

    with IngestLock(), SyncLock():  # both can be held at once
        with pytest.raises(AlreadyRunning, match="a sync is already in progress"):
            with SyncLock():
                pass
