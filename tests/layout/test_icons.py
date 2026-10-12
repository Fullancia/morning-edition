import re
import xml.etree.ElementTree as ET

import pytest

from morning_edition.layout import icons
from morning_edition.sources import weather

SVG = "{http://www.w3.org/2000/svg}"
EM_DASH = chr(0x2014)


def parse(svg: str) -> ET.Element:
    return ET.fromstring(svg)


def test_every_wmo_code_maps_to_a_known_kind():
    for code in weather._WMO:
        assert icons.kind_for_code(code) in icons.ICONS, code


@pytest.mark.parametrize("codes,kind", [
    ((0,), "clear"), ((1, 2), "partly"), ((3,), "cloudy"), ((45, 48), "fog"), ((51, 53, 55), "drizzle"),
    ((61, 63, 65, 80, 81, 82), "rain"), ((56, 57, 66, 67), "sleet"),
    ((71, 73, 75, 77, 85, 86), "snow"), ((95, 96, 99), "storm")])
def test_kind_for_code_mapping(codes, kind):
    assert all(icons.kind_for_code(c) == kind for c in codes)


def test_unknown_code_is_cloudy():
    assert icons.kind_for_code(1234) == "cloudy"
    assert icons.kind_for_code(-1) == "cloudy"


def test_every_icon_is_valid_svg_at_the_requested_size():
    assert set(icons.ICONS) == {"clear", "partly", "cloudy", "fog", "drizzle", "rain", "sleet", "snow", "storm"}
    for kind in icons.ICONS:
        root = parse(icons.icon(kind, "0.17in"))
        assert root.tag == SVG + "svg" and "width:0.17in;height:0.17in" in root.get("style")


def lit_path(svg: str) -> ET.Element:
    (path,) = parse(svg).findall(SVG + "path")
    return path


def outer_sweep(path: ET.Element) -> str:
    return re.search(r"A10\.0,10\.0 0 0 (\d)", path.get("d")).group(1)


def test_moon_variants_are_valid_svg():
    for k, waxing in ((0.0, True), (1.0, True), (0.5, True), (0.5, False), (0.23, True), (0.77, False)):
        root = parse(icons.moon(k, waxing, "0.42in"))
        assert root.tag == SVG + "svg"
    new = parse(icons.moon(0.0, True))
    assert new.findall(SVG + "path") == []                      # nothing lit on a new moon
    full = parse(icons.moon(1.0, True))
    assert [c.get("fill") for c in full.findall(SVG + "circle")] == ["#8a8a8a", "#fff", "none"]
    assert "url(#" not in icons.moon(0.3, True)


def test_moon_dark_side_is_gray_and_outlined():
    circles = parse(icons.moon(0.3, True)).findall(SVG + "circle")
    assert circles[0].get("fill") == "#8a8a8a"
    assert circles[-1].get("fill") == "none" and circles[-1].get("stroke") == "#000"


def test_waxing_moon_is_lit_on_the_right_and_waning_on_the_left():
    for k in (0.23, 0.5, 0.77):
        assert outer_sweep(lit_path(icons.moon(k, True))) == "1"     # clockwise down the right side
        assert outer_sweep(lit_path(icons.moon(k, False))) == "0"    # counterclockwise down the left side


def test_quarter_moon_terminator_is_a_straight_line():
    for waxing in (True, False):
        d = lit_path(icons.moon(0.5, waxing)).get("d")
        assert "L12.0,2.0" in d and d.count("A") == 1


def test_no_em_dashes_in_the_svg_or_the_module():
    for kind in icons.ICONS:
        assert EM_DASH not in icons.icon(kind)
    assert EM_DASH not in icons.moon(0.4, True)
    assert EM_DASH not in open(icons.__file__, encoding="utf-8").read()
