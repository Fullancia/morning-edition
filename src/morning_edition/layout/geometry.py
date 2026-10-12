"""Page geometry in inches. One source of truth for templates and fitting code."""

PAGE_W, PAGE_H = 8.5, 11.0
MARGIN_X, MARGIN_Y = 0.45, 0.4
CONTENT_W = PAGE_W - 2 * MARGIN_X          # 7.6
FOLD = 5.5
GUTTER = 0.16                              # between text columns
REGION_GAP = 0.18                          # between page regions
GAP = 0.12                                 # between stacked pieces
LINE = 12 / 72                             # one line of body text
KICKER_H = 0.25                            # small label above a column

# Front page
MASTHEAD_H = 1.25
EAR_W = 1.45                               # the weather and puzzle boxes beside the nameplate (edition.css .ear)
EAR_PAD_X = 6 / 72                         # .ear horizontal padding, each side
EAR_BORDER = 0.75 / 72                     # .ear border, each side
NAMEPLATE_GAP = 0.2                        # breathing room kept between the nameplate and each ear
FRONT_TOP_Y = MARGIN_Y + MASTHEAD_H + 0.08
FRONT_TOP_H = FOLD - 0.12 - FRONT_TOP_Y
SIDE_W = 1.75
SIDE_X = MARGIN_X + CONTENT_W - SIDE_W
LEAD_W = CONTENT_W - SIDE_W - REGION_GAP
LEAD_HEADER_MAX = 1.5
PHOTO_W = 2.35
PHOTO_MIN_H, PHOTO_MAX_H = 1.0, 1.6
CAPTION_H = 0.3
LEAD_TEXT_W_PHOTO = (LEAD_W - PHOTO_W - 2 * GUTTER) / 2
LEAD_TEXT_W_NOPHOTO = (LEAD_W - 2 * GUTTER) / 3
FRONT_BOTTOM_Y = FOLD + 0.12
FRONT_BOTTOM_H = PAGE_H - MARGIN_Y - FRONT_BOTTOM_Y
CONT_W = 1.75
CONT_TEXT_W = CONT_W - GUTTER
BRIEFS_W = 1.75
BRIEFS_X = MARGIN_X + CONTENT_W - BRIEFS_W
BRIEFS_TEXT_W = BRIEFS_W - 0.14
STORY2_X = MARGIN_X + CONT_W + REGION_GAP
STORY2_W = CONTENT_W - CONT_W - BRIEFS_W - 2 * REGION_GAP
STORY2_COL_W = (STORY2_W - GUTTER) / 2

# Inside pages
HEADER_H = 0.35
INSIDE_Y = MARGIN_Y + HEADER_H
INSIDE_H = PAGE_H - MARGIN_Y - INSIDE_Y
WEATHER_BAND_H = 2.1                       # expanded weather band atop page 2, ends well above the fold
INSIDE_COLS = 3
INSIDE_COL_W = (CONTENT_W - (INSIDE_COLS - 1) * GUTTER) / INSIDE_COLS

# Back page
BACK_TOP_H = FOLD - 0.12 - INSIDE_Y
GOOD_W = CONTENT_W - SIDE_W - REGION_GAP
GOOD_COL_W = (GOOD_W - 2 * GUTTER) / 3
PUZZLE_Y = FOLD + 0.12
PUZZLE_H = PAGE_H - MARGIN_Y - PUZZLE_Y
XWORD_CELL = 0.28
SUDOKU_CELL = 0.27
ANSWER_CELL = 0.11
INSIDE_FOLD_AT = FOLD - INSIDE_Y           # fold offset within an inside column


def inside_col_x(i: int) -> float:
    return MARGIN_X + i * (INSIDE_COL_W + GUTTER)
