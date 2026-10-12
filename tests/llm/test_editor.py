from datetime import datetime, timezone

from morning_edition.llm import editor
from morning_edition.llm.client import LLMError
from morning_edition.models import FeedItem


def item(i, section="Local", hours_ago=1, headlines_only=False, title=None):
    return FeedItem(
        id=i, feed_id=1, section=section, title=title or f"Story {i}", url=f"https://x.com/{i}",
        key=f"https://x.com/{i}", summary=f"Teaser for story {i}.",
        published=datetime(2026, 10, 12, 12 - hours_ago, tzinfo=timezone.utc),
        source="Example", headlines_only=headlines_only,
    )


ITEMS = [item(1), item(2, "World"), item(3, "Tech"), item(4, "Good News"),
         item(5, "Local", hours_ago=5), item(6, "Local", headlines_only=True)]


class FakeLLM:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def ask_json(self, system, user, schema, max_tokens=16000):
        self.calls.append((system, user, schema))
        if self.error:
            raise self.error
        return schema.model_validate(self.result)


def test_sanitize_drops_unknown_duplicate_and_misplaced_ids():
    sel = editor.Selection(
        lead_id=1, good_news_id=4,
        primary=[editor.Pick(id=1, length="story"), editor.Pick(id=2, length="story"),
                 editor.Pick(id=99, length="brief"), editor.Pick(id=2, length="brief"),
                 editor.Pick(id=4, length="story"), editor.Pick(id=6, length="brief")],
        reserves=[editor.Pick(id=2, length="story"), editor.Pick(id=5, length="brief")],
        also=[6, 3, 42],
    )
    clean = editor.sanitize(sel, ITEMS)
    assert clean.lead_id == 1 and clean.good_news_id == 4
    assert [p.id for p in clean.primary] == [2]
    assert [p.id for p in clean.reserves] == [5]
    assert clean.also == [6]


def test_sanitize_rejects_headlines_only_or_good_news_lead():
    sel = editor.Selection(lead_id=6, good_news_id=None, primary=[editor.Pick(id=2, length="story")],
                           reserves=[], also=[])
    clean = editor.sanitize(sel, ITEMS)
    assert clean.lead_id == 2  # promoted from primary
    assert [p.id for p in clean.primary] == []


def test_select_falls_back_on_llm_error():
    sel = editor.select(FakeLLM(error=LLMError("refused")), ITEMS, ["Local", "World", "Tech"], "", 2, 4)
    assert sel.lead_id == 1                        # newest non-headlines Local item
    assert sel.good_news_id == 4
    assert [p.id for p in sel.primary] == [2, 3, 5]  # round-robin by section, newest first
    assert sel.also == [6]


def test_select_without_llm_uses_fallback():
    assert editor.select(None, ITEMS, ["Local", "World", "Tech"], "", 2, 4).lead_id == 1


def test_select_uses_llm_result_and_prompt_mentions_interests():
    llm = FakeLLM({"lead_id": 3, "good_news_id": 4, "primary": [{"id": 1, "length": "story"}],
                   "reserves": [{"id": 2, "length": "brief"}], "also": [6]})
    sel = editor.select(llm, ITEMS, ["Local", "World", "Tech"], "sailing, local politics", 2, 4)
    assert sel.lead_id == 3 and [p.id for p in sel.primary] == [1]
    assert "sailing, local politics" in llm.calls[0][1]


def test_select_sanitizes_invalid_ids_and_falls_back():
    llm = FakeLLM({"lead_id": 99, "good_news_id": None, "primary": [{"id": 98, "length": "story"}],
                   "reserves": [], "also": []})
    sel = editor.select(llm, ITEMS, ["Local", "World", "Tech"], "", 2, 4)
    assert sel.lead_id == 1  # fallback because sanitize left no lead


def test_condense_success_strips_em_dashes():
    llm = FakeLLM({"headline": "Council Backs Seawall\u2014Finally", "deck": "A close vote.",
                   "paragraphs": ["The council voted\u2014five to four.", "Work starts in spring."]})
    piece = editor.condense(llm, ITEMS[0], "Full article text " * 50, "story")
    assert piece.headline == "Council Backs Seawall, Finally"
    assert piece.paragraphs[0] == "The council voted, five to four."
    assert piece.kind == "story" and piece.source == "Example" and piece.key == "https://x.com/1"


def test_pieces_carry_the_original_url_not_the_normalized_key():
    raw = FeedItem(id=9, feed_id=1, section="Local", title="Seawall", url="http://www.x.com/a/?utm_source=rss",
                   key="https://x.com/a", summary="Teaser.", published=None, source="Example",
                   headlines_only=False)
    llm = FakeLLM({"headline": "H", "deck": "D", "paragraphs": ["One."]})
    full = editor.condense(llm, raw, "Full article text " * 50, "story")
    teaser = editor.condense(None, raw, None, "story")
    for piece in (full, teaser):
        assert piece.url == "http://www.x.com/a/?utm_source=rss" and piece.key == "https://x.com/a"


def test_old_pieces_without_a_url_still_load():
    from morning_edition.models import Piece
    old = {"item_id": 1, "section": "Local", "kind": "story", "headline": "H", "deck": "",
           "paragraphs": ["p"], "source": "S", "key": "https://x.com/1"}
    assert Piece.model_validate(old).url == ""


def test_condense_falls_back_to_teaser_brief():
    piece = editor.condense(FakeLLM(error=LLMError("x")), ITEMS[0], "text", "story")
    assert piece.kind == "brief"
    assert piece.headline == "Story 1" and piece.paragraphs == ["Teaser for story 1."]
    assert editor.condense(None, ITEMS[1], None, "lead").kind == "brief"


def test_write_clues_requires_every_entry():
    entries = [(1, "across", "HARBOR"), (1, "down", "HAT")]
    good = FakeLLM({"clues": [{"number": 1, "direction": "across", "clue": "Boat parking"},
                              {"number": 1, "direction": "down", "clue": "Cap"}]})
    assert editor.write_clues(good, entries) == {(1, "across"): "Boat parking", (1, "down"): "Cap"}
    partial = FakeLLM({"clues": [{"number": 1, "direction": "across", "clue": "Boat parking"}]})
    try:
        editor.write_clues(partial, entries)
        raise AssertionError("expected LLMError")
    except LLMError:
        pass


def test_write_clues_returns_empty_dict_for_empty_entries():
    assert editor.write_clues(FakeLLM(), []) == {}


def test_prompt_carries_the_home_area_and_the_stricter_local_rules():
    llm = FakeLLM({"lead_id": 1, "good_news_id": None, "primary": [], "reserves": [], "also": []})
    editor.select(llm, ITEMS, ["Local", "World", "Tech"], "", 2, 4, "New York, New York")
    system, user, _ = llm.calls[0]
    assert "Reader's home: New York, New York" in user
    assert "actually be about the reader's home area" in system
    assert "sponsored content" in system and "best X for Y" in system and "how X can help" in system


def test_pick_section_is_optional_and_nullable_in_the_strict_schema():
    from morning_edition.llm.client import strict_schema
    assert editor.Pick(id=1, length="story").section is None
    props = strict_schema(editor.Selection)["properties"]["primary"]["items"]
    assert "section" in props["required"]
    assert {"type": "null"} in props["properties"]["section"]["anyOf"]


def test_sanitize_keeps_a_configured_pick_section_and_drops_an_invalid_one():
    sel = editor.Selection(
        lead_id=1, good_news_id=None,
        primary=[editor.Pick(id=5, length="story", section="World"),
                 editor.Pick(id=2, length="story", section="Sports"),
                 editor.Pick(id=3, length="brief", section="Good News")],
        reserves=[], also=[])
    clean = editor.sanitize(sel, ITEMS, ["Local", "World", "Tech"])
    assert [(p.id, p.section) for p in clean.primary] == [(5, "World"), (2, None), (3, None)]
    # without a section list nothing can be validated, so overrides are dropped
    assert editor.sanitize(sel, ITEMS).primary[0].section is None


def test_select_applies_pick_sections_from_the_llm():
    llm = FakeLLM({"lead_id": 1, "good_news_id": None,
                   "primary": [{"id": 5, "length": "story", "section": "World"},
                               {"id": 2, "length": "story", "section": "Nowhere"}],
                   "reserves": [], "also": []})
    sel = editor.select(llm, ITEMS, ["Local", "World", "Tech"], "", 2, 4, "New York, New York")
    assert [(p.id, p.section) for p in sel.primary] == [(5, "World"), (2, None)]


def test_fallback_selection_never_sets_a_section():
    sel = editor.fallback_selection(ITEMS, ["Local", "World", "Tech"])
    assert all(p.section is None for p in sel.primary + sel.reserves)
    assert [p.id for p in sel.primary] == [2, 3, 5]


def test_stories_are_asked_for_at_the_fuller_lengths():
    assert editor.WORDS == {"lead": 600, "story": 400, "brief": 200, "good_news": 350}
    assert "about 400 words" in editor.SELECT_SYSTEM and "about 200 words" in editor.SELECT_SYSTEM
    assert "250 words" not in editor.SELECT_SYSTEM and "150 words" not in editor.SELECT_SYSTEM


def test_condense_asks_for_the_length_of_the_kind():
    item = FeedItem(id=1, feed_id=1, section="World", title="T", url="https://example.com/1", key="https://example.com/1",
                    summary="", published=None, source="Example", headlines_only=False)
    asked = []

    class Fake:
        def ask_json(self, system, user, schema, **kw):
            asked.append(user)
            return editor.Condensed(headline="H", deck="D", paragraphs=["One."])

    for kind, words in (("lead", 600), ("story", 400), ("brief", 200), ("good_news", 350)):
        editor.condense(Fake(), item, "Some article text.", kind)
        assert f"of about {words} words" in asked[-1]


def test_select_prompt_asks_for_the_reserves_and_for_a_light_day_to_fill_the_pages():
    llm = FakeLLM({"lead_id": 1, "good_news_id": None, "primary": [], "reserves": [], "also": []})
    editor.select(llm, ITEMS, ["Local", "World", "Tech"], "", 2, 4)
    user = llm.calls[0][1]
    assert "Give 6 reserves" in user and "fewer only if there are not enough good candidates" in user
    assert "up to 6 reserves" not in user
    assert "light news day should still fill the normal day's 2 pages" in user
    assert "about 3 stories, or 2 stories plus 2 or 3 briefs" in user and "about 400 words" in user
    assert "plus 6 briefs" not in user


def test_select_prompt_scales_the_reserves_with_the_pages():
    llm = FakeLLM({"lead_id": 1, "good_news_id": None, "primary": [], "reserves": [], "also": []})
    editor.select(llm, ITEMS, ["Local"], "", 4, 6)
    assert "Give 12 reserves" in llm.calls[0][1] and "normal day's 4 pages" in llm.calls[0][1]
