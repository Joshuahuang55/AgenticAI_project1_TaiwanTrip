"""Tool registry: what the model sees (TOOLS) and what the harness runs (run_tool).

To add a tool, write a module with a function and a SCHEMA, then register it below.
"""

import json

from tools import attractions, exchange, food, holidays, lodging, transport, weather

_MODULES = [
    (lodging.legal_stay_check, lodging.SCHEMA),
    (food.find_local_food, food.SCHEMA),
    (attractions.find_attractions, attractions.SCHEMA),
    (exchange.twd_exchange, exchange.SCHEMA),
    (transport.hsr_trip_planner, transport.SCHEMA),
    (holidays.crowd_risk_check, holidays.SCHEMA),
    (weather.typhoon_backup_plan, weather.SCHEMA),
]

# What the model sees: the "set notes" in the screenplay.
TOOLS = [schema for _, schema in _MODULES]

# What the harness runs: tool name -> Python function.
TOOL_MAP = {schema["function"]["name"]: fn for fn, schema in _MODULES}


def run_tool(name: str, args: dict) -> str:
    """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
    if name not in TOOL_MAP:
        return json.dumps({"error": f"Unknown tool '{name}'. Available: {list(TOOL_MAP)}"})
    try:
        return TOOL_MAP[name](**args)
    except (TypeError, ValueError) as e:
        return json.dumps({"error": f"Bad arguments for {name}: {e}", "hint": "Fix the arguments and retry."})
    except Exception as e:  # A bug in one tool should not take down the chat.
        return json.dumps({"error": f"{name} failed unexpectedly: {type(e).__name__}",
                           "hint": "Tell the user this lookup failed; do not invent the data."})
