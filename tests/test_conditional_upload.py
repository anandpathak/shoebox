import threading

from photoctl import s3 as s3mod


def test_only_one_of_two_racing_uploads_creates_the_object(bucket, tmp_path):
    f = tmp_path / "orig.heic"
    f.write_bytes(b"x" * 1000)
    results = []
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        results.append(bucket.create_if_absent(f, "raw/2025/2025-08/X.heic", storage_class="DEEP_ARCHIVE"))

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [False, True]
    head = bucket.head("raw/2025/2025-08/X.heic")
    assert head["ContentLength"] == 1000 and head["StorageClass"] == "DEEP_ARCHIVE"


def test_multipart_path_is_conditional_too(bucket, tmp_path, monkeypatch):
    monkeypatch.setattr(s3mod, "MULTIPART_THRESHOLD", 10)
    monkeypatch.setattr(s3mod, "PART_SIZE", 5 * 1024 * 1024)
    f = tmp_path / "video.mov"
    f.write_bytes(b"v" * (11 * 1024 * 1024))
    assert bucket.create_if_absent(f, "raw/v.mov") is True
    assert bucket.head("raw/v.mov")["ContentLength"] == f.stat().st_size
    assert bucket.create_if_absent(f, "raw/v.mov") is False
    assert bucket.client.list_multipart_uploads(Bucket=bucket.name).get("Uploads", []) == []
