from io import BytesIO

import httpx
import respx
from PIL import Image

from morning_edition.sources import articles

BODY = " ".join(f"Sentence number {i} explains the seawall plan in plain words." for i in range(40))
PAGE = f"""<html><head><title>Seawall</title>
<meta property="og:image" content="https://example.com/lead.jpg"></head>
<body><nav>Home | News</nav><article><h1>Seawall vote</h1><p>{BODY}</p></article>
<footer>Copyright</footer></body></html>"""


def png_bytes(width, height):
    buf = BytesIO()
    Image.new("RGB", (width, height), "gray").save(buf, "PNG")
    return buf.getvalue()


def test_extract_article_gets_text_and_image():
    art = articles.extract_article(PAGE, "https://example.com/seawall")
    assert art is not None
    assert art.word_count >= 150
    assert "seawall plan" in art.text
    assert art.image_url == "https://example.com/lead.jpg"


def test_extract_article_rejects_short_pages():
    assert articles.extract_article("<html><body><p>Subscribe to read.</p></body></html>", "https://x.com") is None


@respx.mock
def test_fetch_article_handles_errors_and_non_html():
    respx.get("https://example.com/a").mock(return_value=httpx.Response(200, html=PAGE))
    respx.get("https://example.com/pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}))
    respx.get("https://example.com/gone").mock(return_value=httpx.Response(410))
    with httpx.Client() as client:
        assert articles.fetch_article(client, "https://example.com/a").word_count >= 150
        assert articles.fetch_article(client, "https://example.com/pdf") is None
        assert articles.fetch_article(client, "https://example.com/gone") is None


@respx.mock
def test_download_image_enforces_min_width(tmp_path):
    respx.get("https://example.com/big.png").mock(return_value=httpx.Response(200, content=png_bytes(800, 500)))
    respx.get("https://example.com/small.png").mock(return_value=httpx.Response(200, content=png_bytes(300, 200)))
    respx.get("https://example.com/junk.png").mock(return_value=httpx.Response(200, content=b"not an image"))
    with httpx.Client() as client:
        assert articles.download_image(client, "https://example.com/big.png", tmp_path / "a.img") == tmp_path / "a.img"
        assert articles.download_image(client, "https://example.com/small.png", tmp_path / "b.img") is None
        assert articles.download_image(client, "https://example.com/junk.png", tmp_path / "c.img") is None


@respx.mock
def test_fetch_article_gives_up_on_a_page_over_the_size_cap():
    respx.get("https://example.com/huge").mock(return_value=httpx.Response(
        200, html=PAGE + "<!-- " + "x" * 2_100_000 + " -->"))
    respx.get("https://example.com/ok").mock(return_value=httpx.Response(200, html=PAGE))
    with httpx.Client() as client:
        assert articles.fetch_article(client, "https://example.com/huge") is None
        assert articles.fetch_article(client, "https://example.com/ok") is not None
