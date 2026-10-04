"""Preference merging and the SDK/session integration, without HTTP or real model calls."""

import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import pytest

import app
import guardrails
import trip_context as tc
from test_app import _call, _chat, _text, model  # reuse the SDK scripted-model fixture


def update(field, value=None, evidence=None, operation="set"):
    return {"field": field, "operation": operation, "value": value,
            "evidence": evidence if evidence is not None else value}


def apply(context, message, *updates):
    return context.apply(tc.TripUpdates(updates=[tc.PreferenceUpdate(**u) for u in updates]), message)


def test_city_correction_clears_old_area_and_preserves_other_preferences():
    original = apply(tc.TripContext(), "Taipei, Ximen, vegetarian, cheap",
                     update("city", "Taipei"), update("area", "Ximen"),
                     update("dietary_needs", "vegetarian"), update("budget", "cheap"))
    corrected = apply(original, "Actually, Tainan", update("city", "Tainan"))
    assert corrected.preferences["city"].value == "Tainan"
    assert "area" not in corrected.preferences
    assert corrected.preferences["dietary_needs"].value == "vegetarian"
    assert corrected.preferences["budget"].value == "cheap"
    assert original.preferences["city"].value == "Taipei"  # supports rollback


def test_same_city_does_not_clear_area_and_new_city_can_supply_its_own_area():
    original = apply(tc.TripContext(), "Taipei Ximen", update("city", "Taipei"), update("area", "Ximen"))
    same = apply(original, "More food in Taipei", update("city", "Taipei"))
    assert same.preferences["area"].value == "Ximen"
    moved = apply(same, "Tainan, West Central", update("city", "Tainan"), update("area", "West Central"))
    assert moved.preferences["area"].value == "West Central"


def test_no_preference_and_withdrawal_are_distinct_from_missing_information():
    current = apply(tc.TripContext(), "Anywhere, no questions", update("area", evidence="Anywhere", operation="no_preference"),
                    update("follow_up_questions", "avoid", "no questions"))
    assert current.preferences["area"].status == "no_preference"
    assert current.preferences["area"].value is None
    assert "budget" not in current.preferences
    cleared = apply(current, "Forget the area", update("area", evidence="Forget the area", operation="clear"))
    assert cleared.preferences["area"].status == "unknown"
    assert cleared.preferences["follow_up_questions"].value == "avoid"


def test_changes_need_evidence_in_the_current_user_message():
    context = apply(tc.TripContext(), "thanks", update("city", "Taipei"),
                        update("budget", "cheap", " "), update("dietary_needs", "vegan", "vegan"))
    assert context.as_dict() == {}


def test_outing_time_survives_refinement_and_explicit_correction():
    current = apply(tc.TripContext(), "5 hours in Taipei", update("city", "Taipei"),
                    update("available_minutes", "300", "5 hours"))
    refined = apply(current, "indoor art and nature", update("setting", "indoor"),
                    update("interests", "art and nature"))
    assert refined.preferences["available_minutes"].value == "300"
    corrected = apply(refined, "Actually 2 hours", update("available_minutes", "120", "2 hours"))
    assert corrected.preferences["available_minutes"].value == "120"
    assert corrected.preferences["setting"].value == "indoor"
    moved = apply(refined, "Tainan", update("city", "Tainan"))
    assert "available_minutes" not in moved.preferences


@pytest.mark.parametrize("field,value", [("available_minutes", "0"), ("available_minutes", "721"),
                                         ("available_minutes", "5 hours"), ("available_minutes", "90.5"),
                                         ("setting", "maybe indoors")])
def test_outing_preferences_validate_before_saving(field, value):
    assert apply(tc.TripContext(), value, update(field, value)).as_dict() == {}


def test_outing_clock_times_survive_refinement_and_clear_on_city_change():
    current = apply(tc.TripContext(), "Taipei from 10am to 3pm", update("city", "Taipei"),
                    update("outing_start_time", "10:00", "10am"), update("outing_end_time", "15:00", "3pm"))
    refined = apply(current, "art", update("interests", "art"))
    assert refined.preferences["outing_start_time"].value == "10:00"
    assert refined.preferences["outing_end_time"].value == "15:00"
    moved = apply(refined, "Tainan", update("city", "Tainan"))
    assert "outing_start_time" not in moved.preferences and "outing_end_time" not in moved.preferences


def test_corrected_start_drops_incompatible_old_end_and_inverted_pair_is_rejected():
    current = apply(tc.TripContext(), "10:00 to 15:00", update("outing_start_time", "10:00"), update("outing_end_time", "15:00"))
    revised = apply(current, "start at 16:00", update("outing_start_time", "16:00"))
    assert revised.preferences["outing_start_time"].value == "16:00"
    assert "outing_end_time" not in revised.preferences
    rejected = apply(current, "16:00 to 12:00", update("outing_start_time", "16:00"), update("outing_end_time", "12:00"))
    assert rejected.as_dict() == current.as_dict()


@pytest.mark.parametrize("value", ["9am", "24:00", "10:00:00", "10:00+08:00"])
def test_outing_times_require_local_hhmm(value):
    assert apply(tc.TripContext(), value, update("outing_start_time", value)).as_dict() == {}


def test_weather_wrapper_reuses_older_outing_window_and_time_budget(model, monkeypatch):
    monkeypatch.setattr(app, "MAX_USER_TURNS", 1)
    received = []
    monkeypatch.setattr(app, "run_tool", lambda name, args: received.append(dict(args)) or json.dumps({"forecast": None}))
    model([[_text("Initial suggestions.")], [_text("Hello.")],
           [_call("typhoon_backup_plan", '{"city":"Taipei"}')],
           [_text("Your indoor alternative.")]], context_updates=[
        [update("city", "Taipei"), update("outing_start_time", "10:00"), update("outing_end_time", "15:00"),
         update("available_minutes", "300", "five hours")], [], [update("interests", "art")]])
    first = _chat("Taipei, five hours, 10:00 to 15:00")
    _chat("Hello", first.session_id)
    result = _chat("art", first.session_id)
    assert received == [{"city": "Taipei", "available_minutes": 300,
                         "start_time": "10:00", "end_time": "15:00"}]
    assert result.tool_calls[0]["args"] == received[0]


def test_tool_wrapper_restores_time_and_setting_on_preference_only_followup(model, monkeypatch):
    monkeypatch.setattr(app, "MAX_USER_TURNS", 1)
    received = []
    def tool(name, args):
        received.append(dict(args))
        return json.dumps({"results": [], "preferences": args})
    monkeypatch.setattr(app, "run_tool", tool)
    fake = model([[_text("Initial suggestions.")], [_text("Hello.")],
                  [_call("find_attractions", '{"city":"Taipei","interests":["art","nature"]}')],
                  [_text("Here is your five-hour outing.")]], context_updates=[
        [update("city", "Taipei"), update("available_minutes", "300", "5 hours")], [],
        [update("setting", "indoor"), update("interests", "art and nature")],
    ])
    first = _chat("5 hours in Taipei")
    _chat("Hello", first.session_id)
    assert "5 hours" not in json.dumps(app.sessions[first.session_id].history)
    refined = _chat("indoor art and nature", first.session_id)
    assert received[0]["available_minutes"] == 300 and received[0]["setting"] == "indoor"
    assert refined.tool_calls[0]["args"] == received[0]
    assert '"available_minutes": {"value": "300"' in fake.main_instructions[-1]


@pytest.mark.parametrize("value", ["2026-02-30", "2026-1-1", "next Friday", "20261008"])
def test_invalid_or_non_iso_dates_are_not_saved(value):
    assert apply(tc.TripContext(), value, update("start_date", value)).as_dict() == {}


def test_date_correction_drops_the_old_other_boundary():
    current = apply(tc.TripContext(), "2026-10-08 to 2026-10-10",
                    update("start_date", "2026-10-08"), update("end_date", "2026-10-10"))
    corrected = apply(current, "Now leaving on 2026-10-12", update("start_date", "2026-10-12"))
    assert corrected.preferences["start_date"].value == "2026-10-12"
    assert "end_date" not in corrected.preferences


def test_date_correction_keeps_a_compatible_confirmed_boundary():
    current = apply(tc.TripContext(), "2026-10-08 to 2026-10-10",
                    update("start_date", "2026-10-08"), update("end_date", "2026-10-10"))
    corrected = apply(current, "Return on 2026-10-15", update("end_date", "2026-10-15"))
    assert corrected.preferences["start_date"].value == "2026-10-08"
    assert corrected.preferences["end_date"].value == "2026-10-15"


def test_inverted_date_range_does_not_replace_previous_valid_range():
    current = apply(tc.TripContext(), "2026-10-08 to 2026-10-10",
                    update("start_date", "2026-10-08"), update("end_date", "2026-10-10"))
    invalid = apply(current, "2026-10-12 to 2026-10-10",
                    update("start_date", "2026-10-12"), update("end_date", "2026-10-10"))
    assert invalid.as_dict() == current.as_dict()


def test_dates_and_question_policy_cannot_be_saved_as_no_preference():
    current = apply(tc.TripContext(), "any date; surprise me", update("start_date", evidence="any date", operation="no_preference"),
                    update("follow_up_questions", evidence="surprise me", operation="no_preference"),
                    update("follow_up_questions", "always ask five", "surprise me"))
    assert current.as_dict() == {}


def test_sdk_instructions_receive_current_preferences_before_first_main_call(model):
    fake = model([[_text("Here are some choices.")], [_text("Here are refined choices.")]], context_updates=[
        [update("city", "Taipei")],
        [update("area", "Ximen"), update("dietary_needs", "vegetarian")],
    ])
    first = _chat("Food in Taipei?")
    _chat("Ximen, vegetarian", first.session_id)
    state = app.sessions[first.session_id]
    assert state.trip.preferences["city"].value == "Taipei"
    assert state.trip.preferences["area"].value == "Ximen"
    assert state.trip.preferences["dietary_needs"].evidence == "vegetarian"
    assert '"city": {"value": "Taipei"' in fake.main_instructions[0]
    assert '"dietary_needs": {"value": "vegetarian"' in fake.main_instructions[1]
    assert fake.context_inputs[1]["saved_preferences"]["city"]["value"] == "Taipei"


def test_context_survives_history_trimming(model, monkeypatch):
    monkeypatch.setattr(app, "MAX_USER_TURNS", 1)
    fake = model([[_text("First.")], [_text("Second.")], [_text("Third.")]], context_updates=[
        [update("city", "Taipei"), update("dietary_needs", "vegetarian")], [], [],
    ])
    first = _chat("Taipei, vegetarian")
    _chat("Thanks", first.session_id)
    assert "vegetarian" not in json.dumps(app.sessions[first.session_id].history)
    _chat("More food please", first.session_id)
    assert '"dietary_needs": {"value": "vegetarian"' in fake.main_instructions[2]


def test_sessions_are_isolated_and_clear_removes_trip_context(model):
    fake = model([[_text("Taipei choices.")], [_text("Tainan choices.")], [_text("Fresh trip.")]], context_updates=[
        [update("city", "Taipei")], [update("city", "Tainan")], [],
    ])
    first, second = _chat("Taipei"), _chat("Tainan")
    assert first.session_id != second.session_id
    assert app.sessions[first.session_id].trip.preferences["city"].value == "Taipei"
    assert app.sessions[second.session_id].trip.preferences["city"].value == "Tainan"
    assert fake.context_inputs[1]["saved_preferences"] == {}
    app.clear(first.session_id)
    assert first.session_id not in app.sessions
    _chat("Hello", first.session_id)
    assert fake.context_inputs[2]["saved_preferences"] == {}
    assert app.sessions[first.session_id].trip.as_dict() == {}
    assert app.sessions[second.session_id].trip.preferences["city"].value == "Tainan"


def test_evicted_session_does_not_restore_context(model, monkeypatch):
    monkeypatch.setattr(app, "MAX_SESSIONS", 1)
    fake = model([[_text("First.")], [_text("Second.")], [_text("Fresh.")]], context_updates=[
        [update("city", "Taipei")], [update("city", "Tainan")], [],
    ])
    first = _chat("Taipei")
    _chat("Tainan")
    _chat("Hello", first.session_id)
    assert fake.context_inputs[2]["saved_preferences"] == {}


def test_output_guardrail_rejection_rolls_back_preferences(model):
    model([[_text("Safe reply.")], [_text("Stay at Unverified Inn (未驗證民宿).")]], context_updates=[
        [update("city", "Taipei")], [update("city", "Tainan")],
    ])
    first = _chat("Taipei")
    failed = _chat("Tainan", first.session_id)
    assert failed.response == guardrails.REJECTIONS["unverified_stay"]
    assert app.sessions[first.session_id].trip.preferences["city"].value == "Taipei"


def test_model_failure_after_extraction_rolls_back_preferences(model):
    # No scripted main response: the model fails after the extractor succeeds.
    model([], context_updates=[[update("city", "Taipei")]])
    failed = _chat("Taipei")
    assert "couldn't reach the model" in failed.response
    assert app.sessions[failed.session_id].trip.as_dict() == {}


def test_empty_main_response_rolls_back_preferences(model):
    model([[_text("")]], context_updates=[[update("city", "Taipei")]])
    failed = _chat("Taipei")
    assert app.sessions[failed.session_id].trip.as_dict() == {}


@pytest.mark.parametrize("failure", [RuntimeError("down"), asyncio.TimeoutError()])
def test_extraction_failure_preserves_context(monkeypatch, failure):
    current = apply(tc.TripContext(), "Taipei", update("city", "Taipei"))

    async def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(tc.Runner, "run", fail)
    assert asyncio.run(tc.extract_trip_context(current, "Tainan", "", None, app.AGENT.model_settings)) is current


def test_extractor_uses_taiwan_date_and_validated_sdk_output(monkeypatch):
    seen = {}
    monkeypatch.setattr(tc, "taiwan_today", lambda: dt.date(2026, 10, 3))

    async def capture(agent, payload, **kwargs):
        seen.update(json.loads(payload))
        assert agent.output_type is tc.TripUpdates
        assert agent.tools == [] and kwargs["max_turns"] == 1
        assert agent.model_settings.reasoning.effort == "minimal"
        assert agent.model_settings.extra_args == app.AGENT.model_settings.extra_args
        return SimpleNamespace(final_output=tc.TripUpdates(updates=[
            tc.PreferenceUpdate(**update("start_date", "2026-10-04", "tomorrow")),
        ]))

    monkeypatch.setattr(tc.Runner, "run", capture)
    context = asyncio.run(tc.extract_trip_context(tc.TripContext(), "tomorrow", "Which day?", None, app.AGENT.model_settings))
    assert seen["today_in_taiwan"] == "2026-10-03"
    assert context.preferences["start_date"].value == "2026-10-04"
    assert app.AGENT.model_settings.reasoning.effort == "medium"


def test_invalid_schema_does_not_break_chat(model):
    fake = model([[_text("Still works.")]], context_updates=[[update("not_a_field", "Taipei")]])
    result = _chat("Taipei")
    assert result.response == "Still works." and len(fake.context_inputs) == 1
    assert app.sessions[result.session_id].trip.as_dict() == {}


def test_overlapping_requests_serialize_only_within_the_same_session(monkeypatch):
    monkeypatch.setattr(app, "sessions", app.OrderedDict())
    started, release = asyncio.Event(), asyncio.Event()
    observed = []

    async def run(state, message):
        if message == "first":
            started.set()
            await release.wait()
            state.trip = apply(state.trip, "Taipei", update("city", "Taipei"))
        elif message == "second":
            observed.append(state.trip.preferences["city"].value)
        return message

    monkeypatch.setattr(app, "run_turn", run)

    async def scenario():
        first = asyncio.create_task(app.chat(app.ChatRequest(message="first", session_id="a")))
        await started.wait()
        second = asyncio.create_task(app.chat(app.ChatRequest(message="second", session_id="a")))
        independent = await app.chat(app.ChatRequest(message="other", session_id="b"))
        assert independent.response == "other" and observed == []
        release.set()
        replies = await asyncio.gather(first, second)
        assert [r.response for r in replies] == ["first", "second"]

    asyncio.run(asyncio.wait_for(scenario(), timeout=2))
    assert observed == ["Taipei"]
