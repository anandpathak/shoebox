"""One test per conflict rule in LOCAL_APP_PLAN.md §5, each checked in both arrival orders."""

import pytest

from opkit import Dev, fresh, media, record, tags
from shoebox.catalog.apply import apply_ops
from shoebox.catalog.store import person_id, resolve_person


@pytest.fixture
def devs():
    return Dev("MAC"), Dev("PHONE")


def both_orders(tmp_path, ops, check):
    for name, seq in (("fwd", ops), ("rev", list(reversed(ops)))):
        conn = fresh(tmp_path, name)
        for op in seq:  # one at a time: worst case for ordering
            apply_ops(conn, [op])
        check(conn)


def test_same_photo_from_two_devices_merges(tmp_path, devs, sidecar):
    mac, phone = devs
    rec = record(sidecar)
    pid = rec["photo_id"]
    ops = [mac.op("media.upsert", {"record": rec}),
           mac.op("media.source", {"photo_id": pid, "device_id": "MAC", "source_ref": "applephotos:1"}),
           phone.op("media.upsert", {"record": rec}),
           phone.op("media.source", {"photo_id": pid, "device_id": "PHONE", "source_ref": "ios:9"})]

    def check(conn):
        assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM media_sources").fetchone()[0] == 2
    both_orders(tmp_path, ops, check)


def test_same_field_last_writer_wins(tmp_path, devs, sidecar):
    mac, phone = devs
    rec = record(sidecar)
    up = mac.op("media.upsert", {"record": rec})
    phone.see([up])
    a = mac.op("media.set", {"photo_id": rec["photo_id"], "field": "title", "value": "Kudle beach"}, advance=5)
    b = phone.op("media.set", {"photo_id": rec["photo_id"], "field": "title", "value": "Gokarna sunset"}, advance=9)
    winner = max((a, b), key=lambda o: o["hlc"])["body"]["value"]
    both_orders(tmp_path, [up, a, b], lambda c: media(c, rec["photo_id"])["title"] == winner or pytest.fail())


def test_different_fields_both_survive(tmp_path, devs, sidecar):
    mac, phone = devs
    rec = record(sidecar)
    pid = rec["photo_id"]
    up = mac.op("media.upsert", {"record": rec})
    ops = [up, mac.op("media.set", {"photo_id": pid, "field": "title", "value": "T"}),
           phone.op("media.set", {"photo_id": pid, "field": "favorite", "value": False})]

    def check(conn):
        m = media(conn, pid)
        assert (m["title"], m["favorite"]) == ("T", 0)
    both_orders(tmp_path, ops, check)


def test_concurrent_add_beats_remove(tmp_path, devs, sidecar):
    mac, phone = devs
    rec = record(sidecar, labels=[], keywords=[])
    pid = rec["photo_id"]
    up = mac.op("media.upsert", {"record": rec})
    first_add = mac.op("tag.add", {"photo_id": pid, "tag": "beach"})
    phone.see([up, first_add])
    remove = phone.op("tag.remove", {"photo_id": pid, "tag": "beach", "observed_add_ops": [first_add["op_id"]]})
    re_add = mac.op("tag.add", {"photo_id": pid, "tag": "Beach"})  # never seen by the phone
    both_orders(tmp_path, [up, first_add, remove, re_add], lambda c: tags(c, pid) == ["beach"] or pytest.fail())


def test_remove_cancels_seen_add_and_import(tmp_path, devs, sidecar):
    mac, phone = devs
    rec = record(sidecar, labels=["Sky"], keywords=[])
    pid = rec["photo_id"]
    up = mac.op("media.upsert", {"record": rec})
    rm = phone.op("tag.remove", {"photo_id": pid, "tag": "sky", "observed_add_ops": [f"import:{pid}:apple-label:sky"]})
    both_orders(tmp_path, [up, rm], lambda c: tags(c, pid) == [] or pytest.fail())


def test_person_rename_lww_and_merge_resolution(tmp_path, devs, sidecar):
    mac, phone = devs
    rec = record(sidecar, persons=["Asha"])
    pid, p1 = rec["photo_id"], person_id("Asha")
    up = mac.op("media.upsert", {"record": rec})
    phone.see([up])
    r1 = mac.op("person.set", {"person_id": p1, "field": "name", "value": "Asha P"}, advance=3)
    r2 = phone.op("person.set", {"person_id": p1, "field": "name", "value": "Asha Pathak"}, advance=7)
    p2 = mac.op("person.create", {"person_id": "p_kid", "name": "Kid"})
    merge = phone.op("person.merge", {"from": p1, "into": "p_kid"})
    winner = max((r1, r2), key=lambda o: o["hlc"])["body"]["value"]

    def check(conn):
        assert conn.execute("SELECT name FROM people WHERE person_id = ?", (p1,)).fetchone()[0] == winner
        assert resolve_person(conn, p1) == "p_kid"
        hits = conn.execute("SELECT photo_id FROM media_fts WHERE media_fts MATCH 'kid'").fetchall()
        assert [h[0] for h in hits] == [pid]
    both_orders(tmp_path, [up, r1, r2, p2, merge], check)


def test_delete_holds_until_restore(tmp_path, devs, sidecar):
    mac, phone = devs
    rec = record(sidecar)
    pid = rec["photo_id"]
    up = mac.op("media.upsert", {"record": rec})
    phone.see([up])
    delete = mac.op("media.delete", {"photo_id": pid})
    edit = phone.op("media.set", {"photo_id": pid, "field": "title", "value": "Edited"})

    def deleted_but_edited(conn):
        m = media(conn, pid)
        assert m["deleted"] == 1 and m["title"] == "Edited"
        assert conn.execute("SELECT COUNT(*) FROM media_fts").fetchone()[0] == 0
    both_orders(tmp_path, [up, delete, edit], deleted_but_edited)

    mac.see([edit])
    restore = mac.op("media.restore", {"photo_id": pid})
    both_orders(tmp_path / "r", [up, delete, edit, restore], lambda c: media(c, pid)["deleted"] == 0 or pytest.fail())


def test_duplicates_are_ignored(tmp_path, devs, sidecar):
    mac, _ = devs
    rec = record(sidecar)
    ops = [mac.op("media.upsert", {"record": rec}), mac.op("tag.add", {"photo_id": rec["photo_id"], "tag": "x"})]
    conn = fresh(tmp_path, "dup")
    assert apply_ops(conn, ops) == 2
    assert apply_ops(conn, ops + ops) == 0
    assert conn.execute("SELECT COUNT(*) FROM ops").fetchone()[0] == 2


def test_edit_arriving_before_upsert_is_deferred(tmp_path, devs, sidecar):
    mac, phone = devs
    rec = record(sidecar)
    pid = rec["photo_id"]
    up = mac.op("media.upsert", {"record": rec})
    phone.see([up])
    edit = phone.op("media.set", {"photo_id": pid, "field": "title", "value": "Early"})
    conn = fresh(tmp_path, "defer")
    apply_ops(conn, [edit])
    assert media(conn, pid) is None
    assert conn.execute("SELECT COUNT(*) FROM deferred_ops").fetchone()[0] == 1
    apply_ops(conn, [up])
    assert media(conn, pid)["title"] == "Early"
    assert conn.execute("SELECT COUNT(*) FROM deferred_ops").fetchone()[0] == 0


def test_import_never_overrides_an_edit(tmp_path, devs, sidecar):
    mac, phone = devs
    rec = record(sidecar, title="Apple title")
    pid = rec["photo_id"]
    up1 = mac.op("media.upsert", {"record": rec})
    phone.see([up1])
    edit = phone.op("media.set", {"photo_id": pid, "field": "title", "value": "Mine"})
    mac.see([edit])
    up2 = mac.op("media.upsert", {"record": rec}, advance=100)  # re-import later in time
    both_orders(tmp_path, [up1, edit, up2], lambda c: media(c, pid)["title"] == "Mine" or pytest.fail())


def test_device_register(tmp_path, devs):
    mac, _ = devs
    a = mac.op("device.register", {"device_id": "MAC", "name": "Mac mini", "platform": "macos"})
    b = mac.op("device.set", {"device_id": "MAC", "field": "name", "value": "Studio Mac"})

    def check(conn):
        row = conn.execute("SELECT * FROM devices").fetchone()
        assert (row["name"], row["platform"]) == ("Studio Mac", "macos")
        assert row["first_seen_hlc"] == a["hlc"] and row["last_seen_hlc"] == b["hlc"]
    both_orders(tmp_path, [a, b], check)
