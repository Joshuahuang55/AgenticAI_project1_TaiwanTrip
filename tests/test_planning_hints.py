"""Request scope, weather reuse, and honest SDK call records; no model or HTTP requests."""

import datetime as dt
import json
import time

import pytest

import app
from guardrails import ChatState
from trip_context import Preference
from tools import planning_hints as hints
from test_app import _call, _chat, _text, model


def state(message, **preferences):
    value = ChatState(user_texts=[message])
    value.trip.preferences = {key: Preference(item, "specified", message) for key, item in preferences.items()}
    return value


def call(name, city="Taipei", result=None, **args):
    return {"recorded_at": time.time(), "name": name, "args": {"city": city, **args}, "result": json.dumps(result or {"ok": True})}


def suggested(value):
    return {step["tool"] for step in json.loads(value).get("next_steps", [])}


def test_weekend_lodging_result_points_to_missing_sights_and_meals():
    context = state("Help me plan a cheap weekend in Taipei.")
    result = hints.add_next_steps("legal_stay_check", {"city": "Taipei"}, '{"stays":[]}', context)
    assert suggested(result) == {"find_attractions", "find_local_food"}
    assert json.loads(result)["stays"] == []
    assert context.trip.as_dict() == {} and context.tool_calls == []


@pytest.mark.parametrize("name,message", [
    ("legal_stay_check", "Show cheap hotels in Taipei."),
    ("typhoon_backup_plan", "Check tomorrow's weather only."),
    ("find_local_food", "Recommend food in Taipei."),
])
def test_standalone_requests_do_not_receive_trip_completion_hints(name, message):
    assert hints.add_next_steps(name, {"city": "Taipei"}, '{"ok":true}', state(message)) == '{"ok":true}'


@pytest.mark.parametrize("result", ['{"error":"unavailable","hint":"Try later."}', "[]", "invalid", "{}"])
def test_errors_and_unexpected_results_are_preserved(result):
    assert hints.add_next_steps("legal_stay_check", {"city": "Taipei"}, result,
                                state("Plan a cheap weekend in Taipei.")) == result


def test_attempted_tools_in_same_city_are_not_suggested_again():
    context = state("Plan a weekend in Taipei.")
    context.tool_calls = [call("find_attractions", "臺北市", {"error": "unavailable"}),
                          dict(call("find_local_food"), rejected=True)]
    result = hints.add_next_steps("legal_stay_check", {"city": "Taipei"}, '{"stays":[]}', context)
    assert suggested(result) == set()
    context.tool_calls[0]["args"]["city"] = "Tainan"
    assert suggested(hints.add_next_steps("legal_stay_check", {"city": "Taipei"}, '{"stays":[]}', context)) == {"find_attractions"}


@pytest.mark.parametrize("date,end_date,expected", [
    (None, None, False), ("2026-10-02", None, False), ("2026-10-03", None, True),
    ("2026-10-10", "2026-10-10", True), ("2026-10-11", None, False),
    ("2026-10-04", "2026-10-05", False),
])
def test_weather_hint_requires_known_single_day_within_forecast(monkeypatch, date, end_date, expected):
    monkeypatch.setattr(hints, "taiwan_today", lambda: dt.date(2026, 10, 3))
    context = state("I have five hours in Taipei.")
    for key, value in (("start_date", date), ("end_date", end_date)):
        if value:
            context.trip.preferences[key] = Preference(value, "specified", value)
    result = hints.add_next_steps("find_attractions", {"city": "Taipei", "available_minutes": 300}, '{"results":[]}', context)
    assert ("typhoon_backup_plan" in suggested(result)) is expected
    if expected:
        assert json.loads(result)["next_steps"][0]["suggested_args"] == {
            "city": "Taipei", "date": date, "available_minutes": 300}


@pytest.mark.parametrize("city,date,start,end,expected", [
    ("臺北市", "2026-10-04", "10:00", "15:00", False),
    ("Tainan", "2026-10-04", "10:00", "15:00", True),
    ("Taipei", "2026-10-05", "10:00", "15:00", True),
    ("Taipei", "2026-10-04", "08:00", "13:00", True),
])
def test_prior_weather_reuse_matches_city_date_and_window(monkeypatch, city, date, start, end, expected):
    monkeypatch.setattr(hints, "taiwan_today", lambda: dt.date(2026, 10, 3))
    context = state("More suggestions.", start_date="2026-10-04", outing_start_time="10:00", outing_end_time="15:00")
    context.tool_calls = [call("typhoon_backup_plan", city, {
        "date": date, "comparison": {"outing_window": {"start": start, "end": end}}}, date=date)]
    context.turn_start = 1
    result = hints.add_next_steps("find_attractions", {"city": "Taipei", "available_minutes": 300}, '{"results":[]}', context)
    assert ("typhoon_backup_plan" in suggested(result)) is expected


@pytest.mark.parametrize("rejected", [False, True])
def test_failed_weather_is_not_retried_in_same_turn(monkeypatch, rejected):
    monkeypatch.setattr(hints, "taiwan_today", lambda: dt.date(2026, 10, 3))
    context = state("Sightseeing tomorrow.", start_date="2026-10-04")
    context.tool_calls = [dict(call("typhoon_backup_plan", result={"error": "CWA unavailable"}, date="2026-10-04"),
                               rejected=rejected)]
    args = {"city": "Taipei", "available_minutes": 300}
    assert "typhoon_backup_plan" not in suggested(hints.add_next_steps("find_attractions", args, '{"results":[]}', context))
    context.turn_start = 1
    assert "typhoon_backup_plan" in suggested(hints.add_next_steps("find_attractions", args, '{"results":[]}', context))


def test_active_warning_does_not_suggest_traveling_to_sights():
    result = '{"comparison":{"strategy":"postpone_outing"}}'
    assert hints.add_next_steps("typhoon_backup_plan", {"city": "Taipei"}, result,
                                state("Plan a day in Taipei.")) == result


def test_sdk_exposes_hints_without_fabricating_calls(model, monkeypatch):
    executed = []
    monkeypatch.setattr(app, "run_tool", lambda name, args: executed.append(name) or '{"stays":[]}')
    fake = model([[_call("legal_stay_check", '{"city":"Taipei","price_preference":"budget"}')], [_text("Done.")]])
    result = _chat("Help me plan a cheap weekend in Taipei.")
    assert executed == ["legal_stay_check"]
    assert [entry["name"] for entry in result.tool_calls] == executed
    assert suggested(result.tool_calls[0]["result"]) == {"find_attractions", "find_local_food"}
    reply = next(item for item in fake.main_inputs[1] if item.get("type") == "function_call_output")
    assert reply["output"] == result.tool_calls[0]["result"]


def test_sdk_hints_shrink_as_model_chooses_real_calls(model, monkeypatch):
    monkeypatch.setattr(app, "run_tool", lambda name, args: '{"ok":true}')
    model([[_call("legal_stay_check", '{"city":"Taipei"}')],
           [_call("find_attractions", '{"city":"Taipei"}', "c1")],
           [_call("find_local_food", '{"city":"Taipei","district":"大同區","price_preference":"budget"}', "c2")],
           [_text("Done.")]])
    result = _chat("Plan a cheap weekend in Taipei.")
    assert [suggested(entry["result"]) for entry in result.tool_calls] == [
        {"find_attractions", "find_local_food"}, {"find_local_food"}, set()]
    assert result.tool_calls[-1]["args"]["district"] == "大同區"
