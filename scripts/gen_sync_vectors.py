"""Generate spec/sync_vectors/*.json — the conformance suite for apply_ops (Python and TypeScript).

Each vector has ops plus *hand-written* expectations (never computed by the implementation under
test). Runners must apply the ops in several orders (forward, reverse, one at a time, duplicated)
and check every expectation each time.

Run: uv run python scripts/gen_sync_vectors.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from photoctl.catalog.ops import make_op  # noqa: E402
from photoctl.catalog.store import person_id  # noqa: E402
from photoctl.hlc import HLC  # noqa: E402

OUT = ROOT / "spec" / "sync_vectors"
P1, P2 = "AAAAAAAAAAAAAAAAAAAAAAAAAA", "BBBBBBBBBBBBBBBBBBBBBBBBBB"


class Dev:
    def __init__(self, device_id: str, t: int):
        self.t = t
        self.clock = HLC(device_id, clock=lambda: self.t)

    def op(self, kind, body, advance=10):
        self.t += advance
        return make_op(self.clock, kind, body)

    def see(self, *ops):
        for o in ops:
            self.clock.observe(o["hlc"])


def rec(pid=P1, **apple):
    base = {"title": None, "description": None, "favorite": False, "hidden": False, "keywords": [], "labels": [],
            "albums": [], "persons": [], "latitude": 14.5, "longitude": 74.3,
            "exif_info": {"camera_make": "Apple", "camera_model": "iPhone 15", "tzname": "GMT+0530"},
            "place": {"name": "Gokarna, Karnataka", "address": {"city": "Gokarna", "state_province": "Karnataka",
                                                                "country": "India"}}}
    base.update(apple)
    part = "2025/2025-08"
    return {"schema_version": 1, "photo_id": pid, "media_type": "photo",
            "keys": {"raw": f"raw/{part}/{pid}.heic", "compressed": f"compressed/{part}/{pid}.jpg",
                     "thumb": f"thumb/{part}/{pid}.jpg", "meta": f"meta/{part}/{pid}.json"},
            "file": {"sha256": (pid.lower() * 3)[:64], "orig_name": "IMG_0001.HEIC", "orig_ext": "heic",
                     "bytes_raw": 2000000, "width": 4032, "height": 3024},
            "taken_at": "2025-08-15T11:52:05+05:30", "source": {"type": "applephotos", "ref": "uuid-1"},
            "apple": base}


def vectors():
    V = []

    def add(name, description, ops, expect):
        V.append({"name": name, "description": description, "ops": ops, "expect": expect})

    # 1
    a = Dev("MAC", 1_700_000_000_000)
    add("upsert_maps_facts", "A media.upsert fills the catalog row from the sidecar record.",
        [a.op("media.upsert", {"record": rec(), "bytes_compressed": 420000})],
        {"media": {P1: {"media_type": "photo", "mime": "image/heic", "place_city": "Gokarna",
                        "place_country": "India", "camera_model": "iPhone 15", "bytes_compressed": 420000,
                        "lat": 14.5, "lon": 74.3, "deleted": 0, "favorite": 0}},
         "fts": {"gokarna": [P1], "karnataka": [P1]}})
    # 2
    a, b = Dev("MAC", 1_700_000_000_000), Dev("PHONE", 1_700_000_000_000)
    add("same_photo_two_devices", "The same content upserted by two devices is one row with two sources.",
        [a.op("media.upsert", {"record": rec()}),
         a.op("media.source", {"photo_id": P1, "device_id": "MAC", "source_ref": "applephotos:1"}),
         b.op("media.upsert", {"record": rec()}),
         b.op("media.source", {"photo_id": P1, "device_id": "PHONE", "source_ref": "ios:9"})],
        {"counts": {"media": 1, "media_sources": 2}})
    # 3
    a, b = Dev("MAC", 1_700_000_000_000), Dev("PHONE", 1_700_000_000_000)
    up = a.op("media.upsert", {"record": rec()})
    b.see(up)
    e1 = a.op("media.set", {"photo_id": P1, "field": "title", "value": "Kudle beach"}, advance=50)
    e2 = b.op("media.set", {"photo_id": P1, "field": "title", "value": "Gokarna sunset"}, advance=90)
    add("lww_same_field", "Two edits of the same field: the higher HLC wins (PHONE's, made later).",
        [up, e1, e2], {"media": {P1: {"title": "Gokarna sunset"}}})
    # 4
    a, b = Dev("MAC", 1_700_000_000_000), Dev("PHONE", 1_700_000_000_000)
    up = a.op("media.upsert", {"record": rec()})
    add("lww_different_fields", "Edits to different fields both survive.",
        [up, a.op("media.set", {"photo_id": P1, "field": "title", "value": "T"}),
         b.op("media.set", {"photo_id": P1, "field": "favorite", "value": True})],
        {"media": {P1: {"title": "T", "favorite": 1}}})
    # 5
    a, b = Dev("MAC", 1_700_000_000_000), Dev("PHONE", 1_700_000_000_000)
    up1 = a.op("media.upsert", {"record": rec(title="Apple title")})
    b.see(up1)
    edit = b.op("media.set", {"photo_id": P1, "field": "title", "value": "Mine"})
    a.see(edit)
    up2 = a.op("media.upsert", {"record": rec(title="Apple title v2")}, advance=1000)
    add("import_never_beats_edit", "A later re-import doesn't override a user edit.",
        [up1, edit, up2], {"media": {P1: {"title": "Mine"}}})
    # 6
    a = Dev("MAC", 1_700_000_000_000)
    add("later_import_beats_earlier_import", "Without edits, the newest import's values win.",
        [a.op("media.upsert", {"record": rec(title="Old", favorite=False)}),
         a.op("media.upsert", {"record": rec(title="New", favorite=True)})],
        {"media": {P1: {"title": "New", "favorite": 1}}})
    # 7
    a, b = Dev("MAC", 1_700_000_000_000), Dev("PHONE", 1_700_000_000_000)
    up = a.op("media.upsert", {"record": rec()})
    add1 = a.op("tag.add", {"photo_id": P1, "tag": "beach"})
    b.see(up, add1)
    rm = b.op("tag.remove", {"photo_id": P1, "tag": "beach", "observed_add_ops": [add1["op_id"]]})
    add2 = a.op("tag.add", {"photo_id": P1, "tag": "Beach "})
    add("concurrent_add_beats_remove", "A remove only cancels the adds it saw; the unseen add survives "
        "(tags are trimmed and lower-cased).", [up, add1, rm, add2], {"tags": {P1: ["beach"]}})
    # 8
    a = Dev("MAC", 1_700_000_000_000)
    up = a.op("media.upsert", {"record": rec()})
    ad = a.op("tag.add", {"photo_id": P1, "tag": "night"})
    add("remove_seen_add", "Removing a tag whose add was seen removes it.",
        [up, ad, a.op("tag.remove", {"photo_id": P1, "tag": "night", "observed_add_ops": [ad["op_id"]]})],
        {"tags": {P1: []}, "fts": {"night": []}})
    # 9
    a = Dev("MAC", 1_700_000_000_000)
    up = a.op("media.upsert", {"record": rec(labels=["Sky", "Sea"])})
    rm = a.op("tag.remove", {"photo_id": P1, "tag": "sky", "observed_add_ops": [f"import:{P1}:apple-label:sky"]})
    re_up = a.op("media.upsert", {"record": rec(labels=["Sky", "Sea"])})
    add("removed_import_tag_stays_removed", "An imported label removed by the user doesn't come back on re-import. "
        "Import add ids are 'import:<photo_id>:<source>:<tag>'.", [up, rm, re_up], {"tags": {P1: ["sea"]}})
    # 10
    a, b = Dev("MAC", 1_700_000_000_000), Dev("PHONE", 1_700_000_000_000)
    up = a.op("media.upsert", {"record": rec(albums=["Goa 2025"])})
    b.see(up)
    add("album_add_remove", "Albums follow the same add/remove rules.",
        [up, b.op("album.remove", {"photo_id": P1, "album": "Goa 2025",
                                   "observed_add_ops": [f"import:{P1}:album:Goa 2025"]}),
         a.op("album.add", {"photo_id": P1, "album": "Best of 2025"})],
        {"albums": {P1: ["Best of 2025"]}})
    # 11
    a, b = Dev("MAC", 1_700_000_000_000), Dev("PHONE", 1_700_000_000_000)
    pa = person_id("Asha")
    up = a.op("media.upsert", {"record": rec(persons=["Asha", "_UNKNOWN_"])})
    b.see(up)
    r1 = a.op("person.set", {"person_id": pa, "field": "name", "value": "Asha P"}, advance=30)
    r2 = b.op("person.set", {"person_id": pa, "field": "name", "value": "Asha Pathak"}, advance=60)
    add("person_rename_lww", "Concurrent renames: higher HLC wins; Apple's _UNKNOWN_ faces are ignored. "
        f"Imported person ids are 'p_' + sha1(lower(name))[:16] (here {pa}).",
        [up, r1, r2], {"people": {pa: {"name": "Asha Pathak", "resolves_to": pa}},
                       "photo_people": {P1: ["Asha Pathak"]}, "counts": {"people": 1}})
    # 12
    a = Dev("MAC", 1_700_000_000_000)
    ops = [a.op("media.upsert", {"record": rec()}),
           a.op("person.create", {"person_id": "p1", "name": "One"}),
           a.op("person.create", {"person_id": "p2", "name": "Two"}),
           a.op("person.create", {"person_id": "p3", "name": "Three"}),
           a.op("media.person.add", {"photo_id": P1, "person_id": "p1"}),
           a.op("person.merge", {"from": "p1", "into": "p2"}),
           a.op("person.merge", {"from": "p2", "into": "p3"})]
    add("person_merge_chain", "Merges chain: p1 → p2 → p3, so a photo tagged p1 shows Three.",
        ops, {"people": {"p1": {"resolves_to": "p3"}, "p2": {"resolves_to": "p3"}},
              "photo_people": {P1: ["Three"]}, "fts": {"three": [P1], "one": []}})
    # 13
    a, b = Dev("MAC", 1_700_000_000_000), Dev("PHONE", 1_700_000_000_000)
    ops = [a.op("person.create", {"person_id": "px", "name": "X"}),
           b.op("person.create", {"person_id": "py", "name": "Y"}),
           a.op("person.merge", {"from": "px", "into": "py"}),
           b.op("person.merge", {"from": "py", "into": "px"})]
    add("person_merge_cycle", "Concurrent opposite merges form a cycle; both resolve to the smallest id.",
        ops, {"people": {"px": {"resolves_to": "px"}, "py": {"resolves_to": "px"}}})
    # 14
    a, b = Dev("MAC", 1_700_000_000_000), Dev("PHONE", 1_700_000_000_000)
    up = a.op("media.upsert", {"record": rec()})
    b.see(up)
    d = a.op("media.delete", {"photo_id": P1})
    e = b.op("media.set", {"photo_id": P1, "field": "title", "value": "Edited"})
    add("delete_vs_edit", "Delete and a concurrent edit: still deleted, edit kept, hidden from search.",
        [up, d, e], {"media": {P1: {"deleted": 1, "title": "Edited"}}, "fts": {"gokarna": []}})
    # 15
    a.see(e)
    r = a.op("media.restore", {"photo_id": P1})
    add("restore_after_delete", "A restore with a higher HLC brings the item back.",
        [up, d, e, r], {"media": {P1: {"deleted": 0, "title": "Edited"}}, "fts": {"gokarna": [P1]}})
    # 16
    a = Dev("MAC", 1_700_000_000_000)
    ops = [a.op("media.upsert", {"record": rec()}), a.op("tag.add", {"photo_id": P1, "tag": "x"})]
    add("duplicate_delivery", "Ops delivered many times are applied once (runners also duplicate every vector).",
        ops + ops + ops, {"counts": {"ops": 2, "media_tags": 1}})
    # 17
    a, b = Dev("MAC", 1_700_000_000_000), Dev("PHONE", 1_700_000_000_000)
    up = a.op("media.upsert", {"record": rec()})
    b.see(up)
    add("edit_before_upsert_is_deferred", "An edit that arrives before its photo waits, then applies.",
        [up, b.op("media.set", {"photo_id": P1, "field": "hidden", "value": True})],
        {"media": {P1: {"hidden": 1}}, "counts": {"deferred_ops": 0}})
    # 18
    a = Dev("MAC", 1_700_000_000_000)
    ops = [a.op("device.register", {"device_id": "MAC", "name": "Mac mini", "platform": "macos"}),
           a.op("device.set", {"device_id": "MAC", "field": "name", "value": "Studio Mac"})]
    add("device_register_and_rename", "Devices are registered and renamed with the same LWW rule.",
        ops, {"devices": {"MAC": {"name": "Studio Mac", "platform": "macos", "first_seen_hlc": ops[0]["hlc"],
                                  "last_seen_hlc": ops[1]["hlc"]}}})
    # 19
    a, b = Dev("MAC", 1_700_000_000_000), Dev("PHONE", 1_700_000_000_000)
    up = a.op("media.upsert", {"record": rec()})
    ad = a.op("tag.add", {"photo_id": P1, "tag": "sea"})
    b.see(up, ad)
    r1 = a.op("tag.remove", {"photo_id": P1, "tag": "sea", "observed_add_ops": [ad["op_id"]]})
    r2 = b.op("tag.remove", {"photo_id": P1, "tag": "sea", "observed_add_ops": [ad["op_id"]]})
    add("two_removes_same_add", "Two removes of one add: the tombstone records the earliest remover.",
        [up, ad, r1, r2], {"tags": {P1: []}, "tombstones": {ad["op_id"]: min(r1["op_id"], r2["op_id"])}})
    # 20
    a = Dev("MAC", 1_700_000_000_000)
    add("person_on_photo_add_remove", "People on a photo use add/remove sets too.",
        [a.op("media.upsert", {"record": rec()}),
         a.op("person.create", {"person_id": "pk", "name": "Kid"}),
         *(lambda o: [o, a.op("media.person.remove", {"photo_id": P1, "person_id": "pk",
                                                       "observed_add_ops": [o["op_id"]]})])(
             a.op("media.person.add", {"photo_id": P1, "person_id": "pk"}))],
        {"photo_people": {P1: []}, "fts": {"kid": []}})
    # 21
    a, b = Dev("MAC", 1_700_000_000_000), Dev("PHONE", 1_700_000_000_000)
    up = a.op("media.upsert", {"record": rec()})
    add("analysis_sources", "Tags from image analysis and people from face recognition keep their source and are "
        "searchable like any other; the user can still remove them.",
        [up, a.op("tag.add", {"photo_id": P1, "tag": "Landscape", "source": "vision"}),
         a.op("person.create", {"person_id": "pa", "name": "Asha"}),
         a.op("media.person.add", {"photo_id": P1, "person_id": "pa", "source": "face"}),
         *(lambda o: [o, b.op("tag.remove", {"photo_id": P1, "tag": "mountain", "observed_add_ops": [o["op_id"]]})])(
             a.op("tag.add", {"photo_id": P1, "tag": "mountain", "source": "vision"}))],
        {"tags": {P1: ["landscape"]}, "sources": {P1: {"landscape": "vision", "asha": "face"}},
         "photo_people": {P1: ["Asha"]}, "fts": {"landscape": [P1], "asha": [P1], "mountain": []}})
    # 22
    a, b = Dev("MAC", 1_700_000_000_000), Dev("PHONE", 1_700_000_000_000)
    up = a.op("media.upsert", {"record": rec()})
    gen1 = a.op("media.caption", {"photo_id": P1, "caption": "A dog running on a beach", "model": "florence-2-base"})
    b.see(up, gen1)
    mine = b.op("media.set", {"photo_id": P1, "field": "caption", "value": "Bruno at Kudle beach"})
    a.see(mine)
    gen2 = a.op("media.caption", {"photo_id": P1, "caption": "A brown dog on the sand"}, advance=1000)
    add("caption_generated_vs_edited", "Generated captions are searchable; a caption the user wrote beats any "
        "generated one, even a later one.",
        [up, gen1, mine, gen2], {"media": {P1: {"caption": "Bruno at Kudle beach"}},
                                 "fts": {"bruno": [P1], "brown": []}})
    a = Dev("MAC", 1_700_000_000_000)
    add("caption_newer_generated_wins", "Without an edit, the newest generated caption wins.",
        [a.op("media.upsert", {"record": rec()}),
         a.op("media.caption", {"photo_id": P1, "caption": "A dog running on a beach"}),
         a.op("media.caption", {"photo_id": P1, "caption": "A brown dog on the sand"})],
        {"media": {P1: {"caption": "A brown dog on the sand"}}, "fts": {"brown": [P1], "running": []}})
    a = Dev("MAC", 1_700_000_000_000)
    add("text_in_image", "Words found in the picture are searchable; a newer reading replaces the older one.",
        [a.op("media.upsert", {"record": rec()}),
         a.op("media.text", {"photo_id": P1, "text": "CROMA Tax Invoice 4,299"}),
         a.op("media.text", {"photo_id": P1, "text": "CROMA Tax Invoice Total 4,299.00"})],
        {"media": {P1: {"text_in_image": "CROMA Tax Invoice Total 4,299.00"}}, "fts": {"croma": [P1], "invoice": [P1]}})
    return V


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for i, v in enumerate(vectors(), 1):
        (OUT / f"{i:02d}_{v['name']}.json").write_text(json.dumps(v, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {i} vectors to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
