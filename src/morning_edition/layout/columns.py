"""Pour one laid-out story into side-by-side columns as windows onto it.

A story is laid out once at the column width; each column then shows a window of that layout
(see measure.measure_blocks). Windows start at a line's top and end at a line's bottom, so no
line is ever cut, and the columns can be made to end level."""
from dataclasses import dataclass
from itertools import combinations_with_replacement

EPS = 1e-6
MIN_LINES = 3          # a non-empty column holds at least this many lines


@dataclass
class Balanced:
    cols: list[tuple[int, int]]       # per column, the lines it shows: lines[lo:hi]; (k, k) is an empty column
    shown: int                        # lines[:shown] are printed; the rest were trimmed off the end
    bottoms: list[float]              # per column, where it ends, in the same offsets-from-the-top frame
    height: float                     # the lowest bottom

    def window(self, lines: list[tuple[float, float]], i: int) -> tuple[float, float]:
        """(y0, height) of column i's window onto the laid-out story."""
        lo, hi = self.cols[i]
        if lo == hi:
            return 0.0, 0.0
        return lines[lo][0], lines[hi - 1][1] - lines[lo][0]


def _bottom(lines: list[tuple[float, float]], lo: int, hi: int, offset: float) -> float:
    return offset + lines[hi - 1][1] - lines[lo][0] if hi > lo else offset


def _capacity(lines: list[tuple[float, float]], offsets: list[float], cap: float) -> int:
    """How many leading lines the columns hold when each is filled as far as it goes."""
    lo = 0
    for offset in offsets:
        hi = lo
        while hi < len(lines) and _bottom(lines, lo, hi + 1, offset) <= cap + EPS:
            hi += 1
        lo = hi
    return lo


def balance(lines: list[tuple[float, float]], offsets: list[float], cap: float,
            para_ends: list[int] | None = None, tail_slack: int = 9) -> Balanced:
    """Divide lines among columns whose tops sit at the given offsets (all measured from one common
    top) so that nothing hangs below cap.

    If all the lines fit, they are spread to end as high and as level as the line grid allows.
    If not, the text is trimmed at the end: at a paragraph end when one lies within tail_slack
    lines of where the room runs out, otherwise at the last line that fits."""
    n_all = len(lines)
    n = min(n_all, _capacity(lines, offsets, cap))
    if n < n_all and para_ends:
        ends = [e + 1 for e in para_ends if e + 1 <= n and n - (e + 1) <= tail_slack]
        if ends:
            n = max(ends)
    k = len(offsets)
    best: tuple[tuple[float, float], list[tuple[int, int]], list[float]] | None = None
    for cuts in combinations_with_replacement(range(n + 1), k - 1):
        edges = [0, *cuts, n]
        cols = list(zip(edges, edges[1:]))
        sizes = [hi - lo for lo, hi in cols]
        if sizes[0] == 0 and n:
            continue
        if any(sizes[i] == 0 and sizes[i + 1] for i in range(k - 1)):
            continue                                   # an empty column may only trail
        if any(0 < s < min(MIN_LINES, n) for s in sizes):
            continue
        bottoms = [_bottom(lines, lo, hi, o) for (lo, hi), o in zip(cols, offsets)]
        if any(b > cap + EPS for b in bottoms):
            continue
        used = [b for b, (lo, hi) in zip(bottoms, cols) if hi > lo] or bottoms
        key = (round(max(bottoms), 4), round(max(used) - min(used), 4))
        if best is None or key < best[0]:
            best = (key, cols, bottoms)
    if best is None:                                   # nothing satisfies the minimums: fill greedily
        cols, lo = [], 0
        for offset in offsets:
            hi = lo
            while hi < n and _bottom(lines, lo, hi + 1, offset) <= cap + EPS:
                hi += 1
            cols.append((lo, hi))
            lo = hi
        bottoms = [_bottom(lines, lo_, hi_, o) for (lo_, hi_), o in zip(cols, offsets)]
        best = ((0, 0), cols, bottoms)
    _, cols, bottoms = best
    return Balanced(cols, n, bottoms, max(bottoms) if n else min(offsets))
