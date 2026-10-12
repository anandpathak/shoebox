"""Find faces and fingerprint them with OpenCV: YuNet (detector, MIT) + SFace (128-d embedding, Apache-2.0).

Models are downloaded once from the OpenCV model zoo to ~/.photoctl/models and checked against pinned
SHA-256 hashes before use.
"""

from __future__ import annotations

import hashlib
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .. import paths

ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
MODELS = {
    "yunet": (f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
              "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "sface": (f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
              "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}
MIN_SCORE = 0.85        # detector confidence
MIN_SIDE = 40           # px in the 1600px image; smaller faces give unreliable fingerprints


@dataclass
class Face:
    box: tuple[float, float, float, float]    # x, y, w, h as fractions of the image
    score: float
    embedding: np.ndarray                     # 128 float32, L2-normalised

    @property
    def area(self) -> float:
        return self.box[2] * self.box[3]


def model_path(name: str) -> Path:
    url, sha = MODELS[name]
    folder = paths.home() / "models"
    folder.mkdir(exist_ok=True)
    p = folder / url.rsplit("/", 1)[1]
    if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == sha:
        return p
    tmp = p.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    got = hashlib.sha256(tmp.read_bytes()).hexdigest()
    if got != sha:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded {p.name} doesn't match its expected checksum; not using it")
    tmp.replace(p)
    return p


class FaceEngine:
    """Not thread-safe; one per worker thread."""

    def __init__(self) -> None:
        import cv2

        self.cv2 = cv2
        self.detector = cv2.FaceDetectorYN.create(str(model_path("yunet")), "", (320, 320), MIN_SCORE, 0.3, 200)
        self.recognizer = cv2.FaceRecognizerSF.create(str(model_path("sface")), "")

    def faces(self, im: Image.Image) -> list[Face]:
        bgr = np.ascontiguousarray(np.asarray(im)[:, :, ::-1])
        h, w = bgr.shape[:2]
        self.detector.setInputSize((w, h))
        _, found = self.detector.detect(bgr)
        out = []
        for row in [] if found is None else found:
            x, y, fw, fh, score = float(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[14])
            if score < MIN_SCORE or min(fw, fh) < MIN_SIDE:
                continue
            emb = self.recognizer.feature(self.recognizer.alignCrop(bgr, row)).flatten().astype(np.float32)
            emb /= np.linalg.norm(emb) or 1.0
            out.append(Face((max(x, 0) / w, max(y, 0) / h, fw / w, fh / h), score, emb))
        return out


_local = threading.local()


def engine() -> FaceEngine:
    if not hasattr(_local, "engine"):
        _local.engine = FaceEngine()
    return _local.engine
