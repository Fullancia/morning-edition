import random
from dataclasses import dataclass

GIVENS = {"easy": 38, "medium": 32, "hard": 27}


@dataclass
class Sudoku:
    givens: list[list[int]]
    solution: list[list[int]]


def _options(g: list[list[int]], r: int, c: int) -> list[int]:
    used = set(g[r]) | {g[i][c] for i in range(9)}
    br, bc = 3 * (r // 3), 3 * (c // 3)
    used |= {g[br + i][bc + j] for i in range(3) for j in range(3)}
    return [n for n in range(1, 10) if n not in used]


def count_solutions(grid: list[list[int]], limit: int = 2) -> int:
    g = [row[:] for row in grid]
    count = 0

    def solve() -> None:
        nonlocal count
        best = None
        for r in range(9):
            for c in range(9):
                if g[r][c] == 0:
                    opts = _options(g, r, c)
                    if not opts:
                        return
                    if best is None or len(opts) < len(best[2]):
                        best = (r, c, opts)
        if best is None:
            count += 1
            return
        r, c, opts = best
        for n in opts:
            g[r][c] = n
            solve()
            g[r][c] = 0
            if count >= limit:
                return

    solve()
    return count


def _fill(g: list[list[int]], rng: random.Random) -> bool:
    for r in range(9):
        for c in range(9):
            if g[r][c] == 0:
                opts = _options(g, r, c)
                rng.shuffle(opts)
                for n in opts:
                    g[r][c] = n
                    if _fill(g, rng):
                        return True
                g[r][c] = 0
                return False
    return True


def generate(rng: random.Random, difficulty: str = "medium") -> Sudoku:
    solution = [[0] * 9 for _ in range(9)]
    _fill(solution, rng)
    puzzle = [row[:] for row in solution]
    cells = [(r, c) for r in range(9) for c in range(9)]
    rng.shuffle(cells)
    target = GIVENS[difficulty]
    filled = 81
    for r, c in cells:
        if filled <= target:
            break
        keep = puzzle[r][c]
        puzzle[r][c] = 0
        if count_solutions(puzzle) == 1:
            filled -= 1
        else:
            puzzle[r][c] = keep
    return Sudoku(givens=puzzle, solution=solution)
