"""Optional AI models (install with `uv sync --extra ai`): SigLIP 2 for plain-English search, Florence-2
for captions. Both run on this Mac (Apple GPU via PyTorch MPS when available); weights are downloaded once
from Hugging Face into ~/.photoctl/models/hf.

    [analysis]                                        # ~/.photoctl/config.toml (optional)
    search_model  = "google/siglip2-base-patch16-224"
    caption_model = "florence-community/Florence-2-base"
"""

from __future__ import annotations

import math
import os
import threading
import tomllib

import numpy as np
from PIL import Image

from .. import paths

SEARCH_MODEL = "google/siglip2-base-patch16-224"
CAPTION_MODEL = "florence-community/Florence-2-base"
CAPTION_TASK = "<DETAILED_CAPTION>"


def settings() -> dict:
    cfg = paths.config_file()
    data = tomllib.loads(cfg.read_text()).get("analysis", {}) if cfg.exists() else {}
    return {"search_model": data.get("search_model", SEARCH_MODEL),
            "caption_model": data.get("caption_model", CAPTION_MODEL)}


def available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


def _cache() -> str:
    d = paths.home() / "models" / "hf"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _device():
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


def _tensor(out):
    """transformers returns a tensor or a model output depending on version."""
    import torch

    return out if torch.is_tensor(out) else out.pooler_output


class Embedder:
    """Photos and search text → the same vector space (SigLIP 2). Thread-safe."""

    def __init__(self, name: str | None = None):
        import torch
        from transformers import AutoModel, AutoProcessor

        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        self.name = name or settings()["search_model"]
        self.torch, self.device = torch, _device()
        self.processor = AutoProcessor.from_pretrained(self.name, cache_dir=_cache())
        self.model = AutoModel.from_pretrained(self.name, cache_dir=_cache()).to(self.device).eval()
        self.scale = float(self.model.logit_scale.exp().item()) if hasattr(self.model, "logit_scale") else 100.0
        self.bias = float(self.model.logit_bias.item()) if hasattr(self.model, "logit_bias") else 0.0
        self.lock = threading.Lock()

    def _norm(self, t) -> np.ndarray:
        t = _tensor(t)
        t = t / t.norm(dim=-1, keepdim=True)
        return t.float().cpu().numpy()

    def images(self, ims: list[Image.Image]) -> np.ndarray:
        with self.lock, self.torch.no_grad():
            inputs = self.processor(images=ims, return_tensors="pt").to(self.device)
            return self._norm(self.model.get_image_features(**inputs))

    def text(self, query: str) -> np.ndarray:
        with self.lock, self.torch.no_grad():
            inputs = self.processor(text=[query], padding="max_length", max_length=64, truncation=True,
                                    return_tensors="pt").to(self.device)
            return self._norm(self.model.get_text_features(**inputs))[0]

    def probability(self, cosine: np.ndarray) -> np.ndarray:
        """SigLIP's own calibration: how likely the text describes the image (0..1)."""
        return 1 / (1 + np.exp(-(cosine * self.scale + self.bias)))


class Captioner:
    """One- or two-sentence descriptions (Florence-2). Thread-safe."""

    def __init__(self, name: str | None = None):
        import torch
        from transformers import AutoProcessor, Florence2ForConditionalGeneration

        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        self.name = name or settings()["caption_model"]
        self.torch, self.device = torch, _device()
        self.dtype = torch.float16 if self.device == "mps" else torch.float32
        self.processor = AutoProcessor.from_pretrained(self.name, cache_dir=_cache())
        self.model = Florence2ForConditionalGeneration.from_pretrained(
            self.name, cache_dir=_cache(), torch_dtype=self.dtype).to(self.device).eval()
        self.lock = threading.Lock()

    def caption(self, im: Image.Image) -> str:
        with self.lock, self.torch.no_grad():
            inputs = self.processor(text=CAPTION_TASK, images=im, return_tensors="pt").to(self.device, self.dtype)
            ids = self.model.generate(**inputs, max_new_tokens=50, num_beams=3)
            text = self.processor.batch_decode(ids, skip_special_tokens=False)[0]
            out = self.processor.post_process_generation(text, task=CAPTION_TASK, image_size=im.size)
        return tidy(out.get(CAPTION_TASK, "") if isinstance(out, dict) else str(out))


MAX_SENTENCES = 2      # detailed captions drift into made-up detail as they go on; keep the start


def tidy(text: str) -> str:
    """'The image shows a dog on a beach. …' → 'A dog on a beach.' (first sentences only, no unfinished one)"""
    t = " ".join(text.replace("<pad>", "").split()).strip()
    for lead in ("The image shows ", "The image is ", "In this image we can see ", "In this image, we can see ",
                 "This image shows ", "In this picture we can see ", "The picture shows "):
        if t.lower().startswith(lead.lower()):
            t = t[len(lead):]
            break
    sentences = [x.strip() for x in t.replace("! ", ". ").split(". ") if x.strip()]
    if len(sentences) > 1 and not t.rstrip().endswith((".", "!", "?")):
        sentences = sentences[:-1]                      # cut off mid-sentence by the token limit
    t = ". ".join(sentences[:MAX_SENTENCES]).rstrip(".") + "." if sentences else ""
    return (t[:1].upper() + t[1:])[:2000] if t else ""


_models: dict[str, object] = {}
_models_lock = threading.Lock()


def embedder() -> Embedder:
    with _models_lock:
        if "embed" not in _models:
            _models["embed"] = Embedder()
        return _models["embed"]  # type: ignore[return-value]


def captioner() -> Captioner:
    with _models_lock:
        if "caption" not in _models:
            _models["caption"] = Captioner()
        return _models["caption"]  # type: ignore[return-value]


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))
