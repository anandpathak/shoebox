"""T3.9: two OS processes (two device ids, two catalogs) archive, edit and sync concurrently."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

WORKER = Path(__file__).parent / "workers" / "sync_worker.py"
DEVICES = ("01MULTIAAAAAAAAAAAAAAAAAAA", "01MULTIBBBBBBBBBBBBBBBBBBB")


@pytest.fixture(scope="module")
def s3_server():
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.mark.parametrize("run", range(3))
def test_two_processes_converge(run, s3_server, tmp_path, monkeypatch):
    env = {**os.environ, "AWS_ENDPOINT_URL": s3_server, "AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x",
           "AWS_DEFAULT_REGION": "us-east-1"}
    env.pop("AWS_PROFILE", None)
    import boto3

    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    s3 = boto3.client("s3", endpoint_url=s3_server, region_name="us-east-1")
    if "multi-proc" in [b["Name"] for b in s3.list_buckets()["Buckets"]]:
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket="multi-proc"):
            for o in page.get("Contents", []):
                s3.delete_object(Bucket="multi-proc", Key=o["Key"])
    else:
        s3.create_bucket(Bucket="multi-proc")

    procs = []
    for i, dev in enumerate(DEVICES):
        home = tmp_path / dev
        procs.append(subprocess.Popen([sys.executable, str(WORKER), dev, str(run * 10 + i), str(tmp_path / f"{dev}.json")],
                                      env={**env, "PHOTOCTL_HOME": str(home)}, stderr=subprocess.PIPE, text=True))
    for p in procs:
        assert p.wait(timeout=120) == 0, p.stderr.read()

    # a final round so each side has the other's last batch
    from photoctl.catalog import open_catalog
    from photoctl.catalog.checksum import checksum
    from photoctl.device import Device
    from photoctl.s3 import Bucket
    from photoctl.sync import sync_once

    bucket = Bucket("multi-proc", client=s3)
    sums = []
    for dev in DEVICES:
        conn = open_catalog(tmp_path / dev / "catalog.sqlite")
        sync_once(conn, bucket, Device(dev, f"Mac {dev[-1]}", "macos", "x"))
        sums.append(checksum(conn))
    assert sums[0] == sums[1]

    raw = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket="multi-proc", Prefix="raw/")
           for o in page.get("Contents", [])]
    uploads = sum(json.loads((tmp_path / f"{d}.json").read_text())["uploaded"] for d in DEVICES)
    assert uploads == len(raw)  # every original uploaded exactly once despite the overlap
    assert len(raw) < 16        # the two Macs really did share photos
    conn = open_catalog(tmp_path / DEVICES[0] / "catalog.sqlite")
    assert conn.execute("SELECT COUNT(DISTINCT device_id) FROM media_sources").fetchone()[0] == 2
