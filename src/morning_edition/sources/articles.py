import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import httpx
import trafilatura
from PIL import Image
from PIL.Image import UnidentifiedImageError, DecompressionBombError

from .feeds import USER_AGENT
from .http import MAX_IMAGE_BYTES, get_capped

MIN_WORDS = 150
MAX_WORDS_FOR_LLM = 2500


@dataclass
class Article:
    url: str
    text: str
    image_url: str | None
    word_count: int


def extract_article(html_text: str, url: str) -> Article | None:
    raw = trafilatura.extract(html_text, output_format="json", with_metadata=True,
                              url=url, include_comments=False)
    if not raw:
        return None
    data = json.loads(raw)
    text = (data.get("text") or "").strip()
    words = text.split()
    if len(words) < MIN_WORDS:
        return None
    if len(words) > MAX_WORDS_FOR_LLM:
        text = " ".join(words[:MAX_WORDS_FOR_LLM])
    return Article(url=url, text=text, image_url=data.get("image") or None, word_count=len(words))


def fetch_article(client: httpx.Client, url: str) -> Article | None:
    try:
        resp = get_capped(client, url, headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    if "html" not in resp.headers.get("content-type", ""):
        return None
    return extract_article(resp.text, str(resp.url))


def download_image(client: httpx.Client, url: str, dest: Path, min_width: int = 600) -> Path | None:
    try:
        resp = get_capped(client, url, max_bytes=MAX_IMAGE_BYTES, headers={"User-Agent": USER_AGENT},
                          timeout=20, follow_redirects=True)
        resp.raise_for_status()
        with Image.open(BytesIO(resp.content)) as img:
            if img.width < min_width:
                return None
    except (httpx.HTTPError, UnidentifiedImageError, DecompressionBombError, OSError):
        return None
    dest.write_bytes(resp.content)
    return dest
