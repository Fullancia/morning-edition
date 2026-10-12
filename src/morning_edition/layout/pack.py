import copy
from dataclasses import dataclass, field, replace

EPS = 1e-6


FOLD_CLEARANCE = 0.06
FOLD_HOLE = 0.5        # pushing a head below the fold may leave a gap this tall; more than that is a hole


MIN_LINES = 3          # body lines kept above a break and below it: no orphans, no widows


@dataclass
class Block:
    key: str
    height: float
    head: float = 0.0     # kicker + headline: must not straddle the fold
    # break units as (top, bottom) offsets from the block's top (head included): the body's line
    # boxes, or an also-block's items. Empty means the block never splits.
    units: list[tuple[float, float]] = field(default_factory=list)
    min_before: int = MIN_LINES
    min_after: int = MIN_LINES
    # indexes into units of the last line of each paragraph; lets a block be cut short at a paragraph end
    para_ends: list[int] = field(default_factory=list)
    kept: int | None = None      # on a trimmed version: how many paragraphs it keeps
    first: int = 0               # units already placed elsewhere: this block carries on from unit `first`

    @property
    def y0(self) -> float:
        """Where the block's remaining part starts in its layout (0, or the top of unit `first`)."""
        return self.units[self.first][0] if self.first else 0.0

    def trims(self) -> list["Block"]:
        """Shorter versions that stop at a paragraph end, longest first: at least two paragraphs, fewer
        than all, and at least half the body. The tail after the body (a source line) is kept."""
        n = len(self.para_ends)
        if n < 3 or not self.units:
            return []
        tail = self.height - self.units[-1][1]
        top = self.units[0][0]
        body = self.units[-1][1] - top
        out = []
        for k in range(n - 1, 1, -1):
            last = self.para_ends[k - 1]
            end = self.units[last][1]
            if end - top < body / 2 - EPS:
                break
            out.append(Block(self.key, end + tail, self.head, self.units[:last + 1], self.min_before,
                             self.min_after, self.para_ends[:k], k))
        return out


@dataclass
class Column:
    keys: list[str] = field(default_factory=list)
    tops: list[float] = field(default_factory=list)
    used: float = 0.0
    # per placed piece: the part of the block shown, as (y0, y1) offsets from the block's top.
    # (0, None) is the whole block; otherwise a window from y0 to y1 (None: to the block's end).
    windows: list[tuple[float, float | None]] = field(default_factory=list)
    kept: list[int | None] = field(default_factory=list)     # per placed piece: paragraphs kept if trimmed
    # fold holes: a head left straddling the fold rather than pushed below it, as (index of the piece in
    # this column, where it starts, where pushing it below the fold would have started it)
    holes: list[tuple[int, float, float]] = field(default_factory=list)


def _hole(col: Column, block: Block, gap: float, fold_at: float | None) -> tuple[float, float] | None:
    """(natural start, start pushed below the fold) when the block's head would straddle the fold and
    pushing it would leave a hole over FOLD_HOLE tall; None otherwise."""
    if fold_at is None or block.first:
        return None
    natural = col.used + (gap if col.keys else 0.0)
    if natural < fold_at < natural + block.head and fold_at + FOLD_CLEARANCE - natural > FOLD_HOLE:
        return natural, fold_at + FOLD_CLEARANCE
    return None


def _start(col: Column, block: Block, gap: float, fold_at: float | None) -> float:
    """Where the block starts: below the column's last piece, with a head that would straddle the fold
    pushed just below it, unless that leaves a hole over FOLD_HOLE tall, which the head straddles instead."""
    start = col.used + (gap if col.keys else 0.0)
    if (fold_at is not None and not block.first and start < fold_at < start + block.head
            and _hole(col, block, gap, fold_at) is None):
        start = fold_at + FOLD_CLEARANCE
    return start


def _plan_parts(block: Block, rooms: list[float]) -> list[tuple[int, int]] | None:
    """Cut a block into consecutive runs of units, one per room, each as tall as its room allows.
    Every run keeps at least min_before units, and the tail at least min_after. None if it cannot be done."""
    units, n = block.units, len(block.units)
    if n - block.first < block.min_before + block.min_after:
        return None
    parts: list[tuple[int, int]] = []
    lo = block.first
    for room in rooms:
        y0 = units[lo][0] if lo else 0.0
        if block.height - y0 <= room + EPS:
            parts.append((lo, n))
            return parts
        hi = next((h for h in range(n - block.min_after, lo + block.min_before - 1, -1)
                   if units[h - 1][1] - y0 <= room + EPS), None)
        if hi is None:
            return None
        parts.append((lo, hi))
        lo = hi
    return None


def _append(col: Column, block: Block, top: float, window: tuple[float, float | None]) -> None:
    while len(col.windows) < len(col.keys):
        col.windows.append((0.0, None))
    while len(col.kept) < len(col.keys):
        col.kept.append(None)
    col.keys.append(block.key)
    col.tops.append(top)
    col.windows.append(window)
    col.kept.append(block.kept)


def _place_whole(col: Column, block: Block, gap: float, fold_at: float | None) -> None:
    start = _start(col, block, gap, fold_at)
    hole = _hole(col, block, gap, fold_at)
    _append(col, block, start, (block.y0, None))
    if hole:
        col.holes.append((len(col.keys) - 1, *hole))
    col.used = start + block.height - block.y0


def _try_split(cols: list[Column], ci: int, block: Block, column_height: float, per_page: int,
               max_columns: int, gap: float, fold_at: float | None, allow_new: bool) -> bool:
    """Start the block in column ci and let it run on into the following columns of the same page,
    each continuation at the top of an empty column. Commits only when the whole block fits."""
    first = cols[ci] if ci < len(cols) else Column()
    if ci >= len(cols) and not (allow_new and ci < max_columns):
        return False
    start = _start(first, block, gap, fold_at)
    hole = _hole(first, block, gap, fold_at)
    rooms = [column_height - start]
    page_end = (ci // per_page + 1) * per_page
    for k in range(ci + 1, page_end):
        if k < len(cols):
            if cols[k].keys:
                break
            rooms.append(column_height - cols[k].used)
        elif allow_new and k < max_columns:
            rooms.append(column_height)
        else:
            break
    parts = _plan_parts(block, rooms)
    if parts is None or len(parts) < 2:
        return False
    units, n = block.units, len(block.units)
    for idx, (lo, hi) in enumerate(parts):
        while ci + idx >= len(cols):
            cols.append(Column())
        col = cols[ci + idx]
        top = start if idx == 0 else col.used
        y0 = units[lo][0] if lo else 0.0
        y1 = units[hi - 1][1] if hi < n else None
        _append(col, block, top, (y0, y1))
        if idx == 0 and hole:
            col.holes.append((len(col.keys) - 1, *hole))
        col.used = top + (block.height if y1 is None else y1) - y0
    return True


GAP_WORTH_FILLING = 3.0    # inches: a foot of a column this tall is worth looking ahead or trimming to fill


def _fits(col: Column, block: Block, column_height: float, gap: float, fold_at: float | None) -> bool:
    return _start(col, block, gap, fold_at) + block.height - block.y0 <= column_height + EPS


def _place_on_page(cols: list[Column], block: Block, pg: int, column_height: float, per_page: int,
                   max_columns: int, gap: float, fold_at: float | None) -> bool:
    """Place a block using only the columns of page pg: whole into the foot of a filled column, else
    starting in the foot of one and running on into the next empty column, else into an empty column."""
    lo, hi = pg * per_page, (pg + 1) * per_page
    here = range(lo, min(hi, len(cols)))
    for i in here:
        if cols[i].keys and _fits(cols[i], block, column_height, gap, fold_at):
            _place_whole(cols[i], block, gap, fold_at)
            return True
    if block.units:
        for i in reversed([i for i in here if cols[i].keys]):
            if _try_split(cols, i, block, column_height, per_page, max_columns, gap, fold_at, True):
                return True
    for i in here:
        if not cols[i].keys and _fits(cols[i], block, column_height, gap, fold_at):
            _place_whole(cols[i], block, gap, fold_at)
            return True
    nxt = max(len(cols), lo)
    if nxt < hi and nxt < max_columns and block.height <= column_height + EPS:
        while len(cols) < nxt:
            cols.append(Column())
        cols.append(Column())
        _place_whole(cols[nxt], block, gap, fold_at)
        return True
    if block.units and block.height > column_height:
        first = next((i for i in here if not cols[i].keys), nxt)
        if first < hi and _try_split(cols, first, block, column_height, per_page, max_columns, gap, fold_at, True):
            return True
    return False


def _page_has_space(cols: list[Column], pg: int, column_height: float, per_page: int, max_columns: int,
                    gap: float) -> bool:
    for k in range(pg * per_page, (pg + 1) * per_page):
        if k >= len(cols):
            if k < max_columns:
                return True
        elif not cols[k].keys or column_height - cols[k].used - gap >= GAP_WORTH_FILLING:
            return True
    return False


def pack(blocks: list[Block], column_height: float, max_columns: int, gap: float,
         columns: list[Column] | None = None, fold_at: float | None = None,
         per_page: int | None = None, prefer_gaps: bool = False,
         fillers: list[Block] | None = None, min_index: int = 0) -> tuple[list[Column], list[str]]:
    """Pack in rank order. Each block goes whole in the first column with room. If none has,
    it starts in the most recently filled column and runs on, between lines, into the next
    column of the same page (never across pages). Failing that it takes a fresh column whole
    (or, when taller than a column, a fresh column and its page's following ones). A block whose
    headline would cross fold_at starts just below it instead. per_page is the columns per page.

    Before a block opens a new page while the current one still has an empty column or a foot of
    a column at least 3 in tall, the space is filled: first by a later block (the rest of blocks,
    then fillers, which are consumed from that list) that fits it whole or as a split, and failing
    that by the block itself cut short at a paragraph end (see Block.trims).

    With prefer_gaps (for the filler that goes in last) empty columns count as fresh wherever they
    are: a block goes whole into the foot of a filled column, else starts in the foot of any filled
    column whose next column on the page is empty, and only then takes an empty column."""
    cols = columns if columns is not None else []
    page = per_page or max_columns
    spare = fillers if fillers is not None else []
    queue = list(blocks)
    dropped: list[str] = []
    while queue:
        block = queue.pop(0)
        last = max((i for i, c in enumerate(cols) if c.keys), default=-1)
        if prefer_gaps:
            gaps = [c for i, c in enumerate(cols) if c.keys and i >= min_index]
        else:
            # columns up to the most recently filled one are gaps; the empty ones after it are fresh
            gaps = cols[:last + 1]
        target = next((c for c in gaps if _fits(c, block, column_height, gap, fold_at)), None)
        if target is not None:
            _place_whole(target, block, gap, fold_at)
            continue
        if block.units and cols:
            starts = ([i for i in range(last, min_index - 1, -1) if cols[i].keys] if prefer_gaps
                      else [max(last, 0)])
            if any(_try_split(cols, i, block, column_height, page, max_columns, gap, fold_at, True)
                   for i in starts):
                continue
        fresh = cols if prefer_gaps else cols[last + 1:]
        index = next((i for i, c in enumerate(cols) if c in fresh and not c.keys and i >= min_index
                      and _fits(c, block, column_height, gap, fold_at)), None)
        if index is None and len(cols) < max_columns and block.height <= column_height + EPS:
            index = len(cols)
        current = last // page if last >= 0 else None

        def fill_page() -> bool:
            """Use the current page's space before this block opens a later page; True if it did something."""
            if prefer_gaps or current is None or not _page_has_space(cols, current, column_height, page,
                                                                     max_columns, gap):
                return False

            def fits_page(candidate: Block) -> bool:
                return _place_on_page(cols, candidate, current, column_height, page, max_columns, gap, fold_at)

            later = next((c for c in [*queue, *spare] if fits_page(c)), None)
            if later is not None:
                (queue if later in queue else spare).remove(later)
                queue.insert(0, block)           # the block is looked at again with that space gone
                return True
            return any(fits_page(t) for t in block.trims())

        if index is not None:
            if current is not None and index // page > current and fill_page():
                continue
            if index >= len(cols):
                cols.append(Column())
            _place_whole(cols[index], block, gap, fold_at)
            continue
        if block.units and block.height > column_height:
            # taller than a column: start it in the next empty column, or failing that at the top of a later page
            start, filled = last + 1, False
            while start < max_columns:
                if not filled and current is not None and start // page > current:
                    filled = True
                    if fill_page():
                        break
                if _try_split(cols, start, block, column_height, page, max_columns, gap, fold_at, True):
                    break
                start = (start // page + 1) * page
            else:
                dropped.append(block.key)
            continue
        dropped.append(block.key)
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


def _pour_back(queue: list[Block], spare: list[Block], cols: list[Column], column_height: float, gap: float,
               fold_at: float | None, per_page: int) -> list[str]:
    """Pour blocks into the gaps of columns already laid out, with no new columns: each goes whole or
    split into a gap; failing that a later block (then a reserve) takes the gap first, and failing
    that the block is cut at a paragraph end to fit. Returns the keys that found no room."""
    def place(block: Block) -> bool:
        return not pack([block], column_height, len(cols), gap, columns=cols, fold_at=fold_at,
                        per_page=per_page, prefer_gaps=True)[1]

    left: list[str] = []
    queue = list(queue)
    while queue:
        block = queue.pop(0)
        if place(block):
            continue
        later = next((c for c in [*queue, *spare] if place(c)), None)
        if later is not None:
            (queue if later in queue else spare).remove(later)
            queue.insert(0, block)
        elif not any(place(t) for t in block.trims()):
            left.append(block.key)
    return left


def _fill_hole(also: Block, cols: list[Column], column_height: float, gap: float) -> tuple[Block | None, int]:
    """Put the also-block's first items into the first fold hole that has room for them: the head
    below is then pushed under the fold and the items sit above it. Items stay whole, and the head
    that follows moves down with everything under it. Returns the rest of the block (the whole block
    if no hole took any; None if every item was used) and the index of the column that was filled
    (0 if none). Holes that stay unfilled keep their head straddling the fold."""
    units = also.units
    for ci, col in enumerate(cols):
        for index, natural, pushed in col.holes:
            if index >= len(col.keys) or abs(col.tops[index] - natural) > EPS or not units or also.first:
                continue
            delta = pushed - natural
            if col.used + delta > column_height + EPS:
                continue                                   # moving the rest down would overflow the column
            room = pushed - natural - gap
            k = sum(1 for _, bottom in units if bottom <= room + EPS)
            if k < 1:
                continue
            col.keys.insert(index, also.key)
            col.tops.insert(index, natural)
            while len(col.windows) < len(col.keys) - 1:
                col.windows.append((0.0, None))
            while len(col.kept) < len(col.keys) - 1:
                col.kept.append(None)
            col.windows.insert(index, (0.0, units[k - 1][1] if k < len(units) else None))
            col.kept.insert(index, None)
            for j in range(index + 1, len(col.keys)):
                col.tops[j] += delta
            col.used += delta
            col.holes = [h for h in col.holes if h[0] != index]
            return (replace(also, first=k) if k < len(units) else None), ci
    return also, 0


def _blank_pages(cols: list[Column], pages: int, per_page: int) -> list[int]:
    return [i for i in range(pages) if not any(c.keys for c in cols[i * per_page:(i + 1) * per_page])]


def plan_inside(primary: list[Block], reserves: list[Block], column_height: float,
                cols_per_page: int, max_pages: int, gap: float,
                fold_at: float | None = None,
                first_page_offset: float = 0.0,
                also: Block | None = None) -> tuple[list[list[Column]], list[str]]:
    """Pack primary, then choose how to make the page count even, then pour the also-block into the
    gaps that are left. Returns pages (lists of columns, padded with empty columns) and the dropped
    primary keys.

    When the primary packing ends on an odd page, several whole plans are built and compared:
    reserves poured into the gaps and the rest on a fresh partner page; reserves all on a fresh partner
    page; or the odd page dropped with its stories (then reserves) poured back into the gaps of the
    pages that stay. A plan with a blank inside page is never chosen. The best plan places the most
    primary blocks, then the most height, then uses fewer pages. A lone odd page is never dropped to
    nothing while a second page is allowed: its lowest-ranked stories move to a partner page instead.

    A first_page_offset > 0 reserves that much of the first page's columns at the top (for a
    full-width band printed above them). Column tops stay offsets from the column region's top,
    so fold_at still means the same place. That first page is then mandatory: it is never dropped,
    and its partner page may be blank."""
    if max_pages <= 0:
        return [], [b.key for b in primary]
    per = cols_per_page
    primary_keys = [b.key for b in primary]
    by_key = {b.key: b for b in [*primary, *reserves]}
    mandatory = first_page_offset > 0
    first_cols = [Column(used=first_page_offset) for _ in range(per)] if mandatory else None
    spare = list(reserves)                   # reserves that fill a page's space ahead of their turn leave this list
    base, _ = pack(primary, column_height, max_pages * per, gap, columns=first_cols, fold_at=fold_at,
                   per_page=per, fillers=spare)
    pages0 = _pages(base, per)
    padded = lambda cols, n: cols + [Column() for _ in range(n * per - len(cols))]   # noqa: E731

    def pour(cols: list[Column], pool: list[Block], pages: int) -> tuple[list[Column], list[Block]]:
        """Reserves into the gaps of pages already laid out; returns the columns and the reserves left."""
        cols = padded(cols, pages)
        _, left = pack(pool, column_height, len(cols), gap, columns=cols, fold_at=fold_at, per_page=per,
                       prefer_gaps=True)
        return cols, [b for b in pool if b.key in set(left)]

    def fresh_page(cols: list[Column], pool: list[Block], pages: int) -> list[Column] | None:
        """A new page of the given reserves behind the pages laid out; None if there is none to fill it."""
        if pages + 1 > max_pages:
            return None
        page, _ = pack(pool, column_height, per, gap, fold_at=fold_at, per_page=per)
        return padded(cols, pages) + padded(page, 1) if page else None

    # candidate plans: (columns, pages), every one with an even page count
    plans: list[tuple[list[Column], int]] = []
    if pages0 % 2 == 0:
        plans.append((*pour(copy.deepcopy(base), spare, pages0)[:1], pages0))
    else:
        poured, left = pour(copy.deepcopy(base), spare, pages0)
        # plans that keep the odd page; reserves poured into the gaps come before reserves on a fresh page
        keep = [fresh_page(poured, left, pages0)]
        if mandatory and pages0 == 1:
            keep.append(padded(poured, 2) if pages0 + 1 <= max_pages else None)   # the band page's partner may be blank
        keep.append(fresh_page(copy.deepcopy(base), spare, pages0))
        for cols in keep:
            if cols is not None:
                plans.append((cols, len(cols) // per))
        if mandatory and pages0 == 1 and pages0 + 1 > max_pages:
            plans.append((poured, 1))                            # no room for a partner: the band page alone
        if not (mandatory and pages0 == 1):
            cols = copy.deepcopy(base)[:(pages0 - 1) * per]
            gone = list(dict.fromkeys(k for col in base[(pages0 - 1) * per:] for k in col.keys))
            back = [b for b in primary if b.key in gone]
            extra = [by_key[k] for k in gone if k not in set(primary_keys)] + spare
            if pages0 > 1:
                _pour_back(back, extra, cols, column_height, gap, fold_at, per)
                cols, _ = pour(cols, extra, pages0 - 1)
            plans.append((padded(cols, pages0 - 1), pages0 - 1))

    def placed_keys(cols: list[Column]) -> set[str]:
        return {k for c in cols for k in c.keys}

    def score(plan: tuple[list[Column], int]) -> tuple[int, float, int]:
        cols, pages = plan
        keys = placed_keys(cols)
        height = sum(c.used for c in cols) - (first_page_offset * per if mandatory and pages else 0.0)
        return sum(k in keys for k in primary_keys), height, -pages

    def blank_free(plan: tuple[list[Column], int]) -> bool:
        cols, pages = plan
        blank = _blank_pages(cols, pages, per)
        return not blank or (mandatory and blank == [1] and pages == 2)

    valid = [p for p in plans if blank_free(p)]
    best = max(valid, key=score) if valid else None
    if (best is None or best[1] == 0) and primary and pages0 == 1 and max_pages >= 2:
        best = _split_page(primary, base, column_height, per, gap, fold_at, first_cols, first_page_offset)
    if best is None:
        best = max(plans, key=score) if plans else ([], 0)
    cols, pages = best
    cols = padded(cols, pages)
    start = 0
    if also is not None and pages:
        also, start = _fill_hole(also, cols, column_height, gap)
    if also is not None and pages:
        # what is left of the also items follows the ones in the hole, never ahead of them
        pack([also], column_height, len(cols), gap, columns=cols, fold_at=fold_at,
             per_page=per, prefer_gaps=True, min_index=start)
    placed = placed_keys(cols[:pages * per])
    dropped = [k for k in primary_keys if k not in placed]
    return [cols[i * per:(i + 1) * per] for i in range(pages)], dropped


def _split_page(primary: list[Block], base: list[Column], column_height: float, per: int, gap: float,
                fold_at: float | None, first_cols: list[Column] | None,
                first_page_offset: float) -> tuple[list[Column], int]:
    """One odd page of stories and nothing to pair it with: move the lowest-ranked stories to a second
    page so that neither page is blank. With only one story on the page, it stays alone with a blank partner."""
    on_page = [b for b in primary if any(b.key in c.keys for c in base)]
    if len(on_page) < 2:
        return base + [Column() for _ in range(2 * per - len(base))], 2
    cols = [Column(used=first_page_offset) for _ in range(per)] if first_cols else []
    for cut in range(len(on_page) - 1, 0, -1):
        first = copy.deepcopy(first_cols) if first_cols else []
        page_one, _ = pack(on_page[:cut], column_height, per, gap, columns=first, fold_at=fold_at, per_page=per)
        page_two, dropped = pack(on_page[cut:], column_height, per, gap, fold_at=fold_at, per_page=per)
        if page_two and not dropped:
            return (page_one + [Column() for _ in range(per - len(page_one))]
                    + page_two + [Column() for _ in range(per - len(page_two))]), 2
    return base + [Column() for _ in range(2 * per - len(base))], 2
