import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageOps


def halftone(src: Path, out: Path, width_in: float, height_in: float, gamma: float = 0.5,
             dpi: int = 300, lpi: int = 55, angle: float = 45) -> Path:
    """Round-dot newspaper halftone as a 1-bit PNG at exact print size."""
    width, height = round(width_in * dpi), round(height_in * dpi)
    with Image.open(src) as raw:
        img = ImageOps.fit(raw.convert("L"), (width, height), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Contrast(img).enhance(1.15)
    # lift midtones; laser toner spreads and darkens dots on paper
    img = img.point(lambda v: round(255 * (v / 255) ** gamma))
    cell = dpi / lpi
    rotated = img.rotate(angle, expand=True, fillcolor=255)
    canvas = Image.new("L", rotated.size, 255)
    draw = ImageDraw.Draw(canvas)
    pixels = rotated.load()
    rw, rh = rotated.size
    y = 0.0
    while y < rh:
        x = 0.0
        while x < rw:
            cx, cy = min(int(x + cell / 2), rw - 1), min(int(y + cell / 2), rh - 1)
            radius = (cell / 2) * math.sqrt(1 - pixels[cx, cy] / 255) * 1.3
            if radius > 0.4:
                draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=0)
            x += cell
        y += cell
    back = canvas.rotate(-angle, fillcolor=255)
    left, top = (back.width - width) // 2, (back.height - height) // 2
    result = back.crop((left, top, left + width, top + height)).point(lambda v: 0 if v < 128 else 255, "1")
    result.save(out, dpi=(dpi, dpi))
    return out
