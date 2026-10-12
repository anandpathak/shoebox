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
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision failed: {err}")
    return sorted(((o.identifier(), float(o.confidence())) for o in req.results() or []), key=lambda x: -x[1])


def known_labels() -> set[str]:
    import Vision

    req = Vision.VNClassifyImageRequest.alloc().init()
    labels, _ = req.supportedIdentifiersAndReturnError_(None)
    return set(labels or [])
