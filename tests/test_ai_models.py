"""The real SigLIP 2 and Florence-2 models on a few pictures. Slow and needs the models downloaded, so it
only runs when asked:  PHOTOCTL_REAL_AI=1 HF_HOME=… uv run pytest tests/test_ai_models.py
Pictures: OpenCV's sample images (PHOTOCTL_SAMPLE_IMAGES=<folder with messi.jpg and lena.jpg>)."""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("PHOTOCTL_REAL_AI") != "1", reason="set PHOTOCTL_REAL_AI=1 to run")


@pytest.fixture(scope="module")
def images():
    from PIL import Image, ImageDraw

    folder = Path(os.environ.get("PHOTOCTL_SAMPLE_IMAGES", ""))
    out = {n: Image.open(folder / f"{n}.jpg").convert("RGB") for n in ("messi", "lena") if (folder / f"{n}.jpg").exists()}
    snow = Image.new("RGB", (640, 480), (235, 240, 248))
    ImageDraw.Draw(snow).polygon([(0, 480), (200, 150), (380, 380), (500, 200), (640, 480)], fill=(250, 250, 255))
    out["snow"] = snow
    return out


def test_search_vectors_rank_the_right_picture(images, monkeypatch):
    from photoctl.analyze import ai

    monkeypatch.setattr(ai, "_cache", lambda: os.environ.get("HF_HOME", ai._cache()))
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
    from photoctl.analyze import ai

    monkeypatch.setattr(ai, "_cache", lambda: os.environ.get("HF_HOME", ai._cache()))
    cap = ai.Captioner()
    for n, im in images.items():
        text = cap.caption(im)
        print(n, "→", text)
        assert len(text.split()) >= 4 and not text.lower().startswith("the image shows")
