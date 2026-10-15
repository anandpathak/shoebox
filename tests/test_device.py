import re

from shoebox.device import load_device, new_ulid


def test_ulid_is_sortable_and_crockford():
    a, b = new_ulid(1_000), new_ulid(2_000)
    assert len(a) == 26 and re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", a)
    assert a < b


def test_device_is_stable(shoebox_home):
    first = load_device()
    second = load_device()
    assert first.device_id == second.device_id
    assert (shoebox_home / "device.json").exists()
    assert first.platform in ("macos", "linux", "windows", "unknown")


def test_load_device_without_create_returns_none():
    assert load_device(create=False) is None
