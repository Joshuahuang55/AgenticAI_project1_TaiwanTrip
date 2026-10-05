"""Plain Python tool registry. Typed SDK definitions live in tools/agent_tools.py."""

import json

from tools import attractions, exchange, food, holidays, lodging, transport, weather
from tools import call_budget, freshness

_MODULES = [
    lodging.legal_stay_check,
    food.find_local_food,
    attractions.find_attractions,
    exchange.twd_exchange,
    transport.hsr_trip_planner,
    holidays.crowd_risk_check,
    weather.typhoon_backup_plan,
]

# What the harness runs: tool name -> Python function.
TOOL_MAP = {fn.__name__: fn for fn in _MODULES}


def run_tool(name: str, args: dict) -> str:
    """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
    if name not in TOOL_MAP:
        return json.dumps({"error": f"Unknown tool '{name}'. Available: {list(TOOL_MAP)}"})
    granted, wait = call_budget.acquire()
    if not granted:
        return json.dumps({
            "error": f"Lookup limit reached ({call_budget.LIMIT} tool calls per minute).",
            "hint": f"Do not retry this turn. Answer with results already gathered and tell the user "
                    f"new lookups resume in about {wait} seconds.",
            "retry_after_seconds": wait,
        })
    try:
        token = freshness.begin()
        try:
            result = TOOL_MAP[name](**args)
        finally:
            metadata = freshness.finish(token)
        if metadata:
            data = json.loads(result)
            if isinstance(data, dict):
                data["data_freshness"] = metadata
                result = json.dumps(data, ensure_ascii=False)
        return result
    except (TypeError, ValueError) as e:
        return json.dumps({"error": f"Bad arguments for {name}: {e}", "hint": "Fix the arguments and retry."})
    except Exception as e:  # A bug in one tool should not take down the chat.
        return json.dumps({"error": f"{name} failed unexpectedly: {type(e).__name__}",
                           "hint": "Tell the user this lookup failed; do not invent the data."})
