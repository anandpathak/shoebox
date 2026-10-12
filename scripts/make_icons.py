"""Draw the app icons — a framed photo: teal frame, pale sky, sun, hills.

Writes web/public/icons/*.png and, when the native projects exist, the iPhone/Android app icons and
splash screens under app/ios and app/android (PNGs are git-ignored, so run this after cloning)."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "public" / "icons"
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


def splash(w: int, h: int) -> Image.Image:
    im = Image.new("RGB", (w, h), BG)
    icon = draw(int(min(w, h) * 0.3), False)
    im.paste(icon, ((w - icon.width) // 2, (h - icon.height) // 2))
    return im


ios = ROOT / "app" / "ios" / "App" / "App" / "Assets.xcassets"
if ios.exists():
    draw(1024, True).save(ios / "AppIcon.appiconset" / "AppIcon-512@2x.png", optimize=True)  # no alpha allowed
    for name in ("splash-2732x2732.png", "splash-2732x2732-1.png", "splash-2732x2732-2.png"):
        splash(2732, 2732).save(ios / "Splash.imageset" / name, optimize=True)
    print("iOS icons written")
res = ROOT / "app" / "android" / "app" / "src" / "main" / "res"
if res.exists():
    for density, size in {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}.items():
        folder = res / f"mipmap-{density}"
        folder.mkdir(exist_ok=True)
        draw(size, False).save(folder / "ic_launcher.png", optimize=True)
        draw(size, False).save(folder / "ic_launcher_round.png", optimize=True)
        draw(size * 9 // 4, True).save(folder / "ic_launcher_foreground.png", optimize=True)  # adaptive: 108dp layer
    splashes = {"drawable": (480, 320)}
    for density, (w, h) in {"mdpi": (320, 480), "hdpi": (480, 800), "xhdpi": (720, 1280),
                            "xxhdpi": (960, 1600), "xxxhdpi": (1280, 1920)}.items():
        splashes[f"drawable-port-{density}"] = (w, h)
        splashes[f"drawable-land-{density}"] = (h, w)
    for folder, (w, h) in splashes.items():
        (res / folder).mkdir(exist_ok=True)
        splash(w, h).save(res / folder / "splash.png", optimize=True)
    print("Android icons written")
