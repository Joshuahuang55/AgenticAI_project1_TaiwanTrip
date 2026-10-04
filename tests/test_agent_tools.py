"""SDK-generated schemas and validation, history, and execution contracts; no live requests."""

import json
import inspect
import threading

import pytest

import app
from tools import TOOL_MAP, attraction_preferences, food_preferences, transport
from test_app import _call, _chat, _text, model


def test_generated_tools_match_registry_and_hide_sdk_context():
    assert {tool.name for tool in app.AGENT.tools} == set(TOOL_MAP)
    for tool in app.AGENT.tools:
        props = tool.params_json_schema["properties"]
        signature = inspect.signature(TOOL_MAP[tool.name])
        assert set(props) == set(signature.parameters)
        for name, parameter in signature.parameters.items():
            if parameter.default is not inspect.Parameter.empty:
                assert props[name]["default"] == parameter.default
        assert "ctx" not in props
        assert all(prop.get("description") for prop in props.values())
        assert tool.description and tool.tool_input_guardrails and tool.tool_output_guardrails
        assert not tool.strict_json_schema  # Gemini keeps optional/default arguments optional.
    tools = {tool.name: tool for tool in app.AGENT.tools}
    assert tools["hsr_trip_planner"].params_json_schema["required"] == ["origin", "destination", "date"]
    assert tools["find_attractions"].params_json_schema["required"] == ["city"]


def enum(schema):
    if "enum" in schema:
        return schema["enum"]
    if schema.get("type") == "array":
        return enum(schema["items"])
    for item in schema.get("anyOf", []):
        values = enum(item)
        if values:
            return values
    return []


def test_generated_choices_match_domain_choices():
    schemas = {tool.name: tool.params_json_schema["properties"] for tool in app.AGENT.tools}
    assert enum(schemas["find_attractions"]["interests"]) == list(attraction_preferences.INTERESTS)
    assert enum(schemas["find_attractions"]["setting"]) == list(attraction_preferences.SETTINGS)
    assert enum(schemas["find_local_food"]["dietary"]) == list(food_preferences.DIETARY)
    assert enum(schemas["find_local_food"]["price_preference"]) == list(food_preferences.PRICE_PREFERENCES)
    assert enum(schemas["hsr_trip_planner"]["preference"]) == list(transport.PREFERENCES)


@pytest.mark.parametrize("name,args", [
    ("legal_stay_check", {"city": "Taipei"}),
    ("find_local_food", {"city": "Taipei"}),
    ("find_attractions", {"city": "Taipei"}),
    ("twd_exchange", {"amount": 100}),
    ("hsr_trip_planner", {"origin": "Taipei", "destination": "Tainan", "date": "2026-10-12"}),
    ("crowd_risk_check", {"start_date": "2026-10-12", "end_date": "2026-10-12"}),
    ("typhoon_backup_plan", {"city": "Taipei"}),
])
def test_each_decorated_tool_executes_and_records_without_inserting_defaults(model, monkeypatch, name, args):
    calls = []
    main_thread = threading.get_ident()
    def execute(tool, values):
        calls.append((tool, dict(values), threading.get_ident()))
        return '{"ok":true}'
    monkeypatch.setattr(app, "run_tool", execute)
    model([[_call(name, json.dumps(args))], [_text("Done.")]])
    result = _chat("Travel in Taiwan.")
    assert calls[0][:2] == (name, args)
    assert calls[0][2] != main_thread  # Synchronous network/data work stays off the event loop.
    assert result.tool_calls == [{"name": name, "args": args, "result": '{"ok":true}'}]


@pytest.mark.parametrize("name,args", [
    ("find_attractions", {}), ("find_attractions", {"city": 12}),
    ("find_attractions", {"city": "Taipei", "limit": 0}),
    ("find_attractions", {"city": "Taipei", "limit": 11}),
    ("find_attractions", {"city": "Taipei", "available_minutes": True}),
    ("find_attractions", {"city": "Taipei", "interests": ["anything"]}),
    ("find_attractions", {"city": "Taipei", "style": "cheap"}),
    ("find_local_food", {"city": "Taipei", "confirmed_only": "false"}),
    ("legal_stay_check", {"city": "Taipei", "max_price_twd": 0}),
    ("legal_stay_check", {"city": "Taipei", "max_price_twd": True}),
    ("twd_exchange", {"amount": -1}), ("twd_exchange", {"amount": "100"}),
    ("twd_exchange", {"amount": float("nan")}),
    ("hsr_trip_planner", {"origin": "Taipei", "destination": "Tainan"}),
    ("find_attractions", {"city": "Taipei", "extra": "unused"}),
    ("find_attractions", {"city": "Taipei", "invoke": "unused"}),
])
def test_sdk_validation_returns_recorded_error_without_running_domain_tool(model, monkeypatch, name, args):
    calls = []
    monkeypatch.setattr(app, "run_tool", lambda *values: calls.append(values) or "{}")
    fake = model([[_call(name, json.dumps(args))], [_text("Please correct the arguments.")]])
    result = _chat("Travel in Taiwan.")
    assert calls == [] and len(result.tool_calls) == 1
    assert json.loads(result.tool_calls[0]["result"])["error"]
    assert json.loads(result.tool_calls[0]["result"])["hint"]
    reply = next(i for i in fake.main_inputs[1] if i.get("type") == "function_call_output")
    assert reply["output"] == result.tool_calls[0]["result"]


@pytest.mark.parametrize("arguments", ["[]", "null", '"Taipei"'])
def test_non_object_arguments_return_an_error_and_preserve_history(model, monkeypatch, arguments):
    calls = []
    monkeypatch.setattr(app, "run_tool", lambda *args: calls.append(args) or "{}")
    fake = model([[_call("find_attractions", arguments)], [_text("Please retry.")], [_text("Still here.")]])
    first = _chat("Sights in Taipei.")
    assert "JSON object" in json.loads(first.tool_calls[0]["result"])["error"] and calls == []
    second = _chat("Thanks", first.session_id)
    assert second.response == "Still here."
    assert any(i.get("type") == "function_call_output" for i in fake.main_inputs[2])


def test_validation_error_can_be_corrected_by_a_later_tool_call(model, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "run_tool", lambda name, args: calls.append(args) or "{}")
    model([[_call("find_attractions", '{"city":"Taipei","limit":0}')],
           [_call("find_attractions", '{"city":"Taipei","limit":3}', call_id="c1")], [_text("Done.")]])
    result = _chat("Sights in Taipei.")
    assert calls == [{"city": "Taipei", "limit": 3}]
    assert len(result.tool_calls) == 2 and json.loads(result.tool_calls[0]["result"])["error"]


def test_nullable_optional_arguments_restore_saved_outing_context(model, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "run_tool", lambda name, args: calls.append(dict(args)) or "{}")
    model([[_text("Ready.")], [_call("typhoon_backup_plan", '{"city":"Taipei","available_minutes":null,"start_time":null,"end_time":null}')],
           [_text("Done.")]], context_updates=[
        [{"field": "city", "operation": "set", "value": "Taipei", "evidence": "Taipei"},
         {"field": "available_minutes", "operation": "set", "value": "300", "evidence": "five hours"},
         {"field": "outing_start_time", "operation": "set", "value": "10:00", "evidence": "10:00"},
         {"field": "outing_end_time", "operation": "set", "value": "15:00", "evidence": "15:00"}], []])
    first = _chat("Taipei for five hours, 10:00 to 15:00.")
    _chat("Check the weather", first.session_id)
    assert calls == [{"city": "Taipei", "available_minutes": 300, "start_time": "10:00", "end_time": "15:00"}]
