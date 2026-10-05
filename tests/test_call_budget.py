"""The shared tool-call budget, its /quota and /chat surfaces, and English labels for feed text."""

import json

import pytest
from fastapi import HTTPException

import app
from tools import call_budget, run_tool
from tools.english_labels import holiday_en, weather_en
from test_app import _chat, _text, model  # noqa: F401  (scripted SDK model fixture)


@pytest.fixture
def clock(monkeypatch):
    """A controllable monotonic clock and a five-call budget."""
    now = [1000.0]
    monkeypatch.setattr(call_budget.time, "monotonic", lambda: now[0])
    call_budget.reset(5)
    return now


def test_budget_allows_five_calls_then_waits_for_the_oldest(clock):
    for second in range(5):
        clock[0] = 1000.0 + second * 10
        assert call_budget.acquire()[0]
    clock[0] = 1045.0
    granted, wait = call_budget.acquire()
    assert not granted and wait == 15  # the first call frees at 1060
    status = call_budget.status()
    assert status["remaining"] == 0 and status["frees_in_seconds"] == [15, 25, 35, 45, 55]
    clock[0] = 1060.0
    assert call_budget.status()["remaining"] == 1
    assert call_budget.acquire()[0]


def test_exhausted_budget_returns_a_tool_error_without_running_the_tool(clock, monkeypatch):
    ran = []
    monkeypatch.setitem(__import__("tools").TOOL_MAP, "twd_exchange", lambda **args: ran.append(args) or "{}")
    for _ in range(5):
        run_tool("twd_exchange", {"amount": 1})
    result = json.loads(run_tool("twd_exchange", {"amount": 1}))
    assert len(ran) == 5
    assert "Lookup limit reached" in result["error"]
    assert result["retry_after_seconds"] == 60 and "Do not retry" in result["hint"]


def test_unknown_tools_do_not_spend_the_budget(clock):
    run_tool("no_such_tool", {})
    assert call_budget.status()["remaining"] == 5


def test_quota_route_and_chat_response_report_the_budget(clock, model):
    call_budget.acquire()
    assert app.tool_quota() == {"limit": 5, "remaining": 4, "window_seconds": 60, "frees_in_seconds": [60]}
    model([[_text("Hello!")]])
    assert _chat("hi").tool_quota["remaining"] == 4


def test_chat_is_refused_while_the_budget_is_empty(clock, model):
    fake = model([[_text("Hello!")]])
    for _ in range(5):
        call_budget.acquire()
    with pytest.raises(HTTPException) as refused:
        _chat("hi")
    assert refused.value.status_code == 429
    assert refused.value.detail["tool_quota"]["remaining"] == 0
    assert fake.turns  # no model call was spent


@pytest.mark.parametrize("note, label", [
    ("國慶日補假", "National Day (observed)"),
    ("中秋節", "Mid-Autumn Festival"),
    ("兒童節及民族掃墓節", "Children's Day & Tomb Sweeping Day"),
    ("補行上班", "Make-up workday"),
    ("補假", "Day off (observed holiday)"),
    ("", None),
    ("某個新節日", None),
])
def test_holiday_notes_translate_or_return_none(note, label):
    assert holiday_en(note) == label


@pytest.mark.parametrize("text, label", [
    ("晴時多雲", "Sunny, at times partly cloudy"),
    ("多雲時陰短暫陣雨", "Partly cloudy, at times overcast with brief showers"),
    ("陰時多雲陣雨或雷雨", "Overcast, at times partly cloudy with showers or thunderstorms"),
    ("陰天", "Overcast"),
    ("陣雨", "Showers"),
    ("颱風", None),
    (None, None),
])
def test_cwa_weather_translates_or_returns_none(text, label):
    assert weather_en(text) == label
