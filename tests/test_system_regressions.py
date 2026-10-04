"""State transitions from the system review; no live providers or data requests."""

import asyncio
import datetime as dt
import json
import time

import pytest
import requests

import app
from agent_reply import ProposedPlace, TravelReply, resolve_reply
from guardrails import ChatState
from planning_context import MAX_RECORDS, PlanningChoice, PlanningContext
from tools import exchange, freshness, lodging, planning_hints, weather
from trip_context import PreferenceUpdate, TripContext, TripUpdates
from test_app import _call, _chat, _text, model


def sight(name):
    return {"city": "臺北市", "source": "Test listings", "results": [
        {"name": name, "name_en": name, "lat": 25.03, "lon": 121.5}]}


def test_distinct_searches_and_stable_candidate_identity():
    memory = PlanningContext()
    memory.remember("find_attractions", {"city": "Taipei", "keyword": "museum"}, sight("Museum"))
    first = next(iter(memory.records.values()))["candidates"][0]["reference_id"]
    memory.remember("find_attractions", {"city": "Taipei", "keyword": "park"}, sight("Park"))
    memory.remember("find_attractions", {"city": "臺北市", "keyword": "art"}, sight("Museum"))
    assert len(memory.records) == 3
    assert list(memory.records.values())[-1]["candidates"][0]["reference_id"] == first


@pytest.mark.parametrize("selection", ["confirmed", "proposed"])
def test_active_references_resolve_after_lookup_eviction(selection):
    memory = PlanningContext()
    memory.remember("find_attractions", {"city": "Taipei"}, sight("Museum"))
    record = next(iter(memory.records.values()))
    reference = record["candidates"][0]["reference_id"]
    reply = TravelReply(message="Visit Museum.", places=[ProposedPlace(
        reference_id=reference, label="Museum", role="recommended")], sources=[record["reference_id"]])
    if selection == "confirmed":
        memory.confirm([PlanningChoice(kind="attraction", name="Museum", evidence="Pick Museum.")], "Pick Museum.")
    else:
        resolved = resolve_reply(reply, memory)
        memory.remember_proposal(resolved.message, "Taipei", True, resolved.places, resolved.sources)
    for index in range(MAX_RECORDS + 2):
        memory.remember("find_attractions", {"city": "Taipei", "keyword": str(index)}, sight(str(index)))
    assert len(memory.records) == MAX_RECORDS
    resolved = resolve_reply(reply, memory)
    assert resolved.places[0]["name"] == "Museum"
    assert resolved.sources[0]["source"] == "Test listings"


def test_corrected_time_window_and_duration_remain_consistent():
    def apply(context, **values):
        return context.apply(TripUpdates(updates=[PreferenceUpdate(field=key, operation="set", value=value,
                            evidence="update") for key, value in values.items()]), "update")
    trip = apply(TripContext(), city="Taipei", start_date="2026-10-12", outing_start_time="10:00",
                 outing_end_time="15:00", available_minutes="300")
    trip = apply(trip, outing_end_time="18:00")
    assert trip.preferences["available_minutes"].value == "480"
    assert trip.preferences["available_minutes"].derived
    trip = apply(trip, available_minutes="180")
    assert trip.preferences["outing_end_time"].value == "13:00"
    trip = apply(trip, outing_start_time="11:00", available_minutes="300")
    assert trip.preferences["outing_end_time"].value == "16:00"
    assert trip.outing_matches("臺北市", "2026-10-12")
    assert not trip.outing_matches("Tainan", "2026-10-12")
    assert not trip.outing_matches("Taipei", "2026-10-13")
    trip = apply(trip, start_date="2026-10-13")
    assert "available_minutes" not in trip.preferences


def test_successful_lookup_is_returned_after_provider_failure(model, monkeypatch):
    monkeypatch.setattr(app, "run_tool", lambda *_: json.dumps(sight("Museum")))
    model([[_call("find_attractions", '{"city":"Taipei"}')]])
    result = _chat("Suggest attractions in Taipei.")
    assert "Completed lookups" in result.response
    assert result.tool_calls[0]["name"] == "find_attractions"
    assert app.sessions[result.session_id].planning.records
    assert result.places == []  # No uncompleted prose is promoted to a recommendation.


def test_turn_deadline_preserves_finished_data(monkeypatch):
    monkeypatch.setattr(app, "sessions", app.OrderedDict())
    monkeypatch.setattr(app, "TURN_TIMEOUT_SECONDS", 0.01)
    async def slow(state, message):
        state.turn_start = len(state.tool_calls)
        state.tool_calls.append({"name": "twd_exchange", "args": {}, "result": '{"rate":32,"source":"Daily rates"}'})
        await asyncio.sleep(10)
    monkeypatch.setattr(app, "run_turn", slow)
    result = _chat("Convert my budget.")
    assert "too long" in result.response
    assert len(result.tool_calls) == 1


def test_active_session_is_not_evicted_and_expiry_is_visible(monkeypatch):
    monkeypatch.setattr(app, "sessions", app.OrderedDict())
    monkeypatch.setattr(app, "MAX_SESSIONS", 2)
    busy = app.get_session("busy")
    busy.active_requests = 1
    app.get_session("idle")
    app.get_session("new")
    assert "busy" in app.sessions and "idle" not in app.sessions
    app.sessions["new"].last_used -= app.SESSION_TTL_SECONDS + 1
    assert app.session_status("new")["status"] == "expired"
    async def simple(state, message):
        return message
    monkeypatch.setattr(app, "run_turn", simple)
    assert _chat("Hello", "new").session_status == "expired"
    assert app.sessions["busy"] is busy


def test_clear_cancels_active_turn_before_removing_its_state(monkeypatch):
    monkeypatch.setattr(app, "sessions", app.OrderedDict())
    started = asyncio.Event()
    async def blocked(state, message):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(app, "run_turn", blocked)
    async def scenario():
        running = asyncio.create_task(app.chat(app.ChatRequest(message="hello", session_id="busy")))
        await started.wait()
        state = app.sessions["busy"]
        app.clear("busy")
        assert app.sessions["busy"] is state and state.invalidated
        with pytest.raises(asyncio.CancelledError):
            await running
        assert "busy" not in app.sessions
    asyncio.run(scenario())


@pytest.mark.parametrize("dataset,age,usable", [
    (weather.WEEK_FORECAST, 3600, True), (weather.WEEK_FORECAST, 48 * 3600, False),
    (weather.TYPHOON_WARNING, 600, True), (weather.TYPHOON_WARNING, 3600, False),
])
def test_weather_cache_fallback_has_bounded_age(monkeypatch, dataset, age, usable):
    monkeypatch.setenv("CWA_API_KEY", "test")
    monkeypatch.setattr(weather, "_cache", {(dataset, ()): (time.time() - age, {"info": []})})
    def offline(*args, **kwargs):
        raise requests.ConnectionError()
    monkeypatch.setattr(weather._http, "get", offline)
    token = freshness.begin()
    result = weather._cwa_get(dataset, {})
    metadata = freshness.finish(token)
    assert ("error" not in result) is usable
    if usable:
        assert metadata[0]["stale"] and metadata[0]["age_seconds"] >= age
        assert "retrieved_at" in metadata[0]


def test_exchange_without_history_preserves_conversion_and_has_no_trend(monkeypatch):
    monkeypatch.setattr(exchange, "_fetch", lambda cur, date: {"date": "2026-10-04", "rates": {"twd": 32}} if date == "latest" else None)
    result = json.loads(exchange.twd_exchange(100))
    assert result["converted_amount"] == 3200
    assert result["diff_percent"] is None and result["avg_30d"] is None
    assert result["comparison_status"] == "unavailable"
    assert "about the same" not in result["vs_30d"]


def test_similar_registered_names_are_candidates_not_a_verdict(monkeypatch):
    monkeypatch.setattr(lodging, "_search_name", lambda *_: [
        {"HotelName": "Example East Hotel", "HotelLicenseNumber": "A", "PostalAddress": {"StreetAddress": "East Street"}},
        {"HotelName": "Example West Hotel", "HotelLicenseNumber": "B", "PostalAddress": {"StreetAddress": "West Street"}}])
    result = json.loads(lodging.legal_stay_check("Taipei", name="Example Hotel"))
    assert result["is_registered"] is None and result["match_status"] == "candidates"
    assert len(result["matches"]) == 2


def test_old_weather_lookup_is_not_reused(monkeypatch):
    from trip_context import Preference
    monkeypatch.setattr(planning_hints, "taiwan_today", lambda: dt.date(2026, 10, 4))
    state = ChatState(user_texts=["More sightseeing options."])
    state.trip.preferences["start_date"] = Preference("2026-10-05", "specified", "tomorrow")
    state.tool_calls = [{"name": "typhoon_backup_plan", "recorded_at": time.time() - 3600,
                         "args": {"city": "Taipei", "date": "2026-10-05"},
                         "result": json.dumps({"date": "2026-10-05", "comparison": {"outing_window": {"start": "09:00", "end": "14:00"}}})}]
    state.turn_start = 1
    result = json.loads(planning_hints.add_next_steps("find_attractions", {"city": "Taipei", "available_minutes": 300}, '{"results":[]}', state))
    assert result["next_steps"][0]["tool"] == "typhoon_backup_plan"
