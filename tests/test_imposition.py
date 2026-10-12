import pytest

from morning_edition.imposition import impose


def test_letter_duplex_is_passthrough():
    assert impose(b"%PDF-1.7 data") == b"%PDF-1.7 data"


def test_unknown_mode():
    with pytest.raises(ValueError):
        impose(b"%PDF", "tabloid-folded")
