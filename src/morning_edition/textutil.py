import html
import re

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_EM_DASH = re.compile(r"\s*\u2014\s*")


def clean_text(raw: str, limit: int = 600) -> str:
    """Strip HTML tags and entities, collapse whitespace, cut at a word boundary."""
    text = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", raw or ""))).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


def no_em_dash(text: str) -> str:
    """House style: never print an em dash."""
    return _EM_DASH.sub(", ", text)
