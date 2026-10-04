"""Session-local SDK planning facts and diagnostic counters; no extra model or HTTP calls."""

import json
import logging
import time
from collections import Counter

from agents import RunHooks

import guardrails
from tools.planning_hints import TRIP_PLAN
from planning_context import city_key, object_value
from agent_reply import TravelReply, resolve_reply

log = logging.getLogger("uvicorn.error")


class PlanningHooks(RunHooks):
    def begin(self, state):
        state.reply = None
        state.turn_metrics = {"started": time.monotonic(), "model_calls": 0, "finished": False}

    async def on_agent_start(self, context, agent):
        if not context.context.turn_metrics or context.context.turn_metrics.get("finished"):
            self.begin(context.context)

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        context.context.turn_metrics["model_calls"] += 1

    async def on_tool_end(self, context, agent, tool, result):
        state = context.context
        recorded = next((call for call in reversed(state.tool_calls[state.turn_start:])
                         if call.get("id") == context.tool_call_id), None)
        if recorded and not recorded.get("rejected"):
            state.planning.remember(tool.name, recorded["args"], result)

    async def on_agent_end(self, context, agent, output):
        state = context.context
        message = state.user_texts[-1] if state.user_texts else ""
        pref = state.trip.preferences.get("city")
        city = pref.value if pref and pref.status == "specified" else None
        if isinstance(output, TravelReply):
            cleaned = output.model_copy(update={"message": guardrails.redact_links(output.message, state.sources())})
            state.reply = resolve_reply(cleaned, state.planning)
            if state.reply.message and (state.reply.places or state.turn_calls() or TRIP_PLAN.search(message)):
                state.planning.remember_proposal(state.reply.message, city, bool(TRIP_PLAN.search(message)),
                                                 places=state.reply.places, sources=state.reply.sources)
        # Runner still applies output guardrails after this callback. The app finalizes diagnostics.

    def finish(self, state, status):
        metrics = state.turn_metrics
        if not metrics or metrics.get("finished"):
            return
        metrics["finished"] = True
        calls = state.tool_calls[state.turn_start:]
        counts = dict(Counter(call["name"] for call in calls))
        signatures = Counter(json.dumps([call["name"], call["args"]], sort_keys=True) for call in calls)
        recommendations = dict(Counter(place["kind"] for place in state.reply.places
                                       if place["role"] == "recommended")) if state.reply else {}
        flags = []
        message = state.user_texts[-1] if state.user_texts else ""
        if status == "completed" and TRIP_PLAN.search(message):
            pref = state.trip.preferences.get("city")
            evidence = state.planning.as_dict(pref.value if pref and pref.status == "specified" else None)["tool_evidence"]
            available = {record["tool"] for record in evidence if record.get("candidates")}
            for name, flag in (("find_attractions", "trip_without_sight_results"),
                               ("find_local_food", "trip_without_food_results")):
                if name not in available:
                    flags.append(flag)
        if any(count > 1 for count in signatures.values()):
            flags.append("repeated_identical_lookup")
        for call in calls:
            if call["name"] == "find_local_food" and not call["args"].get("district"):
                city = city_key(call["args"].get("city"))
                if any(record["tool"] in ("legal_stay_check", "find_attractions") and
                       any(item.get("district") for item in record.get("candidates", []))
                       for record in state.planning.as_dict(city)["tool_evidence"]):
                    flags.append("food_search_without_planned_area")
                    break
        entry = {"status": status, "tool_counts": counts, "recommendation_counts": recommendations,
                 "model_calls": metrics["model_calls"],
                 "elapsed_ms": round((time.monotonic() - metrics["started"]) * 1000), "flags": flags,
                 "failed_lookups": sum(bool(call.get("rejected") or object_value(call["result"]).get("error")) for call in calls)}
        state.diagnostics.append(entry)
        del state.diagnostics[:-20]
        log.info("Agent planning diagnostics: %s", json.dumps(entry))
