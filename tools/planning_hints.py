"""Small, conditional planning suggestions; never execute tools or change preferences."""

import datetime as dt
import json
import re

from trip_context import taiwan_today
from tools.tdx_client import resolve_city
from tools.weather_planning import window

TRIP_PLAN = re.compile(
    r"\b(?:plan|organize|arrange)\b.{0,80}\b(?:trip|weekend|day|outing|holiday|visit)\b"
    r"|\b(?:trip|weekend|day|outing|holiday)\b.{0,50}\b(?:plan|itinerary)\b"
    r"|\bitinerary\b|\bstay\b.{0,50}\b(?:explore|sightseeing)\b"
    r"|(?:規劃|安排).{0,30}(?:旅行|旅遊|週末|周末|行程)|行程規劃", re.I | re.S)
OVERNIGHT = re.compile(r"\b(?:weekend|overnight)\b|\b\d+\s*(?:days|nights)\b|週末|周末|過夜", re.I)


def _object(result):
    try:
        value = json.loads(result)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _city(value):
    resolved = resolve_city(value) if isinstance(value, str) else None
    return resolved[0] if resolved else value


def _preference(state, key):
    pref = state.trip.preferences.get(key)
    return pref.value if pref and pref.status == "specified" else None


def add_next_steps(name, args, result, state):
    """Augment successful SDK results with suggestions relevant to the current request."""
    data = _object(result)
    if not data or data.get("error") or name not in (
        "legal_stay_check", "find_attractions", "find_local_food", "typhoon_backup_plan"
    ):
        return result
    message = state.user_texts[-1] if state.user_texts else ""
    broad_plan = bool(TRIP_PLAN.search(message))
    city = args.get("city")
    calls = [call for call in state.tool_calls[state.turn_start:]
             if _city(call["args"].get("city")) == _city(city)]
    attempted = {call["name"] for call in calls} | {name}
    steps = []
    if name == "typhoon_backup_plan" and (data.get("comparison") or {}).get("strategy") == "postpone_outing":
        return result

    # A duration alone never establishes today's date. Only a known single-day outing is used.
    date = _preference(state, "start_date")
    end_date = _preference(state, "end_date")
    if name == "find_attractions" and (args.get("available_minutes") or broad_plan) and date and end_date in (None, date):
        today = taiwan_today()
        try:
            day = dt.date.fromisoformat(date)
            start, end = window(day, _preference(state, "outing_start_time"),
                                _preference(state, "outing_end_time"), args.get("available_minutes"))
        except ValueError:
            day = None
        if day and 0 <= (day - today).days <= 7:
            expected = (start.strftime("%H:%M"), end.strftime("%H:%M"))
            checked = False
            for call in state.tool_calls:
                if call["name"] != "typhoon_backup_plan":
                    continue
                weather = _object(call["result"])
                weather_args = call["args"]
                if _city(weather_args.get("city")) != _city(city):
                    continue
                weather_date = weather.get("date") or weather_args.get("date") or today.isoformat()
                if weather_date != date:
                    continue
                if call in calls and (weather.get("error") or call.get("rejected")):
                    checked = True
                    break
                if call.get("rejected"):
                    continue
                outing = (weather.get("comparison") or {}).get("outing_window", {})
                if not weather.get("error") and (outing.get("start"), outing.get("end")) == expected:
                    checked = True
                    break
            if not checked:
                weather_args = {"city": city, "date": date}
                for key in ("start_time", "end_time"):
                    if value := _preference(state, "outing_" + key):
                        weather_args[key] = value
                if args.get("available_minutes") is not None:
                    weather_args["available_minutes"] = args["available_minutes"]
                steps.append({"tool": "typhoon_backup_plan", "suggested_args": weather_args,
                              "reason": "This dated outing is within the forecast window. Check weather before finalizing outdoor/indoor choices."})

    if broad_plan:
        needs = [("find_attractions", "Add sightseeing to the trip outline using the base or nearby districts."),
                 ("find_local_food", "Add meals around the selected base or sightseeing stops; preserve dietary needs and use budget ranking for a cheap trip.")]
        if OVERNIGHT.search(message):
            needs.append(("legal_stay_check", "Add a registered stay if accommodation is part of this overnight plan."))
        for tool, reason in needs:
            if tool not in attempted:
                steps.append({"tool": tool, "reason": reason})
    if not steps:
        return result
    data["next_steps"] = steps
    return json.dumps(data, ensure_ascii=False)
