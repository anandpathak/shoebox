import pytest

from photoctl.catalog.ops import InvalidOp, make_op, validate
from photoctl.hlc import HLC

PID = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@pytest.fixture
def clock():
    return HLC("DEVA", clock=lambda: 1_700_000_000_000)


@pytest.mark.parametrize("kind,body", [
    ("media.set", {"photo_id": PID, "field": "title", "value": "Beach"}),
    ("media.delete", {"photo_id": PID}),
    ("tag.add", {"photo_id": PID, "tag": "beach"}),
    ("tag.remove", {"photo_id": PID, "tag": "beach", "observed_add_ops": ["x"]}),
    ("album.add", {"photo_id": PID, "album": "Goa"}),
    ("person.create", {"person_id": "p_1", "name": "Asha"}),
    ("person.merge", {"from": "p_1", "into": "p_2"}),
    ("media.person.add", {"photo_id": PID, "person_id": "p_1"}),
    ("device.register", {"device_id": "DEVA", "name": "Mac", "platform": "macos"}),
    ("media.source", {"photo_id": PID, "device_id": "DEVA", "source_ref": "applephotos:1"}),
])
def test_valid_ops(clock, kind, body):
    op = make_op(clock, kind, body)
    assert op["op_id"] == op["hlc"] and op["device_id"] == "DEVA" and op["entity"].split(":")[0] in (
        "media", "person", "device")


@pytest.mark.parametrize("kind,body,msg", [
    ("media.set", {"photo_id": PID, "field": "raw_key", "value": "x"}, "field"),
    ("media.set", {"photo_id": "lowercase", "field": "title", "value": "x"}, "photo_id"),
    ("tag.add", {"photo_id": PID, "tag": ""}, "tag"),
    ("tag.remove", {"photo_id": PID, "tag": "x"}, "observed_add_ops"),
    ("device.register", {"device_id": "D", "name": "x", "platform": "amiga"}, "platform"),
    ("media.upsert", {"record": {"photo_id": PID}}, "keys"),
])
def test_invalid_ops_are_rejected(clock, kind, body, msg):
    with pytest.raises(InvalidOp, match=msg):
        make_op(clock, kind, body)


def test_unknown_kind_and_tampered_envelope(clock):
    with pytest.raises(InvalidOp):
        make_op(clock, "media.explode", {})
    op = make_op(clock, "media.delete", {"photo_id": PID})
    op["hlc"] = "yesterday"
    with pytest.raises(InvalidOp, match="hlc"):
        validate(op)
