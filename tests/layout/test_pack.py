from morning_edition.layout.pack import Block, fit_count, pack, plan_inside


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


def test_plan_inside_reserves_open_a_new_page_not_earlier_gaps():
    primary = [Block(f"p{i}", 8) for i in range(8)]   # 3 pages, 2 units free at the foot of every column
    reserves = [Block(f"r{i}", 1.5) for i in range(4)]  # would fit those gaps; must not use them
    pages, dropped = plan_inside(primary, reserves, 10, 3, 4, 0.2)
    assert len(pages) == 4 and dropped == []
    keys = [[k for col in page for k in col.keys] for page in pages]
    assert all(not k.startswith("r") for page in keys[:3] for k in page)
    assert set(keys[3]) == {"r0", "r1", "r2", "r3"}


def test_plan_inside_drops_mostly_empty_odd_page_without_reserves():
    primary = [Block(f"p{i}", 9) for i in range(7)]   # 3 pages, the third one column (30% full)
    pages, dropped = plan_inside(primary, [], 10, 3, 4, 0.2)
    assert len(pages) == 2
    assert dropped == ["p6"]


def test_plan_inside_keeps_over_half_full_odd_page_by_adding_blank_page():
    primary = [Block(f"p{i}", 9) for i in range(8)]   # 3 pages, the third two columns (60% full)
    pages, dropped = plan_inside(primary, [], 10, 3, 4, 0.2)
    assert len(pages) == 4 and dropped == []
    assert all(not col.keys for col in pages[3])


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
    pages, dropped = plan_inside(primary, [], 10, 3, 4, 0.2, fold_at=5.0, first_page_offset=4.5)
    assert dropped == []
    assert pages[1][0].keys == ["a"]                       # 4.5 + 6 does not fit page one: a opens page two
    assert pages[0][0].keys == ["b"]
    assert abs(pages[0][0].tops[0] - 5.06) < 1e-9          # b's head would have crossed the fold at 5


def test_plan_inside_first_page_is_mandatory_with_an_offset():
    pages, dropped = plan_inside([], [], 10, 3, 4, 0.2, first_page_offset=3.0)
    assert len(pages) == 2 and dropped == []
    assert all(not c.keys and abs(c.used - 3.0) < 1e-9 for c in pages[0])
    assert all(not c.keys for c in pages[1])


def test_plan_inside_mostly_empty_first_page_is_kept_not_dropped():
    pages, dropped = plan_inside([Block("p0", 2)], [], 10, 3, 4, 0.2, first_page_offset=3.0)
    assert dropped == [] and len(pages) == 2
    assert pages[0][0].keys == ["p0"] and pages[0][0].tops == [3.0]
    # without the offset the same lone block is dropped with its mostly empty page
    pages, dropped = plan_inside([Block("p0", 2)], [], 10, 3, 4, 0.2)
    assert pages == [] and dropped == ["p0"]


def test_plan_inside_offset_with_one_page_available_leaves_content():
    pages, dropped = plan_inside([Block("p0", 2)], [], 10, 3, 1, 0.2, first_page_offset=3.0)
    assert dropped == [] and len(pages) == 1 and pages[0][0].keys == ["p0"]


def test_plan_inside_offset_page_gets_reserves_on_the_next_page():
    reserves = [Block("r0", 5)]
    pages, dropped = plan_inside([Block("p0", 2)], reserves, 10, 3, 4, 0.2, first_page_offset=3.0)
    assert len(pages) == 2 and dropped == []
    assert pages[1][0].keys == ["r0"]


def test_plan_inside_zero_offset_is_the_old_behavior():
    primary = [Block(f"p{i}", 9) for i in range(7)]
    assert plan_inside(primary, [], 10, 3, 4, 0.2, first_page_offset=0.0) == plan_inside(primary, [], 10, 3, 4, 0.2)
