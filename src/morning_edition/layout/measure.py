from collections.abc import Iterator
from dataclasses import dataclass

import weasyprint

PX_PER_IN = 96


def render_document(html: str) -> weasyprint.Document:
    return weasyprint.HTML(string=html, base_url=".").render()


def _walk(doc: weasyprint.Document) -> Iterator[tuple[int, object, object]]:
    # WeasyPrint has no public layout-tree API; _page_box is stable across 60.x-70.x.
    # placeholders=True so boxes inside position:absolute elements are visited too.
    for page_index, page in enumerate(doc.pages):
        for box in page._page_box.descendants(placeholders=True):
            element = getattr(box, "element", None)
            if element is not None:
                yield page_index, element, box


def heights(html: str) -> dict[str, float]:
    """Height in inches of each element with a data-measure attribute (its first box)."""
    out: dict[str, float] = {}
    for _, element, box in _walk(render_document(html)):
        key = element.get("data-measure")
        if key and key not in out:
            out[key] = box.margin_height() / PX_PER_IN
    return out


def widths(html: str) -> dict[str, float]:
    """Width in inches of each element with a data-measure-width attribute (its first box)."""
    out: dict[str, float] = {}
    for _, element, box in _walk(render_document(html)):
        key = element.get("data-measure-width")
        if key and key not in out:
            out[key] = box.margin_width() / PX_PER_IN
    return out


@dataclass
class Extent:
    page: int
    cls: str
    top: float
    bottom: float
    left: float
    right: float
    text: str


def extents(doc: weasyprint.Document, classes: set[str]) -> list[Extent]:
    out: list[Extent] = []
    seen: set[tuple[int, int]] = set()
    for page_index, element, box in _walk(doc):
        names = set((element.get("class") or "").split()) & classes
        if not names or (page_index, id(element)) in seen:
            continue
        seen.add((page_index, id(element)))
        top = box.border_box_y() / PX_PER_IN
        left = box.border_box_x() / PX_PER_IN
        out.append(Extent(
            page=page_index, cls=sorted(names)[0], top=top,
            bottom=top + box.border_height() / PX_PER_IN, left=left,
            right=left + box.border_width() / PX_PER_IN,
            text="".join(element.itertext()).strip()[:60],
        ))
    return out
