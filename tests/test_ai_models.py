"""The real SigLIP 2 and Florence-2 models on a few pictures. Slow and needs the models downloaded, so it
only runs when asked:  SHOEBOX_REAL_AI=1 HF_HOME=… uv run pytest tests/test_ai_models.py
Pictures: OpenCV's sample images (SHOEBOX_SAMPLE_IMAGES=<folder with messi.jpg and lena.jpg>)."""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("SHOEBOX_REAL_AI") != "1", reason="set SHOEBOX_REAL_AI=1 to run")


@pytest.fixture(scope="module")
def images():
    from PIL import Image, ImageDraw

    folder = Path(os.environ.get("SHOEBOX_SAMPLE_IMAGES", ""))
    out = {n: Image.open(folder / f"{n}.jpg").convert("RGB") for n in ("messi", "lena") if (folder / f"{n}.jpg").exists()}
    snow = Image.new("RGB", (640, 480), (235, 240, 248))
    ImageDraw.Draw(snow).polygon([(0, 480), (200, 150), (380, 380), (500, 200), (640, 480)], fill=(250, 250, 255))
    out["snow"] = snow
    return out


def test_search_vectors_rank_the_right_picture(images, monkeypatch):
    from shoebox.analyze import ai

    if os.environ.get("HF_HOME"):                       # use an existing download instead of ~/.shoebox/models
        monkeypatch.setattr(ai, "_cache", lambda: os.environ["HF_HOME"])
    emb = ai.Embedder()
    names = list(images)
    vecs = emb.images([images[n] for n in names])
    queries = {"a footballer kicking a ball": "messi", "a woman wearing a hat": "lena", "snowy mountains": "snow"}
    for q, want in queries.items():
        if want not in names:
            continue
        sims = vecs @ emb.text(q)
        assert names[int(sims.argmax())] == want, (q, dict(zip(names, sims.round(3))))
        probs = emb.probability(sims)
        print(q, dict(zip(names, probs.round(4))))


def test_captions_describe_the_picture(images, monkeypatch):
    from shoebox.analyze import ai

    if os.environ.get("HF_HOME"):                       # use an existing download instead of ~/.shoebox/models
        monkeypatch.setattr(ai, "_cache", lambda: os.environ["HF_HOME"])
    cap = ai.Captioner()
    for n, im in images.items():
        text = cap.caption(im)
        print(n, "→", text)
        assert len(text.split()) >= 4 and not text.lower().startswith("the image shows")
