"""Practical outing choices from forecast intervals, separate from CWA observations."""

import datetime as dt
import re

TAIPEI = dt.timezone(dt.timedelta(hours=8))


def clock_minutes(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("Outing times must use HH:MM (00:00–23:59)")
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def window(day, start_time, end_time, available_minutes):
    if available_minutes is not None and (
        isinstance(available_minutes, bool) or not isinstance(available_minutes, int)
        or not 15 <= available_minutes <= 720
    ):
        raise ValueError("available_minutes must be an integer from 15 to 720 (total outing time)")
    start = clock_minutes(start_time) if start_time is not None else 8 * 60
    end = (clock_minutes(end_time) if end_time is not None else start + available_minutes
           if start_time is not None and available_minutes is not None else 20 * 60)
    if end <= start or end > 24 * 60:
        raise ValueError("The outing window must end after it starts, within the same day")
    midnight = dt.datetime.combine(day, dt.time(), TAIPEI)
    return midnight + dt.timedelta(minutes=start), midnight + dt.timedelta(minutes=end)


def timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(value)
        return parsed.replace(tzinfo=TAIPEI) if parsed.tzinfo is None else parsed.astimezone(TAIPEI)
    except (ValueError, TypeError):
        return None


def compare(forecast, start, end, current_alert, warning_applies, warning_available):
    periods = []
    for period in forecast["periods"] if forecast else []:
        begins, ends = timestamp(period["start_at"]), timestamp(period["end_at"])
        if begins is None or ends is None or ends <= start or begins >= end:
            continue
        rain = period["rain_chance"]
        periods.append(dict(period, outing_start=max(begins, start).strftime("%H:%M"),
                            outing_end=min(ends, end).strftime("%H:%M"),
                            activity_preference="indoor" if rain is not None and rain >= 70 else
                            "flexible" if rain is None or rain >= 40 else "outdoor"))
    known = [p for p in periods if p["rain_chance"] is not None]
    wet = [p for p in periods if p["activity_preference"] == "indoor"]
    dry = [p for p in known if p["activity_preference"] == "outdoor"]
    if warning_applies:
        strategy, reason = "postpone_outing", "An active typhoon warning covers this city now; postpone sightseeing and follow official updates."
    elif wet and len(wet) == len(periods):
        strategy, reason = "indoor", "Rain chances are high in the forecast periods overlapping your outing; choose an indoor plan."
    elif wet:
        strategy = "split"
        reason = ("Conditions differ across your outing; use lower-rain periods outdoors and high-rain periods indoors."
                  if dry else "Choose indoor activities during high-rain periods and keep the rest of the outing flexible.")
    elif any(p["activity_preference"] == "flexible" for p in periods):
        strategy, reason = "flexible", "Keep the outing flexible, with an indoor alternative if rain develops."
    elif periods:
        strategy, reason = "outdoor", "Reported rain chances are lower during your outing; outdoor sightseeing is a reasonable choice."
    else:
        strategy, reason = "forecast_unavailable", "No forecast period covers this outing yet; keep an indoor option and check closer to the date."
    # Missing periods and rain probabilities must not become an all-clear.
    coverage = []
    for p in periods:
        coverage.append((max(start, timestamp(p["start_at"])), min(end, timestamp(p["end_at"]))))
    cursor = start
    for begins, ends in sorted(coverage):
        if begins > cursor:
            break
        cursor = max(cursor, ends)
    complete = cursor >= end
    if strategy == "outdoor" and not complete:
        strategy, reason = "flexible", "The available periods have lower rain chances, but only cover part of your outing."
    best = min(dry or known, key=lambda p: p["rain_chance"], default=None)
    return {"strategy": strategy, "reason": reason,
            "outing_window": {"start": start.strftime("%H:%M"), "end": end.strftime("%H:%M"),
                              "timezone": "Asia/Taipei"},
            "periods": periods, "forecast_covers_window": complete,
            "lowest_rain_period": best,
            "rain_chance": max((p["rain_chance"] for p in known), default=None),
            "warning_applies_to_outing": warning_applies,
            "warning_status": "active_now" if current_alert else "none_reported" if warning_available else "unavailable",
            "warning_note": "This warning is current; it does not predict conditions on your future travel date."
                            if current_alert and not warning_applies else None,
            "basis": "Planning heuristics using CWA forecast periods, not an hourly forecast or venue-opening check"}
