from morning_edition.layout.columns import balance

L = 1 / 6      # one 12pt line, in inches


def grid(n, gap_after=()):
    """n uniform lines; gap_after maps a line number to the extra space after it (a paragraph margin)."""
    lines, y = [], 0.0
    for i in range(n):
        lines.append((y, y + L))
        y += L + (0.05 if (i + 1) in gap_after else 0.0)
    return lines


def count(b):
    return [hi - lo for lo, hi in b.cols]


def test_a_short_story_is_spread_over_three_columns_that_end_level():
    b = balance(grid(30), [0, 0, 0], cap=10.0)
    assert b.shown == 30 and count(b) == [10, 10, 10]
    assert max(b.bottoms) - min(b.bottoms) < 1e-9 and abs(b.height - 10 * L) < 1e-9


def test_uneven_line_counts_end_within_one_line():
    b = balance(grid(31), [0, 0, 0], cap=10.0)
    assert sorted(count(b)) == [10, 10, 11]
    assert max(b.bottoms) - min(b.bottoms) <= L + 1e-9


def test_columns_below_a_photo_end_level_with_the_first_column():
    photo = 14 * L
    b = balance(grid(30), [0, photo, photo], cap=10.0)
    assert b.shown == 30
    assert max(b.bottoms) - min(b.bottoms) <= L + 1e-9
    n1, n2, n3 = count(b)
    assert n1 > n2 and n1 + n2 + n3 == 30 and abs(n2 - n3) <= 1
    assert b.cols[0][0] == 0 and b.cols[1][0] == b.cols[0][1] and b.cols[2][0] == b.cols[1][1]


def test_a_very_short_story_stays_in_the_first_column_beside_a_photo():
    b = balance(grid(5), [0, 14 * L, 14 * L], cap=10.0)
    assert b.cols == [(0, 5), (5, 5), (5, 5)] and abs(b.height - 14 * L) < 1e-9     # the photo is then the lowest thing


def test_no_non_empty_column_holds_fewer_than_three_lines():
    for n in range(6, 40):
        b = balance(grid(n), [0, 0, 0], cap=10.0)
        assert all(s == 0 or s >= 3 for s in count(b)), (n, count(b))


def test_a_story_too_long_is_trimmed_to_what_fits_without_hanging_below_the_cap():
    cap = 20 * L
    b = balance(grid(100), [0, 0, 0], cap=cap)
    assert b.shown == 60 and count(b) == [20, 20, 20]
    assert all(x <= cap + 1e-9 for x in b.bottoms)
    w = b.window(grid(100), 1)
    assert abs(w[0] - 20 * L) < 1e-9 and abs(w[1] - 20 * L) < 1e-9     # starts at line 21's top, 20 lines tall


def test_trimming_prefers_a_paragraph_end_within_a_few_lines():
    lines = grid(100)
    ends = [54, 79, 99]                      # paragraph ends are line indexes
    b = balance(lines, [0, 0, 0], cap=20 * L, para_ends=ends)
    assert b.shown == 55                     # cuts at the end of the paragraph that ends at index 54
    far = balance(lines, [0, 0, 0], cap=20 * L, para_ends=[30])
    assert far.shown == 60                   # no paragraph end near: cut at the last line that fits


def test_columns_that_start_lower_have_less_room():
    b = balance(grid(100), [0, 8 * L, 8 * L], cap=20 * L)
    assert count(b) == [20, 12, 12] and b.shown == 44


def test_the_window_of_a_column_starts_after_the_paragraph_gap():
    lines = grid(12, gap_after=(4, 8))
    b = balance(lines, [0, 0, 0], cap=10.0)
    assert b.cols == [(0, 4), (4, 8), (8, 12)]
    assert b.window(lines, 1)[0] == lines[4][0]            # the 0.05 gap after line 4 is left out of the window
    assert abs(b.window(lines, 1)[1] - 4 * L) < 1e-9


def test_empty_story():
    b = balance([], [0, 0, 0], cap=5.0)
    assert b.shown == 0 and b.cols == [(0, 0)] * 3
