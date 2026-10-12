"""Weather icons and the moon as inline SVG (black line art for a mono laser).

Every string here is built from the constants and numbers in this file, never from feed text."""
import math

STROKE = 'fill="none" stroke="#000" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"'
STROKE_WHITE = STROKE.replace('fill="none"', 'fill="#fff"')
MOON_DARK = "#8a8a8a"

CLOUD = "M7 18h10.5a3.5 3.5 0 0 0 .6-6.95A5 5 0 0 0 8.4 9.6 4 4 0 0 0 7 18z"
CLOUD_HIGH = "M7 14.5h10.5a3.5 3.5 0 0 0 .6-6.95A5 5 0 0 0 8.4 6.1 4 4 0 0 0 7 14.5z"


def _svg(body: str, size: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'style="width:{size};height:{size}">{body}</svg>')


def _sun(cx=12, cy=12, r=4, ray_in=6.2, ray_out=8.6, rays=8):
    parts = [f'<circle cx="{cx}" cy="{cy}" r="{r}" {STROKE}/>']
    for i in range(rays):
        a = 2 * math.pi * i / rays
        parts.append(f'<line x1="{cx + ray_in * math.cos(a):.2f}" y1="{cy + ray_in * math.sin(a):.2f}" '
                     f'x2="{cx + ray_out * math.cos(a):.2f}" y2="{cy + ray_out * math.sin(a):.2f}" {STROKE}/>')
    return "".join(parts)


def _drops(points, length=2.6):
    return "".join(f'<line x1="{x}" y1="{y}" x2="{x - 1}" y2="{y + length}" {STROKE}/>' for x, y in points)


def _flakes(points, s=1.3):
    out = []
    for x, y in points:
        for a in (0, 60, 120):
            r = math.radians(a)
            out.append(f'<line x1="{x - s * math.cos(r):.2f}" y1="{y - s * math.sin(r):.2f}" '
                       f'x2="{x + s * math.cos(r):.2f}" y2="{y + s * math.sin(r):.2f}" '
                       f'fill="none" stroke="#000" stroke-width="1.1" stroke-linecap="round"/>')
    return "".join(out)


ICONS = {
    "clear": _sun(),
    "partly": _sun(cx=9, cy=8.5, r=3.2, ray_in=5, ray_out=6.8) + f'<path d="{CLOUD}" {STROKE_WHITE}/>',
    "cloudy": f'<path d="{CLOUD}" {STROKE}/>',
    "fog": "".join(f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" {STROKE}/>'
                   for x1, x2, y in ((4, 20, 8), (2.5, 17, 12), (6, 21.5, 16), (4, 15, 20))),
    "drizzle": f'<path d="{CLOUD_HIGH}" {STROKE}/>' + _drops([(9, 18), (15, 18)], 2),
    "rain": f'<path d="{CLOUD_HIGH}" {STROKE}/>' + _drops([(8, 17.5), (12, 17.5), (16, 17.5)], 3.6),
    "sleet": f'<path d="{CLOUD_HIGH}" {STROKE}/>' + _drops([(8, 17.5), (16, 17.5)], 3) + _flakes([(12, 19.5)]),
    "snow": f'<path d="{CLOUD_HIGH}" {STROKE}/>' + _flakes([(8, 18.5), (12, 21), (16, 18.5)]),
    "storm": f'<path d="{CLOUD_HIGH}" {STROKE}/>'
             '<path d="M12.5 15.5 10 19.6h3.2l-2 3.9" fill="none" stroke="#000" stroke-width="1.6" '
             'stroke-linecap="round" stroke-linejoin="round"/>',
}


def icon(kind: str, size: str = "0.3in") -> str:
    return _svg(ICONS[kind], size)


# WMO weather codes (Open-Meteo) to an icon kind; anything not listed is overcast
_KIND_BY_CODE = {
    0: "clear", 1: "partly", 2: "partly", 3: "cloudy", 45: "fog", 48: "fog",
    51: "drizzle", 53: "drizzle", 55: "drizzle",
    61: "rain", 63: "rain", 65: "rain", 80: "rain", 81: "rain", 82: "rain",
    56: "sleet", 57: "sleet", 66: "sleet", 67: "sleet",
    71: "snow", 73: "snow", 75: "snow", 77: "snow", 85: "snow", 86: "snow",
    95: "storm", 96: "storm", 99: "storm",
}


def kind_for_code(code: int) -> str:
    return _KIND_BY_CODE.get(code, "cloudy")


def moon(illumination: float, waxing: bool, size: str = "0.5in") -> str:
    """Draw the moon: gray dark disc, lit region on top, outline. illumination 0..1."""
    r, cx, cy = 10.0, 12.0, 12.0
    k = max(0.0, min(1.0, illumination))
    rx = abs(1 - 2 * k) * r
    body = f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{MOON_DARK}"/>'
    if k > 0.005:
        top, bottom = f"{cx},{cy - r}", f"{cx},{cy + r}"
        if k >= 0.995:
            body += f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#fff"/>'
        else:
            # outer edge on the lit side, then the terminator back to the top
            outer_sweep = 1 if waxing else 0
            crescent = k < 0.5
            term_sweep = (0 if crescent else 1) if waxing else (1 if crescent else 0)
            if rx < 0.05:  # quarter moon: the terminator is a straight line
                terminator = f"L{top}"
            else:
                terminator = f"A{rx:.3f},{r} 0 0 {term_sweep} {top}"
            body += f'<path d="M{top} A{r},{r} 0 0 {outer_sweep} {bottom} {terminator} Z" fill="#fff"/>'

    body += f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#000" stroke-width="1"/>'
    return _svg(body, size)
