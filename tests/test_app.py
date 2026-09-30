"""The /chat harness, with the model mocked."""

import json
from types import SimpleNamespace

import app


def _reply(content=None, calls=()):
    tool_calls = [SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=n, arguments=a))
                  for i, (n, a) in enumerate(calls)] or None
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    msg.model_dump = lambda: {"role": "assistant", "content": content,
                              "tool_calls": [{"id": c.id} for c in tool_calls or []]}
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_malformed_tool_arguments_still_get_a_tool_reply(monkeypatch):
    replies = iter([_reply(calls=[("twd_exchange", "{not json")]), _reply(content="Done.")])
    monkeypatch.setattr(app.litellm, "completion", lambda **kw: next(replies))
    messages = [{"role": "user", "content": "hi"}]
    text, calls = app.run_agent(messages)
    assert text == "Done."
    tool_msg = next(m for m in messages if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "c0" and "not valid JSON" in json.loads(tool_msg["content"])["error"]
    assert calls[0]["name"] == "twd_exchange"


def test_chat_keeps_response_shape_and_reports_only_real_calls(monkeypatch):
    monkeypatch.setattr(app, "run_agent", lambda messages: ("Hello!", []))
    out = app.chat(app.ChatRequest(message="hi"))
    assert out.response == "Hello!" and out.session_id and out.tool_calls == [] and out.map_pins == []
