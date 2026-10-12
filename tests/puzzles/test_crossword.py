import random

import pytest

from morning_edition.puzzles import crossword


def grid(rows):
    return [list(r) for r in rows]


def test_number_slots_standard_numbering():
    pattern = grid(["#....", ".....", ".....", ".....", "....#"])
    slots = crossword.number_slots(pattern)
    numbers = {(s.number, s.direction): (s.cells[0], len(s.cells)) for s in slots}
    assert numbers[(1, "across")] == ((0, 1), 4)
    assert numbers[(1, "down")] == ((0, 1), 5)
    assert numbers[(4, "down")] == ((0, 4), 4)
    assert numbers[(5, "across")] == ((1, 0), 5)
    assert numbers[(5, "down")] == ((1, 0), 4)
    assert numbers[(8, "across")] == ((4, 0), 4)
    assert len(slots) == 10


def test_generate_pattern_rules():
    rng = random.Random(1)
    p = crossword.generate_pattern(rng)
    assert p is not None
    n = len(p)
    assert all(p[r][c] == p[n - 1 - r][n - 1 - c] for r in range(n) for c in range(n))
    assert all(3 <= len(run) <= 7 for run in crossword.runs(p))
    assert crossword._connected(p)


def test_fill_small_word_square_uses_distinct_listed_words():
    words = ["BAT", "ORE", "WED", "BOW", "ARE", "TED"]
    pattern = grid(["...", "...", "..."])
    slots = crossword.fill_pattern(pattern, crossword.WordIndex(words), random.Random(0), deadline=float("inf"))
    assert slots is not None
    answers = [s.answer for s in slots]
    assert len(set(answers)) == 6 and set(answers) <= set(words)
    letters = {}
    for s in slots:
        for cell, ch in zip(s.cells, s.answer):
            assert letters.setdefault(cell, ch) == ch


def test_fill_returns_none_when_impossible():
    pattern = grid(["...", "...", "..."])
    assert crossword.fill_pattern(pattern, crossword.WordIndex(["CAT", "DOG"]), random.Random(0),
                                  deadline=float("inf")) is None


def test_build_crossword_with_bundled_list():
    fill = crossword.build_crossword(crossword.word_index(), random.Random(2), total_seconds=90)
    assert fill is not None
    words = set(crossword.word_index().all_words)
    assert all(s.answer in words for s in fill.slots)
    assert len({s.answer for s in fill.slots}) == len(fill.slots)
    assert all(len(row) == 11 for row in fill.grid)
