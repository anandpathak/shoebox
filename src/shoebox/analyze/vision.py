"""Scene labels from Apple's Vision framework (VNClassifyImageRequest, ~1,300 categories, on-device)."""

from __future__ import annotations

from PIL import Image

from .image import jpeg_bytes


class VisionUnavailable(RuntimeError):
    pass


def classify(im: Image.Image) -> list[tuple[str, float]]:
    """[(identifier, confidence)], most confident first. Identifiers look like 'blue_sky', 'beach'."""
    try:
        import Vision
        from Foundation import NSData
    except ImportError as e:   # not macOS, or pyobjc missing
        raise VisionUnavailable("Apple Vision isn't available (macOS only)") from e
    data = jpeg_bytes(im)
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(NSData.dataWithBytes_length_(data, len(data)), None)
    req = Vision.VNClassifyImageRequest.alloc().init()
    _perform(handler, req)
    return sorted(((o.identifier(), float(o.confidence())) for o in req.results() or []), key=lambda x: -x[1])


def read_text(im: Image.Image, *, max_chars: int = 4000) -> str:
    """Words in the picture (signs, receipts, documents), Vision's accurate recogniser, line by line."""
    try:
        import Vision
        from Foundation import NSData
    except ImportError as e:
        raise VisionUnavailable("Apple Vision isn't available (macOS only)") from e
    data = jpeg_bytes(im)
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(NSData.dataWithBytes_length_(data, len(data)), None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    _perform(handler, req)
    lines = []
    for o in req.results() or []:
        c = o.topCandidates_(1)
        if c and c[0].confidence() >= 0.5:
            lines.append(str(c[0].string()))
    return useful_text(" ".join(lines))[:max_chars]


def useful_text(text: str) -> str:
    """Keep it only if there are real words (not a stray letter on a T-shirt)."""
    words = [w for w in text.split() if sum(ch.isalnum() for ch in w) >= 2]
    return " ".join(text.split()) if len(words) >= 2 else ""


def _perform(handler, req, tries: int = 2) -> None:
    """Vision occasionally fails a request once (seen on the first request in a fresh server); try again."""
    import time

    for attempt in range(tries):
        ok, err = handler.performRequests_error_([req], None)
        if ok:
            return
        if attempt + 1 < tries:
            time.sleep(0.5)
    raise RuntimeError(f"Vision failed: {err}")


def known_labels() -> set[str]:
    import Vision

    req = Vision.VNClassifyImageRequest.alloc().init()
    labels, _ = req.supportedIdentifiersAndReturnError_(None)
    return set(labels or [])
