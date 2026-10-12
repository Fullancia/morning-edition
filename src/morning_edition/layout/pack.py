from dataclasses import dataclass, field

EPS = 1e-6


FOLD_CLEARANCE = 0.06


@dataclass
class Block:
    key: str
    height: float
    head: float = 0.0     # kicker + headline: must not straddle the fold


@dataclass
class Column:
    keys: list[str] = field(default_factory=list)
    tops: list[float] = field(default_factory=list)
    used: float = 0.0


def _start(col: Column, block: Block, gap: float, fold_at: float | None) -> float:
    start = col.used + (gap if col.keys else 0.0)
    if fold_at is not None and start < fold_at < start + block.head:
        start = fold_at + FOLD_CLEARANCE
    return start


def pack(blocks: list[Block], column_height: float, max_columns: int, gap: float,
         columns: list[Column] | None = None, fold_at: float | None = None) -> tuple[list[Column], list[str]]:
    """First fit in rank order: each block goes in the first column with room,
    opening a new column only when none has room. A block whose headline would
    cross fold_at starts just below it instead."""
    cols = columns if columns is not None else []
    dropped: list[str] = []
    for block in blocks:
        target = next((c for c in cols
                       if _start(c, block, gap, fold_at) + block.height <= column_height + EPS), None)
        if target is None:
            if len(cols) < max_columns and block.height <= column_height + EPS:
                target = Column()
                cols.append(target)
            else:
                dropped.append(block.key)
                continue
        start = _start(target, block, gap, fold_at)
        target.keys.append(block.key)
        target.tops.append(start)
        target.used = start + block.height
    return cols, dropped


def fit_count(heights: list[float], capacity: float, gap: float = 0.0) -> int:
    """How many leading items fit in capacity, with gap between items."""
    total = 0.0
    for i, h in enumerate(heights):
        total += h + (gap if i else 0.0)
        if total > capacity + EPS:
            return i
    return len(heights)


def _pages(cols: list[Column], per_page: int) -> int:
    return -(-len(cols) // per_page)


def plan_inside(primary: list[Block], reserves: list[Block], column_height: float,
                cols_per_page: int, max_pages: int, gap: float,
                fold_at: float | None = None,
                first_page_offset: float = 0.0) -> tuple[list[list[Column]], list[str]]:
    """Pack primary then, if the page count is odd, reserves; always return an even page count.
    Returns pages (lists of columns, padded with empty columns) and dropped primary keys.

    A first_page_offset > 0 reserves that much of the first page's columns at the top (for a
    full-width band printed above them). Column tops stay offsets from the column region's top,
    so fold_at still means the same place. That first page is then mandatory: it is never dropped."""
    primary_keys = {b.key for b in primary}
    if max_pages <= 0:
        return [], [b.key for b in primary]
    mandatory_first = first_page_offset > 0
    first_cols = [Column(used=first_page_offset) for _ in range(cols_per_page)] if mandatory_first else None
    cols, dropped = pack(primary, column_height, max_pages * cols_per_page, gap,
                         columns=first_cols, fold_at=fold_at)
    pages = _pages(cols, cols_per_page)
    reserves_used = False
    while pages % 2 == 1:
        if not reserves_used and pages + 1 <= max_pages:
            reserves_used = True
            # reserves go only into the new page's fresh columns, never into gaps on earlier pages
            fresh, _ = pack(reserves, column_height, cols_per_page, gap, fold_at=fold_at)
            if fresh:
                while len(cols) < pages * cols_per_page:
                    cols.append(Column())
                cols.extend(fresh)
                pages = _pages(cols, cols_per_page)
            continue
        last = cols[(pages - 1) * cols_per_page:]
        last_fill = sum(c.used for c in last) / (cols_per_page * column_height)
        if (last_fill > 0.5 or (mandatory_first and pages == 1)) and pages + 1 <= max_pages:
            pages += 1                       # keep the content; the final page stays blank
        elif mandatory_first and pages == 1:
            break                            # no room for a partner page; the band page stays
        else:
            for col in last:
                dropped.extend(k for k in col.keys if k in primary_keys)
            cols = cols[:(pages - 1) * cols_per_page]
            pages -= 1
    while len(cols) < pages * cols_per_page:
        cols.append(Column())
    return [cols[i * cols_per_page:(i + 1) * cols_per_page] for i in range(pages)], dropped
