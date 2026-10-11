from click.testing import CliRunner

from opkit import Dev, fresh, record
from photoctl import cli
from photoctl.catalog.apply import apply_ops
from photoctl.catalog.checksum import checksum


def test_same_ops_same_checksum_regardless_of_order_and_batching(tmp_path, sidecar):
    mac = Dev("MAC")
    rec = record(sidecar)
    ops = [mac.op("media.upsert", {"record": rec}),
           mac.op("tag.add", {"photo_id": rec["photo_id"], "tag": "x"}),
           mac.op("media.set", {"photo_id": rec["photo_id"], "field": "title", "value": "T"})]
    a, b = fresh(tmp_path, "a"), fresh(tmp_path, "b")
    apply_ops(a, ops)
    for op in reversed(ops):
        apply_ops(b, [op])
    assert checksum(a) == checksum(b)


def test_checksum_changes_with_content(tmp_path, sidecar):
    mac = Dev("MAC")
    rec = record(sidecar)
    conn = fresh(tmp_path, "c")
    apply_ops(conn, [mac.op("media.upsert", {"record": rec})])
    before = checksum(conn)
    apply_ops(conn, [mac.op("media.set", {"photo_id": rec["photo_id"], "field": "favorite", "value": False})])
    assert checksum(conn) != before


def test_cli_checksum(tmp_path):
    res = CliRunner().invoke(cli.main, ["catalog", "checksum", "--catalog", str(tmp_path / "x.sqlite")])
    assert res.exit_code == 0 and len(res.output.strip()) == 64
