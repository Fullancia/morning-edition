from morning_edition.layout import measure

HTML = """<html lang="en"><head><style>@page { size: 3in 50in; margin: 0 }
body { margin: 0 } p { font: 9pt/12pt serif; margin: 0 }</style></head><body>
<div data-measure="one" style="width: 2in"><p>Line.</p></div>
<div data-measure="tall" style="width: 2in; height: 1.5in"></div>
<h1 class="headline" style="margin: 0; height: 0.5in">Head</h1>
</body></html>"""


def test_heights_in_inches():
    h = measure.heights(HTML)
    assert abs(h["one"] - 12 / 72) < 0.01
    assert abs(h["tall"] - 1.5) < 0.01


def test_extents_report_positions():
    doc = measure.render_document(HTML)
    (e,) = measure.extents(doc, {"headline"})
    assert e.page == 0 and e.cls == "headline"
    assert abs(e.top - (12 / 72 + 1.5)) < 0.01
    assert abs(e.bottom - e.top - 0.5) < 0.01
    assert e.text.startswith("Head")


def test_extents_see_inside_absolute_boxes():
    html = """<html><head><style>@page { size: 3in 5in; margin: 0 } body { margin: 0 }
    .box { position: absolute; left: 0; top: 1in; width: 2in; overflow: hidden }
    h1 { margin: 0; height: 0.5in }</style></head>
    <body><div class="box"><h1 class="headline">Inside</h1></div></body></html>"""
    (e,) = measure.extents(measure.render_document(html), {"headline"})
    assert abs(e.top - 1.0) < 0.01 and e.text == "Inside"


def test_widths_in_inches():
    html = """<html><head><style>@page { size: 5in 5in; margin: 0 } body { margin: 0 }</style></head><body>
    <span data-measure-width="w" style="display: inline-block; width: 1.5in">x</span></body></html>"""
    assert abs(measure.widths(html)["w"] - 1.5) < 0.01


LINES_HTML = """<html lang="en"><head><style>@page { size: 3in 50in; margin: 0 }
* { margin: 0 } body { font: 9pt/12pt serif } p { margin-bottom: 3pt } li { margin-bottom: 3pt; list-style: none }
h2 { font-size: 20pt; line-height: 1.2 }</style></head><body>
<div data-measure="story" style="width: 1in"><h2>Head words here</h2>
<p>one two three four five six seven eight nine ten eleven twelve</p><p>thirteen fourteen</p></div>
<div data-measure="items" style="width: 1in"><div class="kicker">Also</div>
<ul><li>aaa bbb ccc ddd eee fff</li><li>x</li><li>y</li></ul></div>
<div data-measure="none" style="width: 1in; height: 0.5in"></div>
</body></html>"""


def test_lines_give_each_body_line_box_as_offsets_from_the_block_top():
    blocks = measure.measure_blocks(LINES_HTML)
    hs = {k: m.height for k, m in blocks.items()}
    story = blocks["story"].lines
    assert len(story) >= 5
    assert abs(story[0][0] - 3 * 24 / 72) < 0.01           # three 24pt head lines come first; the head is not a unit
    assert all(abs((b - t) - 12 / 72) < 0.005 for t, b in story)
    assert all(abs(story[i + 1][0] - story[i][1]) < 0.005 for i in range(len(story) - 2))   # lines are contiguous in a paragraph
    assert story[-1][0] - story[-2][1] > 2 / 72            # the paragraph margin shows up between paragraphs
    assert abs(hs["story"] - story[-1][1]) < 0.01     # the last margin collapses out of the block


def test_lines_use_list_items_as_the_units_of_a_list():
    blocks = measure.measure_blocks(LINES_HTML)
    items = blocks["items"].lines
    assert len(items) == 3
    assert items[1][0] > items[0][1]                       # item margin sits between items
    assert items[0][1] - items[0][0] > 12 / 72             # the first item wraps to more than one line
    assert blocks["none"].lines == []


def test_measure_blocks_report_the_last_line_of_each_paragraph():
    m = measure.measure_blocks(LINES_HTML)["story"]
    assert len(m.para_ends) == 2 and m.para_ends[-1] == len(m.lines) - 1
    assert m.para_ends[0] + 1 < len(m.lines) and m.lines[m.para_ends[0] + 1][0] - m.lines[m.para_ends[0]][1] > 2 / 72
    assert measure.measure_blocks(LINES_HTML)["items"].para_ends == []
