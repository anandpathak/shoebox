"""The app used to be called photoctl: an existing ~/.photoctl is moved to ~/.shoebox once."""

from shoebox import paths


def test_old_home_is_moved_once(tmp_path, monkeypatch):
    monkeypatch.delenv("SHOEBOX_HOME", raising=False)
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    old = tmp_path / ".photoctl"
    old.mkdir()
    (old / "device.json").write_text('{"device_id": "X"}')
    assert paths.home() == tmp_path / ".shoebox"
    assert (tmp_path / ".shoebox" / "device.json").read_text() == '{"device_id": "X"}'
    assert not old.exists()
    old.mkdir()                                     # a stray old folder later on is left alone
    assert paths.home() == tmp_path / ".shoebox" and old.exists()
