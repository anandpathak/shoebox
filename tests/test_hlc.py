from photoctl.hlc import HLC, Stamp


def test_monotonic_even_when_wall_clock_goes_backwards():
    t = iter([1000, 1000, 900, 900, 1200])
    c = HLC("DEV", clock=lambda: next(t))
    values = [c.now() for _ in range(5)]
    assert values == sorted(values) and len(set(values)) == 5
    assert values[0] == "0000000001000-0000-DEV"
    assert values[2] == "0000000001000-0002-DEV"  # wall went back to 900: counter keeps it ordered


def test_observe_moves_past_remote_values():
    c = HLC("A", clock=lambda: 1000)
    c.observe("0000000005000-0003-B")
    assert c.now() == "0000000005000-0004-A"


def test_ties_broken_by_device_id():
    assert "0000000001000-0000-A" < "0000000001000-0000-B"
    assert Stamp.parse("0000000001000-0000-A") < Stamp.parse("0000000001000-0000-B")


def test_counter_overflow_borrows_next_millisecond():
    c = HLC("A", last="0000000001000-9999-A", clock=lambda: 1000)
    assert c.now() == "0000000001001-0000-A"


def test_restart_resumes_from_persisted_value():
    c = HLC("A", last="0000000009000-0005-A", clock=lambda: 1000)
    assert c.now() > "0000000009000-0005-A"
