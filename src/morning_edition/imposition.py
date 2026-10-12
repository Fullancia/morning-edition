"""Arranges 8.5x11 pages onto physical sheets.

v1 prints letter pages duplex, so pages pass through unchanged. A future
"tabloid-folded" mode (two letter pages per 11x17 side, folded) belongs here
and nowhere else.
"""

MODES = ("letter-duplex",)


def impose(pdf: bytes, mode: str = "letter-duplex") -> bytes:
    if mode == "letter-duplex":
        return pdf
    raise ValueError(f"unknown imposition mode {mode!r}")
