import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from pydantic import BaseModel

from morning_edition.llm.client import LLM, LLMError, strict_schema


class Inner(BaseModel):
    title: str
    score: int | None


class Outer(BaseModel):
    items: list[Inner]
    note: str


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def fake_client(messages):
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


def text_response(payload, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, stop_details=None,
                           content=[SimpleNamespace(type="thinking", thinking=""),
                                    SimpleNamespace(type="text", text=json.dumps(payload))])


def test_strict_schema_inlines_refs_and_keeps_title_property():
    s = strict_schema(Outer)
    assert "$defs" not in json.dumps(s)
    assert s["additionalProperties"] is False
    assert s["required"] == ["items", "note"]
    inner = s["properties"]["items"]["items"]
    assert inner["additionalProperties"] is False
    assert set(inner["properties"]) == {"title", "score"}
    assert "title" not in s  # schema-level title metadata removed


def test_ask_json_sends_structured_output_and_fallbacks():
    msgs = FakeMessages(text_response({"items": [{"title": "a", "score": None}], "note": "ok"}))
    llm = LLM("claude-opus-5-5", "medium", client=fake_client(msgs))
    out = llm.ask_json("sys", "user", Outer)
    assert out.items[0].title == "a"
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["effort"] == "medium"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"


def test_ask_json_raises_on_refusal_and_bad_json_and_api_error():
    refused = SimpleNamespace(stop_reason="refusal", content=[],
                              stop_details=SimpleNamespace(category="other", explanation="no"))
    with pytest.raises(LLMError):
        LLM("m", "low", client=fake_client(FakeMessages(refused))).ask_json("s", "u", Outer)
    with pytest.raises(LLMError):
        LLM("m", "low", client=fake_client(FakeMessages(text_response({"wrong": 1})))).ask_json("s", "u", Outer)
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    with pytest.raises(LLMError):
        LLM("m", "low", client=fake_client(FakeMessages(error=err))).ask_json("s", "u", Outer)


def test_any_sdk_exception_becomes_llm_error_and_is_recorded(caplog):
    llm = LLM("m", "low", client=fake_client(FakeMessages(error=RuntimeError("socket exploded"))))
    with caplog.at_level("WARNING", logger="morning_edition"):
        with pytest.raises(LLMError, match="socket exploded"):
            llm.ask_json("s", "u", Outer)
    assert len(llm.errors) == 1 and "socket exploded" in llm.errors[0]
    assert "socket exploded" in caplog.text


def test_every_failure_kind_is_recorded():
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    llm = LLM("m", "low", client=fake_client(FakeMessages(error=err)))
    for _ in range(2):
        with pytest.raises(LLMError):
            llm.ask_json("s", "u", Outer)
    assert len(llm.errors) == 2
    bad = LLM("m", "low", client=fake_client(FakeMessages(text_response({"wrong": 1}))))
    with pytest.raises(LLMError):
        bad.ask_json("s", "u", Outer)
    assert len(bad.errors) == 1


def block(kind, **fields):
    return SimpleNamespace(type=kind, **fields)


def research_response(text_blocks, stop="end_turn", extra=()):
    return SimpleNamespace(stop_reason=stop, stop_details=None,
                           content=[*extra, *[block("text", text=t) for t in text_blocks]])


class SequencedMessages(FakeMessages):
    def __init__(self, *responses):
        super().__init__()
        self.queue = list(responses)

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.queue.pop(0)


def test_research_sends_web_search_tool_and_no_structured_format():
    msgs = FakeMessages(research_response(["Found ", "three outlets."]))
    llm = LLM("claude-opus-5-5", "medium", client=fake_client(msgs))
    assert llm.research("sys", "find feeds", max_searches=7) == "Found three outlets."
    call = msgs.calls[0]
    assert call["tools"] == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 7}]
    assert call["output_config"] == {"effort": "medium"}
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"
    assert call["system"] == "sys" and call["max_tokens"] == 16000
    assert call["messages"] == [{"role": "user", "content": "find feeds"}]


def test_research_resumes_after_pause_turn_with_the_assistant_content():
    searching = [block("server_tool_use", id="s1", name="web_search", input={"query": "x"})]
    paused = research_response([], stop="pause_turn", extra=searching)
    final = research_response(["done"])
    msgs = SequencedMessages(paused, final)
    llm = LLM("m", "low", client=fake_client(msgs))
    assert llm.research("s", "u") == "done"
    assert len(msgs.calls) == 2
    assert msgs.calls[1]["messages"] == [{"role": "user", "content": "u"},
                                         {"role": "assistant", "content": searching}]
    assert llm.errors == []


def test_research_keeps_earlier_paused_content_when_it_pauses_again():
    first = [block("server_tool_use", id="s1", name="web_search", input={})]
    second = [block("server_tool_use", id="s2", name="web_search", input={})]
    msgs = SequencedMessages(research_response([], "pause_turn", first),
                             research_response([], "pause_turn", second), research_response(["ok"]))
    assert LLM("m", "low", client=fake_client(msgs)).research("s", "u") == "ok"
    assert msgs.calls[2]["messages"][1] == {"role": "assistant", "content": first + second}


def test_research_gives_up_after_five_continuations():
    pause = lambda: research_response([], "pause_turn", [block("server_tool_use", id="s", name="w", input={})])
    msgs = SequencedMessages(*[pause() for _ in range(7)])
    llm = LLM("m", "low", client=fake_client(msgs))
    with pytest.raises(LLMError, match="paused"):
        llm.research("s", "u")
    assert len(msgs.calls) == 6  # the first call plus five continuations
    assert len(llm.errors) == 1


def test_research_failures_raise_llm_error_and_are_recorded(caplog):
    refused = SimpleNamespace(stop_reason="refusal", content=[],
                              stop_details=SimpleNamespace(category="other", explanation="no"))
    truncated = SimpleNamespace(stop_reason="max_tokens", content=[block("text", text="partial")])
    empty = research_response([])
    boom = FakeMessages(error=RuntimeError("socket exploded"))
    for msgs in (FakeMessages(refused), FakeMessages(truncated), FakeMessages(empty), boom):
        llm = LLM("m", "low", client=fake_client(msgs))
        with caplog.at_level("WARNING", logger="morning_edition"), pytest.raises(LLMError):
            llm.research("s", "u")
        assert len(llm.errors) == 1
    assert "socket exploded" in caplog.text
