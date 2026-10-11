"""One simulated Mac for the multi-process sync test. Run with PHOTOCTL_HOME and AWS_ENDPOINT_URL set.

argv: <device_id> <seed> <out_json>
"""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import SIDECAR  # noqa: E402
from opkit import record  # noqa: E402
from photoctl.catalog import open_catalog  # noqa: E402
from photoctl.catalog.checksum import checksum  # noqa: E402
from photoctl.catalog.ops import Emitter  # noqa: E402
from photoctl.catalog.store import upsert_record  # noqa: E402
from photoctl.device import Device  # noqa: E402
from photoctl.runlock import SyncLock  # noqa: E402
from photoctl.s3 import Bucket  # noqa: E402
from photoctl.sync import sync_once  # noqa: E402

device_id, seed, out = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
rng = random.Random(seed)
dev = Device(device_id, f"Mac {device_id[-1]}", "macos", "x")
bucket = Bucket("multi-proc")
conn = open_catalog()
files = Path(out.parent / f"files-{device_id}")
files.mkdir(exist_ok=True)
uploaded = 0

photos = rng.sample(range(12), 8)  # each Mac has 8 of the same 12 photos: plenty of overlap
for i, n in enumerate(photos):
    rec = record(SIDECAR, n)
    f = files / f"{n}.heic"
    f.write_bytes(f"photo {n}".encode() * 100)  # same bytes on both Macs for the same photo
    uploaded += bucket.create_if_absent(f, rec["keys"]["raw"], storage_class="DEEP_ARCHIVE")
    upsert_record(conn, rec, dev)
    em = Emitter(conn, device_id)
    target = record(SIDECAR, rng.choice(photos[: i + 1]))["photo_id"]
    choice = rng.random()
    if choice < 0.4:
        em.emit("media.set", {"photo_id": target, "field": "title", "value": f"{device_id}-{i}"})
    elif choice < 0.7:
        em.emit("tag.add", {"photo_id": target, "tag": rng.choice(["beach", "sea", "night"])})
    else:
        seen = [r[0] for r in conn.execute("SELECT add_op FROM media_tags WHERE photo_id = ? AND tag = 'beach'",
                                           (target,))]
        em.emit("tag.remove", {"photo_id": target, "tag": "beach", "observed_add_ops": seen})
    if rng.random() < 0.5:
        with SyncLock():
            sync_once(conn, bucket, dev)

with SyncLock():
    sync_once(conn, bucket, dev)
out.write_text(json.dumps({"uploaded": uploaded}))
