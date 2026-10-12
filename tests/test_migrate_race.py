"""Many connections opening an old catalog at once: exactly one migrates it, none fails."""

import sqlite3
import threading

from photoctl.catalog import SCHEMA_VERSION, _migrations, open_catalog, schema_version


import pytest


@pytest.mark.parametrize("round_", range(5))
def test_concurrent_opens_migrate_once(tmp_path, round_):
    path = tmp_path / "cat.sqlite"
    old = sqlite3.connect(path)
    for v, sql in sorted(_migrations().items()):
        if v > 3:
            break
        old.executescript(sql)
        old.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(v),))
    old.commit()
    old.close()
    errors, start = [], threading.Barrier(24)

    def worker():
        start.wait()
        try:
            open_catalog(path, any_thread=True).close()
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=worker) for _ in range(24)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert schema_version(open_catalog(path)) == SCHEMA_VERSION
