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


@dataclass
class Measured:
    height: float                                # inches
    lines: list[tuple[float, float]]             # break units as (top, bottom) from the block's top, inches
    para_ends: list[int]                         # indexes into lines of the last line of each paragraph


def measure_blocks(html: str) -> dict[str, Measured]:
    """Height of each data-measure block plus its break units.

    A unit is one line box of a paragraph, or, when the block holds a list, one list item.
    These are the places a column may end or begin: a window of the block that starts at a unit's
    top and ends at a unit's bottom never cuts a line."""
    from weasyprint.formatting_structure import boxes   # imported here: layout internals, see _walk

    doc = render_document(html)
    owners: dict[str, object] = {}
    for _, element, box in _walk(doc):
        key = element.get("data-measure")
        if key and key not in owners:
            owners[key] = box
    out: dict[str, Measured] = {}
    for key, owner in owners.items():
        top = owner.border_box_y()
        lines: list[tuple[float, float]] = []
        owner_of_line: list[int] = []
        items: list[tuple[float, float]] = []
        seen_items: set[int] = set()
        for box in owner.descendants(placeholders=True):
            element = getattr(box, "element", None)
            if element is None:
                continue
            if element.tag == "p" and isinstance(box, boxes.LineBox):
                target = lines
                owner_of_line.append(id(element))
            elif element.tag == "li" and isinstance(box, boxes.BlockBox) and id(element) not in seen_items:
                seen_items.add(id(element))
                target = items
            else:
                continue
            target.append((box.position_y - top, box.position_y - top + box.height))
        units = items or lines
        ends = [] if items else [i for i, pid in enumerate(owner_of_line)
                                 if i + 1 == len(owner_of_line) or owner_of_line[i + 1] != pid]
        out[key] = Measured(owner.margin_height() / PX_PER_IN,
                            [(round(t / PX_PER_IN, 5), round(b / PX_PER_IN, 5)) for t, b in units], ends)
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
