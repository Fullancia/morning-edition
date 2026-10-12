from PIL import Image

from morning_edition.layout.halftone import halftone


def solid(path, shade):
    Image.new("RGB", (900, 600), (shade, shade, shade)).save(path)
    return path


def black_fraction(path):
    img = Image.open(path)
    return sum(1 for v in img.getdata() if v == 0) / (img.width * img.height)


def gradient(path):
    img = Image.new("L", (900, 600))
    img.putdata([int(255 * (x / 899)) for _ in range(600) for x in range(900)])
    img.save(path)
    return path


def test_output_is_one_bit_at_print_size(tmp_path):
    out = halftone(gradient(tmp_path / "g.png"), tmp_path / "out.png", 2.35, 1.4)
    img = Image.open(out)
    assert img.mode == "1"
    assert img.size == (705, 420)
    assert round(img.info["dpi"][0]) == 300


def test_darker_input_prints_more_ink(tmp_path):
    dark = halftone(solid(tmp_path / "d.png", 60), tmp_path / "d1.png", 2, 1)
    light = halftone(solid(tmp_path / "l.png", 200), tmp_path / "l1.png", 2, 1)
    assert black_fraction(dark) > black_fraction(light)


def test_gamma_setting_lightens(tmp_path):
    src = gradient(tmp_path / "g.png")
    heavy = halftone(src, tmp_path / "h.png", 2, 1, gamma=1.0)
    lifted = halftone(src, tmp_path / "l.png", 2, 1, gamma=0.5)
    assert black_fraction(lifted) < black_fraction(heavy)
