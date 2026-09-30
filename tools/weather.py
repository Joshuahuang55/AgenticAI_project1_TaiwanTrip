"""typhoon_backup_plan (member B): CWA forecast and typhoon warnings, with indoor backups from TDX."""

import datetime as dt
import json
import os
import ssl
import time
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter

from tools import attractions
from tools.tdx_client import city_choices, resolve_city

CWA_BASE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/"
WEEK_FORECAST = "F-D0047-091"  # county forecast, 12-hour periods for about 7 days
TYPHOON_WARNING = "W-C0034-001"  # latest typhoon warning bulletin, even after it is lifted
TIMEOUT = 15
CACHE_TTL = 30 * 60  # forecasts update a few times a day
BAD_RAIN_CHANCE = 70
TYPHOON_MONTHS = range(7, 11)  # July-October
TAIPEI = ZoneInfo("Asia/Taipei")
# Indoor places for a rainy day, searched in listing names and descriptions.
INDOOR_WORDS = (*attractions.KEYWORDS["museum"], "展示館", "文化館", "觀光工廠", "水族館")

_cache: dict[tuple, tuple[float, dict]] = {}


class _CwaTLS(HTTPAdapter):
    """CWA's certificate lacks a Subject Key Identifier, which Python 3.13+ rejects under
    VERIFY_X509_STRICT. Keep full chain and hostname checks; drop only that strict profile."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


_http = requests.Session()
_http.mount("https://opendata.cwa.gov.tw", _CwaTLS())


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


def _value(element: dict, period: int, field: str) -> str | None:
    try:
        return element["Time"][period]["ElementValue"][0][field]
    except (IndexError, KeyError, TypeError):
        return None


def _as_int(text: str | None) -> int | None:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _forecast(location: dict, day: dt.date) -> dict | None:
    """The 12-hour periods starting on `day`, summarized: worst rain chance, temp range, weather."""
    elements = {e.get("ElementName"): e for e in location.get("WeatherElement") or []}
    times = (elements.get("天氣現象") or {}).get("Time") or []
    idx = [i for i, t in enumerate(times) if (t.get("StartTime") or "")[:10] == day.isoformat()]
    if not idx:
        return None
    periods = []
    for i in idx:
        periods.append({
            "start": times[i]["StartTime"][11:16],
            "end": (times[i].get("EndTime") or "")[11:16],
            "weather": _value(elements["天氣現象"], i, "Weather"),
            "rain_chance": _as_int(_value(elements.get("12小時降雨機率", {}), i, "ProbabilityOfPrecipitation")),
            "min_temp_c": _as_int(_value(elements.get("最低溫度", {}), i, "MinTemperature")),
            "max_temp_c": _as_int(_value(elements.get("最高溫度", {}), i, "MaxTemperature")),
        })
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
        "description": _value(elements.get("天氣預報綜合描述", {}), idx[0], "WeatherDescription"),
    }


def _typhoon_alert(county: str, now: dt.datetime) -> str | None:
    """An active typhoon warning covering this county (or with no land areas listed), else None.
    The feed keeps the last bulletin after a warning is lifted, so check urgency, expiry, and headline."""
    records = _cwa_get(TYPHOON_WARNING, {})
    if "error" in records:
        return None
    for info in records.get("info") or []:
        headline = info.get("headline") or ""
        try:
            expired = dt.datetime.fromisoformat(info.get("expires") or "") <= now
        except ValueError:
            expired = False
        if info.get("urgency") == "Past" or expired or "解除" in headline:
            continue
        areas = [a.get("areaDesc") for a in info.get("area") or []]
        if areas and county not in areas:
            continue
        sections = (info.get("description") or {}).get("section") or []
        where = next((s.get("value") for s in sections if s.get("title") == "命名與位置"), "")
        return f"{headline}: {where}".strip(": ")
    return None


def _indoor_spots(county: str, limit: int = 3) -> list[dict]:
    """Up to `limit` indoor places with coordinates, so the frontend pins them as backups."""
    match = " or ".join(
        f"contains(AttractionName,'{w}') or contains(Description,'{w}')" for w in INDOOR_WORDS
    )
    rows = attractions.tdx_get(attractions.ATTRACTION_PATH, {
        "$filter": f"PostalAddress/City eq '{county}' and ({match})", "$top": attractions.RANK_POOL})
    if isinstance(rows, dict):
        return []
    rows = [r for r in rows if r.get("ServiceStatus") not in attractions.CLOSED_STATUS]
    named = [r for r in attractions._dedupe(attractions._rank(rows, INDOOR_WORDS))
             if any(w in (r.get("AttractionName") or "") for w in INDOOR_WORDS)]
    return [attractions._summarize(r, {}, {}) for r in attractions._spread(named, limit)]


def typhoon_backup_plan(city: str, date: str | None = None) -> str:
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

    records = _cwa_get(WEEK_FORECAST, {"LocationName": county})
    if "error" in records:
        return json.dumps(records, ensure_ascii=False)
    try:
        location = records["Locations"][0]["Location"][0]
    except (KeyError, IndexError, TypeError):
        return json.dumps({"error": f"No CWA forecast for {county}.",
                           "hint": "Tell the user the forecast is unavailable; do not guess."}, ensure_ascii=False)

    forecast = _forecast(location, day)
    if forecast is None:
        season = ("July-October is typhoon season: plan an indoor backup day and check the CWA forecast "
                  "about a week before." if day.month in TYPHOON_MONTHS else
                  "Outside typhoon season (July-October), but afternoon showers are common; pack an umbrella.")
        return json.dumps({
            "city": county,
            "date": day.isoformat(),
            "forecast": None,
            "typhoon_alert": None,
            "is_bad_weather": None,
            "seasonal_note": f"The CWA forecast covers about 7 days, so {day} is too far out. {season}",
            "source": "Central Weather Administration (CWA)",
        }, ensure_ascii=False)

    alert = _typhoon_alert(county, now)
    bad = alert is not None or (forecast["rain_chance"] or 0) >= BAD_RAIN_CHANCE
    return json.dumps({
        "city": county,
        "date": day.isoformat(),
        "forecast": forecast,
        "typhoon_alert": alert,
        "is_bad_weather": bad,
        "backup_spots": _indoor_spots(county) if bad else [],
        "note": "Weather text is in Chinese: translate it. Bad weather means a typhoon warning or at least "
                f"{BAD_RAIN_CHANCE}% rain chance; suggest the indoor backup_spots, which are pinned on the map.",
        "source": "Central Weather Administration (CWA)" + (", backups from TDX" if bad else ""),
    }, ensure_ascii=False)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "typhoon_backup_plan",
        "description": (
            "Check the CWA forecast and active typhoon warnings for a Taiwan city on a date within about "
            "a week. Returns weather, rain chance, temperatures, typhoon_alert, and is_bad_weather "
            "(typhoon warning or rain chance of 70% or more); when bad, up to 3 indoor backup_spots. "
            "Dates further out get a seasonal note instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Taiwan city/county in English, e.g. 'Tainan', 'Taipei', 'Hualien'.",
                },
                "date": {"type": "string", "description": "Travel date, YYYY-MM-DD. Omit for today."},
            },
            "required": ["city"],
        },
    },
}
