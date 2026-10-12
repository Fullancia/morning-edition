from morning_edition.layout.pack import Block, Column, fit_count, pack, plan_inside


def test_pack_first_fit_fills_gaps_in_rank_order():
    blocks = [Block("a", 6), Block("b", 6), Block("c", 3), Block("d", 1)]
    cols, dropped = pack(blocks, column_height=10, max_columns=2, gap=0.5)
    assert [c.keys for c in cols] == [["a", "c"], ["b", "d"]]
    assert dropped == []
    assert cols[0].used == 9.5


def test_pack_pushes_a_headline_below_the_fold():
    blocks = [Block("a", 4.5), Block("b", 3, head=1)]
    cols, _ = pack(blocks, column_height=10, max_columns=1, gap=0.2, fold_at=5)
    assert cols[0].tops[0] == 0
    assert abs(cols[0].tops[1] - 5.06) < 1e-9  # b would have started at 4.7, headline across 5
    assert abs(cols[0].used - 8.06) < 1e-9


def test_pack_drops_what_cannot_fit():
    cols, dropped = pack([Block("a", 8), Block("b", 8), Block("big", 11)], 10, 1, 0.5)
    assert [c.keys for c in cols] == [["a"]]
    assert dropped == ["b", "big"]


def test_fit_count():
    assert fit_count([1, 1, 1], 2.5) == 2
    assert fit_count([1, 1, 1], 3) == 3
    assert fit_count([1, 1], 2.4, gap=0.5) == 1
    assert fit_count([], 3) == 0


def test_plan_inside_reserves_fill_the_next_page():
    primary = [Block(f"p{i}", 9) for i in range(2)]   # one page, odd
    reserves = [Block(f"r{i}", 9) for i in range(3)]
    pages, dropped = plan_inside(primary, reserves, column_height=10, cols_per_page=3, max_pages=4, gap=0.2)
    assert len(pages) == 2
    keys = [k for page in pages for col in page for k in col.keys]
    assert keys == ["p0", "p1", "r0", "r1", "r2"]
    assert dropped == []


def test_plan_inside_reserves_fill_gaps_on_earlier_pages_before_a_new_page():
    primary = [Block(f"p{i}", 8) for i in range(6)]   # 2 full pages, 2 units free at the foot of every column
    reserves = [Block(f"r{i}", 1.5) for i in range(4)]  # fit those gaps, so they go there
    pages, dropped = plan_inside(primary, reserves, 10, 3, 4, 0.2)
    assert dropped == [] and len(pages) == 2
    keys = [[k for col in page for k in col.keys] for page in pages]
    assert {k for page in keys for k in page if k.startswith("r")} == {"r0", "r1", "r2", "r3"}
    assert pages[0][0].keys == ["p0", "r0"] and abs(pages[0][0].used - 9.7) < 1e-9


def test_plan_inside_reserves_too_big_for_any_gap_still_open_a_new_page():
    primary = [Block(f"p{i}", 8) for i in range(9)]   # three full pages, 2 units free at every column foot
    reserves = [Block(f"r{i}", 5) for i in range(3)]
    pages, dropped = plan_inside(primary, reserves, 10, 3, 4, 0.2)
    keys = [[k for col in page for k in col.keys] for page in pages]
    assert len(pages) == 4 and set(keys[3]) == {"r0", "r1", "r2"}
    assert all(not k.startswith("r") for page in keys[:3] for k in page)


def test_plan_inside_drops_mostly_empty_odd_page_without_reserves():
    primary = [Block(f"p{i}", 9) for i in range(7)]   # 3 pages, the third one column (30% full)
    pages, dropped = plan_inside(primary, [], 10, 3, 4, 0.2)
    assert len(pages) == 2
    assert dropped == ["p6"]


def test_plan_inside_never_prints_a_blank_page_even_when_the_odd_page_is_over_half_full():
    primary = [Block(f"p{i}", 9) for i in range(8)]   # 3 pages, the third two columns (60% full), no reserves
    pages, dropped = plan_inside(primary, [], 10, 3, 4, 0.2)
    assert len(pages) == 2 and dropped == ["p6", "p7"]
    assert all(col.keys for page in pages for col in page)


def test_plan_inside_a_full_odd_page_with_no_partner_available_is_dropped_not_followed_by_a_blank():
    primary = [Block(f"p{i}", 9) for i in range(9)]   # three full pages, no reserves, no fourth page
    pages, dropped = plan_inside(primary, [], 10, 3, 3, 0.2)
    assert len(pages) == 2 and dropped == ["p6", "p7", "p8"]


def test_plan_inside_keeps_the_odd_page_when_reserves_can_fill_a_partner_page():
    # repro: reserves poured into the empty column used to leave page one odd and drop everything
    primary = [Block(f"p{i}", 3.5) for i in range(4)]
    reserves = [Block(f"r{i}", 1.5) for i in range(4)]
    pages, dropped = plan_inside(primary, reserves, 8.0, 3, 4, 0.12)
    assert dropped == [] and len(pages) == 2
    placed = [k for page in pages for c in page for k in c.keys]
    assert [k for k in placed if k.startswith("p")] == ["p0", "p1", "p2", "p3"]
    assert all(any(c.keys for c in page) for page in pages)              # no blank page


def test_plan_inside_never_returns_zero_pages_for_primary_content():
    for reserves in ([], [Block("r0", 1.5)], [Block(f"r{i}", 1.5) for i in range(6)]):
        pages, dropped = plan_inside([Block(f"p{i}", 3.5) for i in range(4)], reserves, 8.0, 3, 4, 0.12)
        assert pages and dropped == []


def test_plan_inside_splits_a_lone_odd_page_over_two_pages_when_there_are_no_reserves():
    primary = [Block(f"p{i}", 3.5) for i in range(4)]                    # one page, odd, nothing to pair it with
    pages, dropped = plan_inside(primary, [], 8.0, 3, 4, 0.12)
    assert dropped == [] and len(pages) == 2
    assert all(any(c.keys for c in page) for page in pages)
    assert [k for c in pages[1] for k in c.keys] == ["p3"]                # the lowest-ranked story moved over
    assert [k for c in pages[0] for k in c.keys] == ["p0", "p1", "p2"]


def test_plan_inside_a_lone_story_keeps_a_blank_partner_rather_than_vanishing():
    pages, dropped = plan_inside([Block("only", 3.0)], [], 8.0, 3, 4, 0.12)
    assert dropped == [] and len(pages) == 2 and pages[0][0].keys == ["only"]


def test_plan_inside_picks_the_plan_that_places_the_most_primary_blocks():
    # page three holds two stories; dropping it loses them, a partner page of reserves keeps them
    primary = [Block(f"p{i}", 9) for i in range(8)]
    reserves = [Block(f"r{i}", 6) for i in range(3)]
    pages, dropped = plan_inside(primary, reserves, 10, 3, 4, 0.2)
    assert dropped == [] and len(pages) == 4
    assert {k for page in pages for c in page for k in c.keys if k.startswith("p")} == {f"p{i}" for i in range(8)}


def test_plan_inside_prefers_fewer_pages_when_the_content_placed_is_equal():
    primary = [Block("a", 9), Block("b", 9), Block("c", 9)]               # one page, full
    reserves = []
    pages, _ = plan_inside(primary, reserves, 10, 3, 4, 0.2)
    assert len(pages) == 2                                                  # a blank-free split, not a fourth page
    assert all(any(c.keys for c in page) for page in pages)


def test_plan_inside_no_plan_ever_contains_a_blank_inside_page_unless_it_is_the_band_partner():
    import random
    rng = random.Random(3)
    for _ in range(200):
        primary = [Block(f"p{i}", rng.choice([2, 3.5, 5, 7, 7.5])) for i in range(rng.randint(0, 9))]
        reserves = [Block(f"r{i}", rng.choice([1.5, 3, 5])) for i in range(rng.randint(0, 5))]
        offset = rng.choice([0.0, 0.0, 2.1])
        pages, dropped = plan_inside(primary, reserves, 8.0, 3, rng.choice([2, 4, 6]), 0.12, first_page_offset=offset)
        assert len(pages) % 2 == 0 or offset
        lone = len({k for page in pages for c in page for k in c.keys if k.startswith("p")}) == 1
        for i, page in enumerate(pages):
            if not any(c.keys for c in page):          # only a band page's partner, or a lone story's, may be blank
                band_itself = offset and i == 0              # the band prints there even with no story beside it
                assert band_itself or ((offset or lone) and i == 1 and len(pages) == 2), (len(primary), len(reserves), len(pages))
        if primary and len(pages) == 0:
            assert False, "primary content vanished"


def test_plan_inside_respects_max_pages():
    primary = [Block(f"p{i}", 9) for i in range(20)]
    pages, dropped = plan_inside(primary, [], 10, 3, 2, 0.2)
    assert len(pages) == 2
    assert dropped == [f"p{i}" for i in range(6, 20)]


def test_plan_inside_zero_pages_allowed():
    pages, dropped = plan_inside([], [], 10, 3, 4, 0.2)
    assert pages == [] and dropped == []


def test_plan_inside_first_page_offset_starts_page_one_columns_lower():
    primary = [Block(f"p{i}", 4) for i in range(4)]
    pages, dropped = plan_inside(primary, [], 10, 3, 4, 0.2, first_page_offset=3.0)
    assert dropped == [] and len(pages) == 2
    first = pages[0]
    assert [c.keys for c in first] == [["p0"], ["p1"], ["p2"]]
    assert [c.tops for c in first] == [[3.0], [3.0], [3.0]]
    assert all(abs(c.used - 7.0) < 1e-9 for c in first)
    assert pages[1][0].keys == ["p3"] and pages[1][0].tops == [0.0]      # later pages are untouched


def test_plan_inside_first_page_offset_respects_the_column_height_and_fold():
    primary = [Block("a", 6), Block("b", 3, head=1)]
    pages, dropped = plan_inside(primary, [], 10, 3, 4, 0.2, fold_at=5.0, first_page_offset=4.7)
    assert dropped == []
    assert pages[1][0].keys == ["a"]                       # 4.7 + 6 does not fit page one: a opens page two
    assert pages[0][0].keys == ["b"]
    assert abs(pages[0][0].tops[0] - 5.06) < 1e-9          # b's head would have crossed the fold at 5: pushed, a 0.36 gap


def test_plan_inside_a_head_over_a_big_fold_gap_straddles_the_fold_when_no_also_remains():
    primary = [Block("a", 7), Block("b", 3, head=1)]
    pages, _ = plan_inside(primary, [], 10, 3, 4, 0.2, fold_at=5.0, first_page_offset=4.0)
    assert pages[0][0].keys == ["b"] and pages[0][0].tops == [4.0]      # pushing it would leave a 1.06 hole


def test_plan_inside_first_page_is_mandatory_with_an_offset():
    pages, dropped = plan_inside([], [], 10, 3, 4, 0.2, first_page_offset=3.0)
    assert len(pages) == 2 and dropped == []
    assert all(not c.keys and abs(c.used - 3.0) < 1e-9 for c in pages[0])
    assert all(not c.keys for c in pages[1])


def test_plan_inside_mostly_empty_first_page_is_kept_not_dropped():
    pages, dropped = plan_inside([Block("p0", 2)], [], 10, 3, 4, 0.2, first_page_offset=3.0)
    assert dropped == [] and len(pages) == 2
    assert pages[0][0].keys == ["p0"] and pages[0][0].tops == [3.0]
    # without the offset a lone story is still never dropped to nothing: it keeps a second page
    pages, dropped = plan_inside([Block("p0", 2)], [], 10, 3, 4, 0.2)
    assert len(pages) == 2 and dropped == [] and pages[0][0].keys == ["p0"]


def test_plan_inside_offset_with_one_page_available_leaves_content():
    pages, dropped = plan_inside([Block("p0", 2)], [], 10, 3, 1, 0.2, first_page_offset=3.0)
    assert dropped == [] and len(pages) == 1 and pages[0][0].keys == ["p0"]


def test_plan_inside_offset_page_gets_reserves_on_the_next_page():
    reserves = [Block("r0", 5)]
    pages, dropped = plan_inside([Block("p0", 2)], reserves, 10, 3, 4, 0.2, first_page_offset=3.0)
    assert len(pages) == 2 and dropped == []
    assert pages[0][1].keys == ["r0"] and pages[0][1].tops == [3.0]     # a gap on page one: no new column needed
    assert all(not c.keys for c in pages[1])
    pages, dropped = plan_inside([Block("p0", 2)], [Block("r0", 8)], 10, 3, 4, 0.2, first_page_offset=3.0)
    assert pages[0][1].keys == [] and pages[1][0].keys == ["r0"]       # too tall for page one: the partner page


def test_plan_inside_zero_offset_is_the_old_behavior():
    primary = [Block(f"p{i}", 9) for i in range(7)]
    assert plan_inside(primary, [], 10, 3, 4, 0.2, first_page_offset=0.0) == plan_inside(primary, [], 10, 3, 4, 0.2)


# ---- line-level splits ----

LINE_H = 0.2


def lined(key, n, head=0.5, line=LINE_H, para_gap_after=(), **kw):
    """A block with a head and n body lines; para_gap_after lists line numbers followed by a 0.1 gap."""
    units, y = [], head
    for i in range(n):
        units.append((round(y, 9), round(y + line, 9)))
        y += line + (0.1 if i + 1 in para_gap_after else 0.0)
    return Block(key, round(y, 9), head=head, units=units, **kw)


def shown(col):
    return list(zip(col.keys, col.tops, col.windows))


def test_a_block_that_does_not_fit_runs_on_at_the_top_of_the_next_column_of_the_same_page():
    a = lined("a", 30)                               # 0.5 + 6.0 = 6.5 tall
    b = lined("b", 30)                               # does not fit the 3.3 left in column one
    cols, dropped = pack([a, b], 10, 3, 0.2, per_page=3)
    assert dropped == [] and [c.keys for c in cols] == [["a", "b"], ["b"]]
    assert cols[1].tops == [0.0]                     # the continuation starts at the very top
    assert cols[0].windows[0] == (0.0, None)         # a is whole
    y0, y1 = cols[0].windows[1]
    assert y0 == 0.0 and y1 is not None and 6.7 + y1 <= 10 + 1e-9
    assert 10 - (6.7 + y1) < LINE_H                  # as many lines as the room allows
    start = next(top for top, _ in b.units if top >= y1 - 1e-9)
    assert cols[1].windows == [(start, None)]        # the next column resumes at the very next line
    assert abs(cols[0].used - (6.7 + y1)) < 1e-9 and abs(cols[1].used - (b.height - start)) < 1e-9


def test_the_cut_leaves_at_least_three_lines_on_each_side():
    # column one has room for the head plus 29 of 30 lines; the cut must back off so three remain below it
    a = lined("a", 30)
    filler = Block("f", 10 - (0.5 + 29 * LINE_H))    # leaves exactly head + 29 lines of room
    cols, _ = pack([filler, a], 10, 3, 0.0, per_page=3)
    y0, y1 = cols[0].windows[1]
    assert round((y1 - 0.5) / LINE_H) == 27
    assert cols[1].windows[0][0] == a.units[27][0] and len(a.units) - 27 == 3


def test_no_split_when_the_room_cannot_hold_the_head_and_three_lines():
    a = lined("a", 20)
    filler = Block("f", 10 - (0.5 + 2 * LINE_H))     # head and only two lines of room
    cols, _ = pack([filler, a], 10, 3, 0.0, per_page=3)
    assert [c.keys for c in cols] == [["f"], ["a"]]  # a opens a new column whole
    assert cols[1].windows == [(0.0, None)]


def test_no_split_when_it_would_leave_fewer_than_three_lines_for_the_next_column():
    a = lined("a", 6)                                # exactly 3 + 3: the only legal cut is after line 3
    filler = Block("f", 10 - (0.5 + 5 * LINE_H))     # room for the head and 5 lines: a cut after line 5 is illegal
    cols, _ = pack([filler, a], 10, 3, 0.0, per_page=3)
    assert cols[0].windows[1] == (0.0, a.units[2][1]) and cols[1].windows[0][0] == a.units[3][0]
    short = lined("s", 5)                            # 5 lines cannot give 3 above and 3 below: never split
    cols, _ = pack([Block("f", 10 - (0.5 + 4 * LINE_H)), short], 10, 3, 0.0, per_page=3)
    assert [c.keys for c in cols] == [["f"], ["s"]]


def test_a_split_never_crosses_a_page():
    cols, dropped = pack([lined("a", 40), lined("c", 30), lined("d", 30)], 10, 4, 0.2, per_page=2)
    assert dropped == []
    pages_of = {}
    for i, col in enumerate(cols):
        for k in col.keys:
            pages_of.setdefault(k, set()).add(i // 2)
    assert all(len(v) == 1 for v in pages_of.values()), pages_of
    assert cols[1].keys == ["c"] and cols[1].tops == [0.0] and cols[2].keys == ["d"]   # d needs page two


def test_the_last_column_of_a_page_never_hands_a_story_on_to_the_next_page():
    a = lined("a", 30)
    cols, dropped = pack([lined("x", 45), lined("y", 40), a], 10, 6, 0.2, per_page=2)
    # x fills column 0, y fills column 1 (the last of page one, 1.3 left); a would need page two's column
    assert cols[0].keys == ["x"] and cols[1].keys == ["y"] and cols[2].keys == ["a"]
    assert cols[2].windows == [(0.0, None)] and cols[2].tops == [0.0]


def test_a_continuation_can_run_on_into_a_third_column():
    big = lined("big", 80)                           # 0.5 + 16 = 16.5: more than one column
    cols, dropped = pack([lined("a", 30), big], 10, 3, 0.2, per_page=3)
    assert dropped == []
    assert [c.keys for c in cols] == [["a", "big"], ["big"], ["big"]]
    assert all(c.tops[-1] == 0.0 for c in cols[1:])
    assert all(c.used <= 10 + 1e-9 for c in cols)
    # the windows tile the block: each begins at the very next line after the previous one ends
    w = [c.windows[-1] for c in cols]
    for (_, end), (start, _) in zip(w, w[1:]):
        assert abs(start - next(top for top, _ in big.units if top >= end - 1e-9)) < 1e-9
    assert w[-1][1] is None


def test_a_block_taller_than_a_column_splits_across_its_page_but_not_beyond():
    cols, dropped = pack([lined("big", 80)], 10, 3, 0.2, per_page=3)
    assert dropped == [] and [c.keys for c in cols] == [["big"], ["big"]]
    cols, dropped = pack([lined("big", 80)], 10, 3, 0.2, per_page=1)
    assert dropped == ["big"]                        # one column a page: it cannot continue anywhere


def test_a_head_is_pushed_below_the_fold_when_the_gap_is_half_an_inch_or_less():
    a = Block("a", 2.0)                              # b would start at 2.2 with its head across a fold at 2.5
    b = lined("b", 40, head=1.0)
    cols, _ = pack([a, b], 10, 3, 0.2, per_page=3, fold_at=2.5)
    assert abs(cols[0].tops[1] - 2.56) < 1e-9        # pushed just below the fold, a 0.36 gap
    y0, y1 = cols[0].windows[1]
    assert y0 == 0.0 and y1 is not None and cols[0].used <= 10 + 1e-9 and cols[1].tops == [0.0]
    assert cols[0].holes == []


def test_a_head_over_a_bigger_gap_straddles_the_fold_and_is_recorded_as_a_hole():
    a = lined("a", 6)                                # 0.5 + 1.2 = 1.7
    b = lined("b", 40, head=1.0)                     # would start at 1.9; pushing it would leave a 0.66 hole
    cols, _ = pack([a, b], 10, 3, 0.2, per_page=3, fold_at=2.5)
    assert abs(cols[0].tops[1] - 1.9) < 1e-9
    assert len(cols[0].holes) == 1 and cols[0].holes[0][0] == 1
    assert abs(cols[0].holes[0][1] - 1.9) < 1e-9 and abs(cols[0].holes[0][2] - 2.56) < 1e-9


def test_also_items_fill_a_fold_hole_and_the_head_goes_below_the_fold():
    primary = [Block("p0", 2.7), Block("q", 4.0, head=1.8)]          # q would start at 3.4; pushed to 5.06
    pages, dropped = plan_inside(primary, [], 10, 3, 4, 0.2, fold_at=5.0, first_page_offset=0.5, also=item_block(4))
    col = pages[0][0]
    assert col.keys == ["p0", "also", "q"] and dropped == []
    assert abs(col.tops[1] - 3.4) < 1e-9 and abs(col.tops[2] - 5.06) < 1e-9      # items above the fold, head just below
    assert col.windows[1] == (0.0, 1.3)                                          # kicker and two whole items
    # the other two items carry on elsewhere, starting at the top of the third item
    rest = [(c.keys, c.windows) for page in pages for c in page if "also" in c.keys and c is not col]
    assert rest and rest[0][1][rest[0][0].index("also")][0] == item_block(4).units[2][0]


def test_a_fold_hole_stays_a_straddle_when_the_items_would_not_fit_it_or_the_column_cannot_take_the_shift():
    primary = [Block("p0", 3.5), Block("q", 4.0, head=1.0)]
    pages, _ = plan_inside(primary, [], 10, 3, 4, 0.2, fold_at=5.0, first_page_offset=0.5, also=item_block(4))
    assert pages[0][0].keys == ["p0", "q"]                  # a 0.66 room cannot hold the kicker and one item
    primary = [Block("p0", 2.8), Block("q", 5.8, head=1.8)]      # moving q down 1.46 would push it past the foot
    pages, _ = plan_inside(primary, [], 10, 3, 4, 0.2, fold_at=5.0, first_page_offset=0.5, also=item_block(4))
    assert pages[0][0].keys == ["p0", "q"]


def test_an_also_block_that_fits_a_hole_whole_leaves_nothing_to_place_elsewhere():
    primary = [Block("p0", 2.2), Block("q", 4.0, head=2.5)]          # q would start at 2.9, a 2.16 hole
    pages, _ = plan_inside(primary, [], 10, 3, 4, 0.2, fold_at=5.0, first_page_offset=0.5, also=item_block(3))
    assert pages[0][0].keys == ["p0", "also", "q"] and pages[0][0].windows[1] == (0.0, None)
    assert sum("also" in c.keys for page in pages for c in page) == 1


def test_a_continuation_in_an_offset_column_starts_at_the_offset():
    cols = [Column(used=3.0) for _ in range(3)]
    pack([lined("a", 40), lined("b", 30)], 10, 3, 0.2, columns=cols, per_page=3)
    assert cols[0].keys[0] == "a" and cols[0].tops[0] == 3.0
    assert any(c.keys == ["b"] and c.tops == [3.0] for c in cols[1:])


def test_blocks_without_units_never_split():
    cols, dropped = pack([Block("a", 6), Block("b", 6)], 10, 3, 0.2, per_page=3)
    assert [c.keys for c in cols] == [["a"], ["b"]]


def test_whole_blocks_fill_gaps_before_any_split():
    cols, _ = pack([lined("a", 30), lined("b", 30), lined("tiny", 3)], 10, 3, 0.2, per_page=3)
    holder = next(c for c in cols if "tiny" in c.keys)
    assert holder.windows[holder.keys.index("tiny")] == (0.0, None)


def item_block(n, item=0.5, head=0.25, **kw):
    units = [(round(head + i * (item + 0.05), 9), round(head + i * (item + 0.05) + item, 9)) for i in range(n)]
    return Block("also", units[-1][1], head=head, units=units, min_after=2, **kw)


def full_page_then(tail_height):
    """Three full columns on page one and one column of tail_height on page two."""
    return [Block(f"p{i}", 9.5) for i in range(3)] + [Block("p3", tail_height)]


def test_also_goes_into_a_gap_split_by_item_not_into_its_own_column():
    also = item_block(6)                             # 0.25 + 6 * 0.55 - 0.05 = 3.5 tall
    pages, dropped = plan_inside(full_page_then(7.0), [], 10, 3, 4, 0.2, also=also)
    assert dropped == []
    c0, c1 = pages[1][0], pages[1][1]
    assert c0.keys == ["p3", "also"] and c1.keys == ["also"] and c1.tops == [0.0]
    end = c0.windows[1][1]
    assert end is not None and 7.2 + end <= 10 + 1e-9    # the first part is whole items, inside the gap
    assert c1.windows[0][0] > end                        # the second part starts at a later item
    assert not any(c.keys == ["also"] for c in pages[0])


def test_also_that_fits_a_gap_whole_stays_whole():
    pages, _ = plan_inside(full_page_then(5.0), [], 10, 3, 4, 0.2, also=item_block(4))
    assert pages[1][0].keys == ["p3", "also"] and pages[1][0].windows[1] == (0.0, None)
    assert not pages[1][1].keys


def test_also_with_no_gap_of_three_items_takes_its_own_column():
    pages, _ = plan_inside(full_page_then(8.7), [], 10, 3, 4, 0.2, also=item_block(6))
    # 1.1 left under p3: the kicker and three items (1.85) do not fit, so no gap will do
    assert pages[1][0].keys == ["p3"] and pages[1][1].keys == ["also"]
    assert pages[1][1].windows == [(0.0, None)] and pages[1][1].tops == [0.0]


def test_a_tall_block_that_cannot_continue_on_this_page_starts_at_the_top_of_a_later_one():
    cols, dropped = pack([lined("a", 45), lined("big", 80), Block("tiny", 2)], 10, 6, 0.2, per_page=2)
    assert dropped == []
    assert cols[0].keys == ["a"] and cols[1].keys == ["tiny"]    # page one's last column was left for later gaps
    assert cols[2].keys == ["big"] and cols[3].keys == ["big"] and cols[2].tops == [0.0] and cols[3].tops == [0.0]


def filled(key, used):
    return Column(keys=[key], tops=[0.0], used=used, windows=[(0.0, None)])


def test_prefer_gaps_splits_into_the_foot_of_a_filled_column_instead_of_taking_an_empty_one():
    def pages():
        return [filled("p0", 9.5), filled("p1", 7.8), Column(), filled("p2", 9.5)]   # page one has an empty third column

    cols, _ = pack([item_block(6)], 10, 6, 0.2, columns=pages(), per_page=3, prefer_gaps=True)
    assert cols[1].keys == ["p1", "also"] and cols[2].keys == ["also"] and cols[2].tops == [0.0]
    # without the preference the empty column that sits before the frontier takes it whole
    cols, _ = pack([item_block(6)], 10, 6, 0.2, columns=pages(), per_page=3)
    assert cols[1].keys == ["p1"] and cols[2].keys == ["also"] and cols[2].windows == [(0.0, None)]


def test_prefer_gaps_still_puts_a_block_that_fits_a_gap_whole_into_it():
    cols = [filled("p0", 9.5), filled("p1", 7.0), Column()]
    pack([item_block(4)], 10, 3, 0.2, columns=cols, per_page=3, prefer_gaps=True)
    assert cols[1].keys == ["p1", "also"] and cols[1].windows[1] == (0.0, None) and not cols[2].keys


def test_prefer_gaps_falls_back_to_an_empty_column_when_no_gap_holds_three_items():
    cols = [filled("p0", 9.5), filled("p1", 8.8), Column()]
    pack([item_block(6)], 10, 3, 0.2, columns=cols, per_page=3, prefer_gaps=True)
    assert cols[2].keys == ["also"] and cols[2].windows == [(0.0, None)]


# ---- filling a page's space: look ahead, then trim ----

def storied(key, para_lines, head=0.5, line=LINE_H, tail=0.0):
    """A block with a head, paragraphs of the given line counts, and an optional tail (a source line)."""
    units, ends, y = [], [], head
    for n in para_lines:
        for _ in range(n):
            units.append((round(y, 9), round(y + line, 9)))
            y += line
        ends.append(len(units) - 1)
    return Block(key, round(y + tail, 9), head=head, units=units, para_ends=ends)


def test_trims_are_the_shorter_versions_that_stop_at_a_paragraph_end():
    b = storied("a", [5, 5, 5, 5, 5, 5], tail=0.15)          # head 0.5, 30 lines, 6 paragraphs
    kept = [t.kept for t in b.trims()]
    assert kept == [5, 4, 3]                                  # at least half the body, at least two paragraphs
    t = b.trims()[0]
    assert t.key == "a" and abs(t.height - (0.5 + 25 * LINE_H + 0.15)) < 1e-9     # still ends with the tail
    assert len(t.units) == 25 and t.para_ends == [4, 9, 14, 19, 24]
    assert storied("s", [30, 30]).trims() == []              # two paragraphs: nothing to cut
    assert storied("h", [5, 5, 5, 45]).trims() == []         # every cut loses over half the body
    assert Block("plain", 3.0).trims() == []


def test_a_later_block_fills_the_empty_column_before_a_tall_one_opens_a_new_page():
    cols, dropped = pack([lined("a", 45), storied("big", [10] * 7), lined("c", 20)], 10, 6, 0.2, per_page=2)
    assert dropped == []
    assert cols[0].keys == ["a"] and cols[1].keys == ["c"]            # c, ranked below big, took page one's empty column
    assert cols[2].keys == ["big"] and cols[3].keys == ["big"]


def test_a_later_block_fills_a_gap_of_three_inches_before_the_next_page_opens():
    # column one has 4.3 free; x needs a new column on page two; the small c fits the gap and goes there first
    cols, _ = pack([Block("a", 5.5), Block("b", 9.5), Block("x", 9.5), Block("c", 3.0)], 10, 6, 0.2, per_page=2)
    assert cols[0].keys == ["a", "c"] and cols[1].keys == ["b"] and cols[2].keys == ["x"]


def test_reserves_given_as_fillers_are_used_and_removed():
    fillers = [Block("r0", 6.0), Block("r1", 3.0)]
    cols, _ = pack([Block("a", 5.5), Block("b", 9.5), Block("x", 9.5)], 10, 6, 0.2, per_page=2, fillers=fillers)
    assert "r1" in cols[0].keys and [f.key for f in fillers] == ["r0"]


def test_when_nothing_fits_the_block_is_trimmed_at_a_paragraph_end_to_fill_the_empty_column():
    cols, dropped = pack([lined("a", 45), storied("big", [10] * 7)], 10, 6, 0.2, per_page=2)
    assert dropped == []
    assert cols[1].keys == ["big"] and cols[1].kept == [4] and cols[1].windows == [(0.0, None)]
    assert abs(cols[1].used - (0.5 + 40 * LINE_H)) < 1e-9           # 8.5: head plus four paragraphs
    assert len(cols) == 2                                           # and it did not also open the next page


def test_a_trimmed_block_fits_the_gap_in_a_filled_column():
    # a leaves 3.8 under it; t is 6.65 whole and cannot go, but three paragraphs (3.65) fit
    cols, _ = pack([Block("a", 6.0), Block("b", 9.5), storied("t", [5] * 6, tail=0.15)], 10, 6, 0.2, per_page=2)
    assert cols[0].keys == ["a", "t"] and cols[0].kept == [None, 3]
    assert cols[0].used <= 10 + 1e-9 and len(cols) == 2


def test_trimming_never_cuts_below_two_paragraphs_or_half_the_body():
    cols, _ = pack([lined("a", 45), storied("two", [40, 40])], 10, 6, 0.2, per_page=2)
    assert cols[1].keys == [] and cols[2].keys == ["two"] and cols[2].kept == [None]
    cols, _ = pack([lined("a", 45), storied("front", [5, 5, 5, 60])], 10, 6, 0.2, per_page=2)
    assert cols[1].keys == []                                       # every cut would lose over half the body


def test_no_look_ahead_or_trim_while_the_page_has_no_space():
    cols, _ = pack([lined("a", 45), lined("b", 45), storied("t", [10] * 7), lined("c", 10)], 10, 6, 0.2, per_page=2)
    assert cols[2].keys[0] == "t" and cols[2].kept[0] is None        # page one is full: t simply opens page two


def test_plan_inside_lets_reserves_fill_an_empty_column_before_a_tall_story_opens_the_next_page():
    primary = [lined("p0", 45), storied("big", [10] * 7)]
    reserves = [lined("r0", 20), lined("r1", 20)]
    pages, dropped = plan_inside(primary, reserves, 10, 2, 4, 0.2)
    assert dropped == []
    assert pages[0][1].keys == ["r0", "r1"]              # both fit the empty column beside p0
    assert pages[1][0].keys == ["big"] and pages[1][1].keys == ["big"]


# ---- stories from a dropped odd page are poured back into the kept pages' gaps ----

def six_with_gaps():
    return [Block(f"p{i}", 6) for i in range(6)]          # two pages, 3.8 free under every column


def test_dropped_page_stories_that_fit_a_gap_are_poured_back_in_rank_order():
    primary = six_with_gaps() + [Block("big", 9), Block("small", 3.5), Block("small2", 3.5)]
    pages, dropped = plan_inside(primary, [], 10, 3, 4, 0.2)
    assert len(pages) == 2 and dropped == ["big"]
    assert pages[0][0].keys == ["p0", "small"] and pages[0][1].keys == ["p1", "small2"]


def test_look_ahead_lets_a_later_dropped_story_use_the_gap_an_earlier_one_cannot():
    primary = six_with_gaps() + [Block("big", 9), Block("small", 3.5)]
    pages, dropped = plan_inside(primary, [], 10, 3, 4, 0.2)
    assert dropped == ["big"] and "small" in pages[0][0].keys


def test_a_dropped_story_is_trimmed_at_a_paragraph_end_to_fit_a_gap():
    # page one has 4.8 free under each column, page two is full, and t opens page three
    primary = [Block(f"p{i}", 5) for i in range(3)] + [Block(f"q{i}", 9.5) for i in range(3)] + [storied("t", [5] * 8)]
    pages, dropped = plan_inside(primary, [], 10, 3, 4, 0.2)
    assert len(pages) == 2 and dropped == []
    col = pages[0][0]
    assert col.keys == ["p0", "t"] and col.kept == [None, 4] and col.used <= 10 + 1e-9


def test_reserves_from_the_dropped_page_and_leftover_reserves_fill_gaps_after_the_stories():
    primary = six_with_gaps() + [Block("big", 9)]
    reserves = [Block("r0", 3.5), Block("r1", 3.5), Block("r2", 3.5), Block("r3", 3.5), Block("r4", 3.5),
                Block("r5", 3.5), Block("r6", 3.5)]
    pages, dropped = plan_inside(primary, reserves, 10, 3, 3, 0.2)      # no room for a fourth page
    assert dropped == ["big"] and len(pages) == 2
    placed = [k for page in pages for c in page for k in c.keys]
    assert placed.count("r0") == 1 and all(f"r{i}" in placed for i in range(6))


def test_the_also_block_takes_only_what_the_poured_back_stories_leave():
    primary = six_with_gaps() + [Block("big", 9), Block("small", 3.5)]
    pages, dropped = plan_inside(primary, [], 10, 3, 4, 0.2, also=item_block(4))
    keys = [k for page in pages for c in page for k in c.keys]
    assert "small" in pages[0][0].keys and "also" in keys
    assert not any("also" in c.keys for c in pages[0][:1])      # not ahead of the story in column one's gap


def test_a_story_that_opened_the_dropped_page_goes_back_into_an_earlier_pages_gap():
    primary = [Block(f"p{i}", 6) for i in range(3)] + [Block(f"q{i}", 9.5) for i in range(3)] + [Block("s", 3.5)]
    pages, dropped = plan_inside(primary, [], 10, 3, 4, 0.2)
    assert len(pages) == 2 and dropped == [] and pages[0][0].keys == ["p0", "s"]


def test_the_rest_of_the_also_items_never_land_ahead_of_the_ones_in_the_hole():
    from morning_edition.layout.pack import _fill_hole
    cols = [filled("a", 4.0), Column(keys=["q"], tops=[3.4], used=7.4, windows=[(0.0, None)], holes=[(0, 3.4, 5.06)])]
    rest, start = _fill_hole(item_block(4), cols, 10, 0.2)
    assert start == 1 and rest is not None and rest.first == 2
    pack([rest], 10, 2, 0.2, columns=cols, per_page=2, prefer_gaps=True, min_index=start)
    assert cols[0].keys == ["a"]                      # column one's foot would have fitted it; it is behind the hole
