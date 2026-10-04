"""CWA forecasts and current warnings, with weather comparisons for an outing window."""

import datetime as dt
import json
import os
import time
from zoneinfo import ZoneInfo

import requests

from tools import weather_planning
from tools.gov_tls import gov_session
from tools.tdx_client import city_choices, resolve_city

CWA_BASE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/"
WEEK_FORECAST = "F-D0047-091"  # county forecast, 12-hour periods for about 7 days
TYPHOON_WARNING = "W-C0034-001"  # latest typhoon warning bulletin, even after it is lifted
TIMEOUT = 15
CACHE_TTL = 30 * 60  # forecasts update a few times a day
BAD_RAIN_CHANCE = 70
TYPHOON_MONTHS = range(7, 11)  # July-October
TAIPEI = ZoneInfo("Asia/Taipei")

_cache: dict[tuple, tuple[float, dict]] = {}


# CWA's certificate fails Python 3.13+'s strict profile; see tools/gov_tls.py.
_http = gov_session("https://opendata.cwa.gov.tw")


def _cwa_get(dataset: str, params: dict) -> dict:
    """GET a CWA dataset's `records`, or {"error", "hint"}. Never raises; successes are cached."""
    key = (dataset, tuple(sorted(params.items())))
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]
    api_key = os.environ.get("CWA_API_KEY")
    if not api_key:
        return {"error": "CWA credentials are not configured on the server.",
                "hint": "Tell the user weather data is temporarily unavailable. Do not guess the forecast."}
    try:
        resp = _http.get(CWA_BASE + dataset, params={"Authorization": api_key, **params}, timeout=TIMEOUT)
        resp.raise_for_status()
        records = resp.json()["records"]
    except (requests.RequestException, ValueError, KeyError) as e:
        if cached:
            return cached[1]
        return {"error": f"CWA request failed: {type(e).__name__}",
                "hint": "The weather service is unreachable. Tell the user and suggest trying again shortly."}
    _cache[key] = (time.time(), records)
    return records


def _value(element: dict, starts: dt.datetime, ends: dt.datetime, field: str) -> str | None:
    # Elements can have different ordering or missing intervals; align by time, not index.
    for slot in element.get("Time") or []:
        if (weather_planning.timestamp(slot.get("StartTime")) == starts
                and weather_planning.timestamp(slot.get("EndTime")) == ends):
            values = slot.get("ElementValue") or []
            return values[0].get(field) if values else None
    return None


def _as_int(text: str | None) -> int | None:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _forecast(location: dict, day: dt.date) -> dict | None:
    """Periods overlapping the day, including a preceding evening's overnight interval."""
    elements = {e.get("ElementName"): e for e in location.get("WeatherElement") or []}
    times = (elements.get("天氣現象") or {}).get("Time") or []
    midnight = dt.datetime.combine(day, dt.time(), TAIPEI)
    next_day = midnight + dt.timedelta(days=1)
    periods = []
    for slot in times:
        starts = weather_planning.timestamp(slot.get("StartTime"))
        ends = weather_planning.timestamp(slot.get("EndTime"))
        if starts is None or ends is None or ends <= starts or ends <= midnight or starts >= next_day:
            continue
        def value(name, field):
            return _value(elements.get(name, {}), starts, ends, field)
        rain = _as_int(value("12小時降雨機率", "ProbabilityOfPrecipitation"))
        if rain is not None and not 0 <= rain <= 100:
            rain = None
        periods.append({
            "start": starts.strftime("%H:%M"), "end": ends.strftime("%H:%M"),
            "start_at": starts.isoformat(), "end_at": ends.isoformat(),
            "weather": value("天氣現象", "Weather"), "rain_chance": rain,
            "min_temp_c": _as_int(value("最低溫度", "MinTemperature")),
            "max_temp_c": _as_int(value("最高溫度", "MaxTemperature")),
            "description": value("天氣預報綜合描述", "WeatherDescription"),
        })
    if not periods:
        return None
    periods.sort(key=lambda p: p["start_at"])
    rain = [p["rain_chance"] for p in periods if p["rain_chance"] is not None]
    lows = [p["min_temp_c"] for p in periods if p["min_temp_c"] is not None]
    highs = [p["max_temp_c"] for p in periods if p["max_temp_c"] is not None]
    return {
        "date": day.isoformat(),
        "weather": periods[0]["weather"],
        "rain_chance": max(rain) if rain else None,
        "min_temp_c": min(lows) if lows else None,
        "max_temp_c": max(highs) if highs else None,
        "periods": periods,
        "description": periods[0]["description"],
    }


def _typhoon_status(county: str, now: dt.datetime) -> tuple[str | None, bool]:
    """An active typhoon warning covering this county (or with no land areas listed), else None.
    The feed keeps the last bulletin after a warning is lifted, so check urgency, expiry, and headline."""
    records = _cwa_get(TYPHOON_WARNING, {})
    if "error" in records:
        return None, False
    for info in records.get("info") or []:
        headline = info.get("headline") or ""
        expires = weather_planning.timestamp(info.get("expires"))
        expired = expires is not None and expires <= now
        if info.get("urgency") == "Past" or expired or "解除" in headline:
            continue
        areas = [a.get("areaDesc") for a in info.get("area") or []]
        if areas and county not in areas:
            continue
        sections = (info.get("description") or {}).get("section") or []
        where = next((s.get("value") for s in sections if s.get("title") == "命名與位置"), "")
        return f"{headline}: {where}".strip(": "), True
    return None, True


def typhoon_backup_plan(city: str, date: str | None = None, available_minutes: int | None = None,
                       start_time: str | None = None, end_time: str | None = None) -> str:
    place = resolve_city(city)
    if place is None:
        return json.dumps({
            "error": f"Unknown city '{city}'.",
            "hint": "Use a Taiwan city or county name.",
            "valid_cities": city_choices(),
        })
    county = place[0]
    now = dt.datetime.now(TAIPEI)
    try:
        day = dt.date.fromisoformat(date) if date else now.date()
    except ValueError:
        return json.dumps({"error": f"Bad date '{date}'.", "hint": "Use YYYY-MM-DD, e.g. 2026-10-10."})
    if day < now.date():
        return json.dumps({"error": f"{day} is in the past.", "hint": "Ask for today or a future travel date."})
    try:
        start, end = weather_planning.window(day, start_time, end_time, available_minutes)
    except ValueError as exc:
        return json.dumps({"error": str(exc), "hint": "Correct the outing preferences or same-day HH:MM window."})

    records = _cwa_get(WEEK_FORECAST, {"LocationName": county})
    if "error" in records:
        return json.dumps(records, ensure_ascii=False)
    try:
        locations = [location for group in records["Locations"] for location in group["Location"]]
        location = next(location for location in locations if location.get("LocationName") == county)
    except (KeyError, IndexError, TypeError, StopIteration):
        return json.dumps({"error": f"No CWA forecast for {county}.",
                           "hint": "Tell the user the forecast is unavailable; do not guess."}, ensure_ascii=False)

    forecast = _forecast(location, day)
    alert, warning_available = _typhoon_status(county, now)
    warning_applies = alert is not None and day == now.date()
    comparison = weather_planning.compare(forecast, start, end, alert, warning_applies, warning_available)
    window_minutes = int((end - start).total_seconds() // 60)
    comparison["available_minutes"] = min(available_minutes, window_minutes) if available_minutes is not None else (
        window_minutes if start_time is not None and end_time is not None and window_minutes <= 720 else None)
    if forecast is None and not warning_applies:
        season = ("July-October is typhoon season: plan an indoor backup day and check the CWA forecast "
                  "about a week before." if day.month in TYPHOON_MONTHS else
                  "Outside typhoon season (July-October), but afternoon showers are common; pack an umbrella.")
        beyond = day > now.date() + dt.timedelta(days=7)
        return json.dumps({
            "city": county,
            "date": day.isoformat(),
            "forecast": None,
            "typhoon_alert": None,
            "is_bad_weather": None,
            "current_typhoon_alert": alert, "comparison": comparison, "backup_spots": [],
            "seasonal_note": (f"The CWA forecast covers about 7 days, so {day} is too far out. {season}"
                              if beyond else "The requested date has no forecast periods in the returned data; try again later."),
            "source": "Central Weather Administration (CWA)",
        }, ensure_ascii=False)

    rain = comparison["rain_chance"]
    known = bool(comparison["periods"]) and comparison["forecast_covers_window"] and all(
        p["rain_chance"] is not None for p in comparison["periods"])
    bad = True if warning_applies or rain is not None and rain >= BAD_RAIN_CHANCE else False if known else None
    return json.dumps({
        "city": county,
        "date": day.isoformat(),
        "forecast": forecast,
        "typhoon_alert": alert if warning_applies else None, "current_typhoon_alert": alert,
        "is_bad_weather": bad,
        "comparison": comparison, "backup_spots": [],
        "note": "Use the comparison for the outing. Rain percentages cover whole forecast periods, not individual hours."
                if not warning_applies else "An active warning is not an invitation to visit indoor venues; follow CWA and local closure updates.",
        "source": "Central Weather Administration (CWA)",
    }, ensure_ascii=False)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "typhoon_backup_plan",
        "description": (
            "Check the CWA forecast and active typhoon warnings for a Taiwan city on a date within about "
            "a week. Compare forecast periods overlapping the outing; return a practical strategy "
            "for outdoor, flexible, or indoor activities. Returns weather only; use find_attractions "
            "separately when place recommendations would help. A current typhoon warning recommends "
            "postponing sightseeing. Dates further out get a seasonal note instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Taiwan city/county in English, e.g. 'Tainan', 'Taipei', 'Hualien'.",
                },
                "date": {"type": "string", "description": "Travel date, YYYY-MM-DD. Omit for today."},
                "available_minutes": {"type": "integer", "minimum": 15, "maximum": 720,
                                      "description": "Total outing time in minutes, reused on follow-ups."},
                "start_time": {"type": "string", "description": "Outing start in HH:MM Taiwan time; default 08:00."},
                "end_time": {"type": "string", "description": "Same-day end in HH:MM; default 20:00, or start plus available_minutes when a start is stated."},
            },
            "required": ["city"],
        },
    },
}
