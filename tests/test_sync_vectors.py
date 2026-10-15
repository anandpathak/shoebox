"""Run spec/sync_vectors/*.json against the Python apply_ops in several delivery orders."""

import json
from pathlib import Path

import pytest

from shoebox.catalog import open_catalog
from shoebox.catalog.apply import apply_ops
from shoebox.catalog.store import resolve_person

VECTORS = sorted((Path(__file__).parent.parent / "spec" / "sync_vectors").glob("*.json"))
LIVE = "add_op NOT IN (SELECT add_op FROM tombstones)"


def orders(ops):
    yield "forward", [ops]
    yield "reverse", [list(reversed(ops))]
    yield "one_by_one_reversed", [[o] for o in reversed(ops)]
    yield "duplicated", [ops[::2], ops, list(reversed(ops))]


def check(conn, expect):
    for pid, cols in expect.get("media", {}).items():
        row = conn.execute("SELECT * FROM media WHERE photo_id = ?", (pid,)).fetchone()
        assert row is not None, pid
        assert {k: row[k] for k in cols} == cols
    for table, key, col in (("tags", "media_tags", "tag"), ("albums", "media_albums", "album")):
        for pid, values in expect.get(table, {}).items():
            got = sorted({r[0] for r in conn.execute(f"SELECT {col} FROM {key} WHERE photo_id = ? AND {LIVE}", (pid,))})
            assert got == sorted(values), (table, pid)
    for person, cols in expect.get("people", {}).items():
        if "name" in cols:
            assert conn.execute("SELECT name FROM people WHERE person_id = ?", (person,)).fetchone()[0] == cols["name"]
        if "resolves_to" in cols:
            assert resolve_person(conn, person) == cols["resolves_to"]
    for pid, names in expect.get("photo_people", {}).items():
        ids = {resolve_person(conn, r[0]) for r in
               conn.execute(f"SELECT person_id FROM media_people WHERE photo_id = ? AND {LIVE}", (pid,))}
        got = sorted(conn.execute("SELECT name FROM people WHERE person_id = ?", (i,)).fetchone()[0] for i in ids)
        assert got == sorted(names)
    for table, n in expect.get("counts", {}).items():
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == n, table
    for dev, cols in expect.get("devices", {}).items():
        row = conn.execute("SELECT * FROM devices WHERE device_id = ?", (dev,)).fetchone()
        assert {k: row[k] for k in cols} == cols
    for q, pids in expect.get("fts", {}).items():
        got = sorted(r[0] for r in conn.execute("SELECT photo_id FROM media_fts WHERE media_fts MATCH ?", (q,)))
        assert got == sorted(pids), q
    for pid, sources in expect.get("sources", {}).items():   # tag or person name → source
        got = dict(conn.execute(f"SELECT tag, source FROM media_tags WHERE photo_id = ? AND {LIVE}", (pid,)).fetchall())
        got |= {n.lower(): s for n, s in conn.execute(
            f"SELECT p.name, m.source FROM media_people m JOIN people p USING (person_id) WHERE m.photo_id = ? AND m.{LIVE}",
            (pid,)).fetchall()}
        assert {k: got.get(k) for k in sources} == sources, pid
    for add_op, remover in expect.get("tombstones", {}).items():
        assert conn.execute("SELECT removed_by FROM tombstones WHERE add_op = ?", (add_op,)).fetchone()[0] == remover


@pytest.mark.parametrize("path", VECTORS, ids=[p.stem for p in VECTORS])
def test_vector(path):
    vector = json.loads(path.read_text())
    for name, batches in orders(vector["ops"]):
        conn = open_catalog(":memory:")
        for batch in batches:
            apply_ops(conn, batch)
        try:
            check(conn, vector["expect"])
        except AssertionError as e:
            raise AssertionError(f"{path.stem} [{name}]: {e}") from e


def test_enough_vectors():
    assert len(VECTORS) >= 15
