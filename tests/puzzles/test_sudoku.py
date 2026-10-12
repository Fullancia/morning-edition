import random

import pytest

from morning_edition.puzzles import sudoku


def valid_complete(g):
    rows = all(sorted(r) == list(range(1, 10)) for r in g)
    cols = all(sorted(g[r][c] for r in range(9)) == list(range(1, 10)) for c in range(9))
    boxes = all(
        sorted(g[br + i][bc + j] for i in range(3) for j in range(3)) == list(range(1, 10))
        for br in (0, 3, 6) for bc in (0, 3, 6)
    )
    return rows and cols and boxes


@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
def test_generate_unique_and_consistent(difficulty):
    p = sudoku.generate(random.Random(7), difficulty)
    assert valid_complete(p.solution)
    givens = sum(1 for r in p.givens for v in r if v)
    target = sudoku.GIVENS[difficulty]
    assert target <= givens <= target + 4
    assert all(v in (0, p.solution[r][c]) for r, row in enumerate(p.givens) for c, v in enumerate(row))
    assert sudoku.count_solutions(p.givens) == 1


def test_count_solutions_detects_multiple():
    empty = [[0] * 9 for _ in range(9)]
    assert sudoku.count_solutions(empty, limit=2) == 2


def test_generate_is_deterministic_for_seed():
    assert sudoku.generate(random.Random(3)).givens == sudoku.generate(random.Random(3)).givens
