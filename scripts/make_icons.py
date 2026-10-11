"""Draw the app icons (web/public/icons/*.png) — a framed photo: teal frame, pale sky, sun, hills."""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "web" / "public" / "icons"
TEAL, PALE, BG = (11, 118, 129), (211, 234, 236), (237, 241, 242)


def draw(size: int, maskable: bool) -> Image.Image:
    s = 4  # supersample for smooth edges
    W = size * s
    im = Image.new("RGB", (W, W), TEAL if maskable else BG)
    d = ImageDraw.Draw(im)
    pad = int(W * (0.18 if maskable else 0.08))          # maskable icons keep a safe zone
    if not maskable:
        d.rounded_rectangle([pad, pad, W - pad, W - pad], radius=int(W * 0.18), fill=TEAL)
    inner = pad + int(W * 0.1)
    d.rounded_rectangle([inner, inner, W - inner, W - inner], radius=int(W * 0.05), fill=PALE)
    r = int(W * 0.07)
    cx, cy = inner + int((W - 2 * inner) * 0.3), inner + int((W - 2 * inner) * 0.32)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=TEAL)
    base = W - inner
    width = W - 2 * inner
    d.polygon([(inner, base), (inner + width * 0.38, base - width * 0.42), (inner + width * 0.62, base - width * 0.18),
               (inner + width * 0.78, base - width * 0.3), (W - inner, base)], fill=TEAL)
    return im.resize((size, size), Image.Resampling.LANCZOS)


OUT.mkdir(parents=True, exist_ok=True)
for size in (192, 512):
    draw(size, False).save(OUT / f"icon-{size}.png", optimize=True)
    draw(size, True).save(OUT / f"maskable-{size}.png", optimize=True)
draw(180, True).save(OUT / "apple-touch-icon.png", optimize=True)   # iOS adds its own rounding
print("icons written to", OUT)
