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
