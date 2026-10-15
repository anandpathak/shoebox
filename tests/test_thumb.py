from PIL import Image

from conftest import make_jpeg
from shoebox.derive import make_thumb


def test_thumb_is_small_and_has_no_exif(tmp_path):
    src = make_jpeg(tmp_path / "c.jpg", size=(1600, 1200))
    size = make_thumb(src, tmp_path / "t.jpg")
    with Image.open(tmp_path / "t.jpg") as im:
        assert max(im.size) == 320
        assert not im.getexif()
    assert size < 40_000
