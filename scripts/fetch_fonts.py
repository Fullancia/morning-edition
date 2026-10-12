"""Download the bundled fonts (all SIL Open Font License) as static TTFs, plus their licenses.

Run once: python scripts/fetch_fonts.py
The Google Fonts CSS API returns TTF URLs when called with a plain (non-browser) User-Agent
(verified 2026-10-11).
"""
import re
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src/morning_edition/layout/fonts"
CSS_API = "https://fonts.googleapis.com/css2?family="
OFL = "https://raw.githubusercontent.com/google/fonts/main/ofl/{}/OFL.txt"
FAMILIES = [
    ("Pirata+One", "pirataone"),
    ("Manufacturing+Consent", "manufacturingconsent"),
    ("Grenze+Gotisch:wght@700", "grenzegotisch"),
    ("Playfair+Display:wght@900", "playfairdisplay"),
    ("Libre+Caslon+Text:ital,wght@0,400;0,700;1,400", "librecaslontext"),
    ("Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;0,8..60,700;1,8..60,400", "sourceserif4"),
    ("Libre+Franklin:ital,wght@0,400;0,500;0,700;0,800;1,400", "librefranklin"),
]


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.7.1"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    faces = []
    for query, folder in FAMILIES:
        css = get(CSS_API + query).decode()
        for block in re.findall(r"@font-face\s*{[^}]*}", css):
            family = re.search(r"font-family: '([^']+)'", block).group(1)
            style = re.search(r"font-style: (\w+)", block).group(1)
            weight = re.search(r"font-weight: (\d+)", block).group(1)
            url = re.search(r"url\((https://[^)]+\.ttf)\)", block).group(1)
            name = f"{family.replace(' ', '')}-{weight}{'i' if style == 'italic' else ''}.ttf"
            (OUT / name).write_bytes(get(url))
            faces.append(f"@font-face {{ font-family: '{family}'; font-style: {style}; "
                         f"font-weight: {weight}; src: url({name}); }}")
        (OUT / f"OFL-{folder}.txt").write_bytes(get(OFL.format(folder)))
    (OUT / "fonts.css").write_text("\n".join(faces) + "\n")
    print(f"{len(faces)} faces")


if __name__ == "__main__":
    main()
