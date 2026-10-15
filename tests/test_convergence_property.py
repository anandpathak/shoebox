"""T2.4: random edits on 3 devices with partial syncs; every delivery order must converge."""

import os

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from conftest import SIDECAR
from opkit import Dev, record
from shoebox.catalog import open_catalog
from shoebox.catalog.apply import apply_ops
from shoebox.catalog.checksum import checksum

EXAMPLES = int(os.environ.get("SHOEBOX_PROPERTY_EXAMPLES", "300"))
RECORDS = [record(SIDECAR, n, persons=[f"P{n}"], labels=["Sea"] if n % 2 else []) for n in range(3)]
TAGS = ["beach", "sea", "night"]
PEOPLE = ["p_a", "p_b", "p_c"]

KINDS = ["upsert", "title", "fav", "delete", "restore", "tag+", "tag-", "person", "merge",
         "person+", "person-", "rename", "sync", "drift"]
# Small index ranges + extra weight on syncs and set operations make conflicting histories common.
action = st.tuples(
    st.integers(0, 2),                                                    # device
    st.sampled_from(KINDS + ["sync"] * 4 + ["tag+", "tag-", "person+", "person-"] * 2),
    st.integers(0, 1),                                                    # photo / tag / person / other device
    st.integers(0, 1),                                                    # second argument
)


def simulate(actions):
    devs = [Dev(f"D{i}", start_ms=1_700_000_000_000 + i * 7) for i in range(3)]
    known = [[] for _ in devs]              # ops each device has seen (its own + synced)
    all_ops = []

    def emit(i, kind, body):
        op = devs[i].op(kind, body)
        known[i].append(op)
        all_ops.append(op)

    for i, kind, a, b in actions:
        pid = RECORDS[a]["photo_id"]
        if kind == "upsert":
            emit(i, "media.upsert", {"record": RECORDS[a]})
        elif kind == "title":
            emit(i, "media.set", {"photo_id": pid, "field": "title", "value": f"t{i}{b}"})
        elif kind == "fav":
            emit(i, "media.set", {"photo_id": pid, "field": "favorite", "value": bool(b % 2)})
        elif kind in ("delete", "restore"):
            emit(i, f"media.{kind}", {"photo_id": pid})
        elif kind == "tag+":
            emit(i, "tag.add", {"photo_id": pid, "tag": TAGS[b]})
        elif kind == "tag-":
            seen = [o["op_id"] for o in known[i] if o["kind"] == "tag.add" and o["body"]["photo_id"] == pid
                    and o["body"]["tag"] == TAGS[b]]
            emit(i, "tag.remove", {"photo_id": pid, "tag": TAGS[b], "observed_add_ops": seen})
        elif kind == "person":
            emit(i, "person.create", {"person_id": PEOPLE[a], "name": f"N{i}{b}"})
        elif kind == "rename":
            emit(i, "person.set", {"person_id": PEOPLE[a], "field": "name", "value": f"R{i}{b}"})
        elif kind == "merge":
            emit(i, "person.merge", {"from": PEOPLE[a], "into": PEOPLE[b]})
        elif kind == "person+":
            emit(i, "media.person.add", {"photo_id": pid, "person_id": PEOPLE[b]})
        elif kind == "person-":
            seen = [o["op_id"] for o in known[i] if o["kind"] == "media.person.add"
                    and o["body"]["photo_id"] == pid and o["body"]["person_id"] == PEOPLE[b]]
            emit(i, "media.person.remove", {"photo_id": pid, "person_id": PEOPLE[b], "observed_add_ops": seen})
        elif kind == "sync":                 # device i pulls everything device a knows
            new = [o for o in known[a] if o not in known[i]]
            known[i].extend(new)
            devs[i].see(new)
        elif kind == "drift":                # clocks jump forward or backwards
            devs[i].t += (b - 1) * 60_000
    return all_ops


@settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(actions=st.lists(action, min_size=1, max_size=60), data=st.data())
def test_any_delivery_order_converges(actions, data):
    ops = simulate(actions)
    reference = open_catalog(":memory:")
    apply_ops(reference, ops, trusted=True)
    expected = checksum(reference)

    shuffled = data.draw(st.permutations(ops))

    one_by_one = open_catalog(":memory:")          # worst case: every op arrives on its own
    for op in shuffled:
        apply_ops(one_by_one, [op], trusted=True)
    assert checksum(one_by_one) == expected

    cut = sorted(data.draw(st.lists(st.integers(0, len(ops)), max_size=4)))
    batched = open_catalog(":memory:")             # typical: a few pulled batches
    for i, j in zip([0, *cut], [*cut, len(ops)]):
        apply_ops(batched, shuffled[i:j], trusted=True)
    apply_ops(batched, shuffled[: len(ops) // 2], trusted=True)  # redelivery is harmless
    assert checksum(batched) == expected
