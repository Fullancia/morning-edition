import random
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache

from . import wordlist

BLOCK = "#"


@dataclass
class Slot:
    number: int
    direction: str
    cells: list[tuple[int, int]]
    answer: str = ""


@dataclass
class CrosswordFill:
    grid: list[str]
    slots: list[Slot]

    def numbers(self) -> dict[tuple[int, int], int]:
        return {s.cells[0]: s.number for s in self.slots}


def runs(grid: list[list[str]]) -> list[list[tuple[int, int]]]:
    """Every maximal run of white cells, across then down, including runs shorter than 3."""
    n = len(grid)
    out = []
    for horizontal in (True, False):
        def cell(line: int, pos: int) -> tuple[int, int]:
            return (line, pos) if horizontal else (pos, line)

        for line in range(n):
            pos = 0
            while pos < n:
                r, c = cell(line, pos)
                if grid[r][c] == BLOCK:
                    pos += 1
                    continue
                start = pos
                while pos < n and grid[cell(line, pos)[0]][cell(line, pos)[1]] != BLOCK:
                    pos += 1
                out.append([cell(line, x) for x in range(start, pos)])
    return out


def _connected(grid: list[list[str]]) -> bool:
    n = len(grid)
    cells = [(r, c) for r in range(n) for c in range(n) if grid[r][c] != BLOCK]
    if not cells:
        return False
    seen = {cells[0]}
    stack = [cells[0]]
    while stack:
        r, c = stack.pop()
        for y, x in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
            if 0 <= y < n and 0 <= x < n and grid[y][x] != BLOCK and (y, x) not in seen:
                seen.add((y, x))
                stack.append((y, x))
    return len(seen) == len(cells)


def generate_pattern(rng: random.Random, size: int = 11, max_len: int = 7,
                     max_blocks: int = 34, tries: int = 500) -> list[list[str]] | None:
    """Start open; add symmetric block pairs inside runs longer than max_len, keeping
    every run at least 3 long and the white cells connected."""
    for _ in range(tries):
        g = [["." for _ in range(size)] for _ in range(size)]
        stuck = False
        while True:
            long_runs = [run for run in runs(g) if len(run) > max_len]
            if not long_runs:
                break
            run = rng.choice(long_runs)
            positions = list(range(3, len(run) - 3))
            rng.shuffle(positions)
            for i in positions:
                r, c = run[i]
                if g[r][c] == BLOCK:
                    continue
                mr, mc = size - 1 - r, size - 1 - c
                g[r][c] = g[mr][mc] = BLOCK
                if all(len(x) >= 3 for x in runs(g)) and _connected(g):
                    break
                g[r][c] = g[mr][mc] = "."
            else:
                stuck = True
                break
        if not stuck and sum(row.count(BLOCK) for row in g) <= max_blocks:
            return g
    return None


def number_slots(pattern: list[list[str]]) -> list[Slot]:
    n = len(pattern)

    def white(r, c):
        return 0 <= r < n and 0 <= c < n and pattern[r][c] != BLOCK

    slots: list[Slot] = []
    number = 0
    for r in range(n):
        for c in range(n):
            if not white(r, c):
                continue
            across = not white(r, c - 1) and white(r, c + 1)
            down = not white(r - 1, c) and white(r + 1, c)
            if not (across or down):
                continue
            number += 1
            if across:
                end = c
                while white(r, end):
                    end += 1
                slots.append(Slot(number, "across", [(r, x) for x in range(c, end)]))
            if down:
                end = r
                while white(end, c):
                    end += 1
                slots.append(Slot(number, "down", [(y, c) for y in range(r, end)]))
    return slots


class WordIndex:
    def __init__(self, words: Iterable[str]):
        self.all_words = tuple(dict.fromkeys(w.upper() for w in words))
        self.by_len: dict[int, list[str]] = defaultdict(list)
        self.pos: dict[tuple[int, int, str], set[str]] = defaultdict(set)
        for w in self.all_words:
            self.by_len[len(w)].append(w)
            for i, ch in enumerate(w):
                self.pos[(len(w), i, ch)].add(w)

    def matches(self, letters: list[str | None]) -> Iterable[str]:
        length = len(letters)
        pool: set[str] | None = None
        for i, ch in enumerate(letters):
            if ch is None:
                continue
            found = self.pos.get((length, i, ch), set())
            pool = found if pool is None else pool & found
            if not pool:
                return ()
        return self.by_len.get(length, ()) if pool is None else pool


def fill_pattern(pattern: list[list[str]], index: WordIndex, rng: random.Random, deadline: float,
                 clock: Callable[[], float] = time.monotonic) -> list[Slot] | None:
    slots = number_slots(pattern)
    letters: dict[tuple[int, int], str] = {}
    used: set[str] = set()
    done = [False] * len(slots)

    def candidates(slot: Slot) -> list[str]:
        return [w for w in index.matches([letters.get(cell) for cell in slot.cells]) if w not in used]

    def solve() -> bool:
        if clock() > deadline:
            raise TimeoutError
        best, best_words = None, None
        for i, slot in enumerate(slots):
            if done[i]:
                continue
            words = candidates(slot)
            if not words:
                return False
            if best is None or len(words) < len(best_words):
                best, best_words = i, words
                if len(words) == 1:
                    break
        if best is None:
            return True
        slot = slots[best]
        rng.shuffle(best_words)
        for word in best_words[:40]:
            previous = {cell: letters.get(cell) for cell in slot.cells}
            for cell, ch in zip(slot.cells, word):
                letters[cell] = ch
            done[best] = True
            used.add(word)
            slot.answer = word
            if solve():
                return True
            used.discard(word)
            done[best] = False
            slot.answer = ""
            for cell, ch in previous.items():
                if ch is None:
                    letters.pop(cell, None)
                else:
                    letters[cell] = ch
        return False

    try:
        return slots if solve() else None
    except TimeoutError:
        return None


@lru_cache(maxsize=1)
def word_index() -> WordIndex:
    return WordIndex(wordlist.load_words())


def build_crossword(index: WordIndex, rng: random.Random, attempt_seconds: float = 20,
                    total_seconds: float = 90,
                    clock: Callable[[], float] = time.monotonic) -> CrosswordFill | None:
    stop = clock() + total_seconds
    while clock() < stop:
        pattern = generate_pattern(rng)
        if pattern is None:
            continue
        slots = fill_pattern(pattern, index, rng, min(stop, clock() + attempt_seconds), clock)
        if slots is None:
            continue
        grid = [row[:] for row in pattern]
        for slot in slots:
            for (r, c), ch in zip(slot.cells, slot.answer):
                grid[r][c] = ch
        return CrosswordFill(grid=["".join(row) for row in grid], slots=slots)
    return None
