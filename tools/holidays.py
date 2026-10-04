"""Estimate holiday travel pressure from Taiwan's official office calendar."""

import csv
import datetime as dt
import io
import json
import re
import time
from pathlib import Path

import requests

from tools.gov_tls import gov_session

SOURCE = "https://data.gov.tw/dataset/14718"
CALENDAR_URLS = {
    2026: "https://www.dgpa.gov.tw/uploads/dgpa/files/202506/a52331bd-a189-466b-b0f0-cae3062bbf74.csv",
    2027: "https://www.dgpa.gov.tw/uploads/dgpa/files/202607/f538b1ff-ba60-4c63-9477-10db8e6612d1.csv",
}
CACHE_TTL = 24 * 60 * 60
# DGPA's certificate fails Python 3.13+'s strict profile; see tools/gov_tls.py.
_http = gov_session("https://www.dgpa.gov.tw")
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_cache: dict[int, tuple[float, dict[dt.date, dict]]] = {}
CALIBRATION = json.loads((Path(__file__).parent / "data" / "crowd_calibration.json").read_text(encoding="utf-8"))


def _error(message: str, hint: str) -> str:
    return json.dumps({"error": message, "hint": hint})


def _parse_calendar(content: bytes, year: int) -> dict[dt.date, dict]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if not {"西元日期", "是否放假", "備註"}.issubset(reader.fieldnames or []):
        raise ValueError("missing calendar columns")

    days = {}
    for row in reader:
        day = dt.datetime.strptime(row["西元日期"], "%Y%m%d").date()
        flag = row["是否放假"]
        if day.year != year or day in days or flag not in {"0", "2"}:
            raise ValueError("invalid calendar row")
        days[day] = {"is_holiday": flag == "2", "note": (row["備註"] or "").strip()}

    expected_days = (dt.date(year + 1, 1, 1) - dt.date(year, 1, 1)).days
    if len(days) != expected_days:
        raise ValueError("incomplete calendar")
    return days


def _load_year(year: int) -> dict[dt.date, dict] | None:
    url = CALENDAR_URLS.get(year)
    if url is None:
        return None
    cached = _cache.get(year)
    if cached and time.monotonic() - cached[0] < CACHE_TTL:
        return cached[1]
    try:
        response = _http.get(url, timeout=10)
        response.raise_for_status()
        days = _parse_calendar(response.content, year)
    except (requests.RequestException, csv.Error, UnicodeError, ValueError, TypeError) as exc:
        raise RuntimeError(f"Could not load the {year} government calendar") from exc
    _cache[year] = (time.monotonic(), days)
    return days


def _record(day: dt.date) -> dict | None:
    calendar = _load_year(day.year)
    return calendar.get(day) if calendar is not None else None


def _break_around(day: dt.date, record=None) -> tuple[dt.date, dt.date, bool]:
    """Return the full known run of days off and whether it hits an unpublished year."""
    record = record or _record
    first = last = day
    unknown = False
    while first > dt.date.min:
        previous = first - dt.timedelta(days=1)
        row = record(previous)
        if row is None:
            unknown = True
            break
        if not row["is_holiday"]:
            break
        first = previous
    while last < dt.date.max:
        following = last + dt.timedelta(days=1)
        row = record(following)
        if row is None:
            unknown = True
            break
        if not row["is_holiday"]:
            break
        last = following
    return first, last, unknown


def _day_type(day: dt.date, row: dict, record=None) -> str:
    """Classify a calendar day without using passenger counts."""
    record = record or _record
    if not row["is_holiday"]:
        if day.weekday() >= 5:
            return "working_weekend"
        tomorrow = day + dt.timedelta(days=1)
        next_row = record(tomorrow)
        if next_row and next_row["is_holiday"]:
            first, last, unknown = _break_around(tomorrow, record)
            if not unknown and (last - first).days >= 2:
                return "long_break_eve"
        return "workday"

    first, last, unknown = _break_around(day, record)
    if unknown:
        return "uncertain_break"
    if (last - first).days >= 2:
        if day == first:
            return "long_break_first"
        if day == last:
            return "long_break_last"
        return "long_break_middle"
    if row["note"]:
        return "single_holiday"
    return "weekend"


REASONS = {
    "workday": "Scheduled working day; no holiday travel signal.",
    "working_weekend": "Scheduled working weekend.",
    "long_break_eve": "Working day before a long break.",
    "long_break_first": "First day of a long break; individual trains may differ from the network average.",
    "long_break_middle": "Middle day of a long break; demand may vary by destination and train.",
    "long_break_last": "Last day of a long break; individual trains may differ from the network average.",
    "single_holiday": "Official day off; demand may vary by destination and train.",
    "weekend": "Regular weekend day; demand may vary by destination and train.",
    "uncertain_break": "Day off; an adjacent year's calendar is unavailable, so break length is uncertain.",
}


def _risk(day_type: str) -> tuple[str, str, dict | None]:
    stats = CALIBRATION["categories"].get(day_type)
    if day_type in {"workday", "working_weekend"}:
        level = "low"
    elif day_type == "weekend":
        level = "medium"
    elif (stats and stats["sample_days"] >= CALIBRATION["minimum_samples"]
          and stats["median_ratio"] >= CALIBRATION["high_ratio_threshold"]):
        level = "high"
    else:
        level = "medium"
    evidence = ({"tra_median_ratio": stats["median_ratio"], "sample_days": stats["sample_days"]}
                if stats and stats["sample_days"] >= CALIBRATION["minimum_samples"] else None)
    reason = REASONS[day_type]
    if evidence and day_type not in {"workday", "working_weekend", "weekend"}:
        reason += f" Historical TRA entries: {stats['median_ratio']:.2f}x comparable days ({stats['sample_days']} sampled days)."
    return level, reason, evidence


def crowd_risk_check(start_date: str, end_date: str) -> str:
    """Return daily holiday facts and an explainable travel-pressure estimate."""
    try:
        if not DATE_PATTERN.fullmatch(start_date) or not DATE_PATTERN.fullmatch(end_date):
            raise ValueError
        start, end = dt.date.fromisoformat(start_date), dt.date.fromisoformat(end_date)
    except (TypeError, ValueError):
        return _error("Dates must use YYYY-MM-DD.", "Ask for valid start_date and end_date values.")
    if end < start:
        return _error("end_date is before start_date.", "Ask for a date range in chronological order.")
    if (end - start).days >= 30:
        return _error("Date range exceeds 30 days.", "Ask for a range of at most 30 calendar days.")
    missing = [year for year in range(start.year, end.year + 1) if year not in CALENDAR_URLS]
    if missing:
        return _error(
            f"The official calendar is unavailable for {', '.join(map(str, missing))}.",
            "Ask for a year with a published calendar; do not guess holiday dates.",
        )

    try:
        days = []
        for offset in range((end - start).days + 1):
            day = start + dt.timedelta(days=offset)
            row = _record(day)
            day_type = _day_type(day, row)
            risk, reason, evidence = _risk(day_type)
            days.append({
                "date": day.isoformat(),
                "weekday": day.strftime("%a"),
                "is_holiday": row["is_holiday"],
                "holiday_name": row["note"] if row["is_holiday"] and row["note"] else None,
                "calendar_note": row["note"] or None,
                "calendar_pattern": day_type,
                "risk": risk,
                "reason": reason,
                "historical_tra_evidence": evidence,
            })
    except RuntimeError:
        return _error(
            "The government holiday calendar could not be loaded.",
            "Tell the user the crowd-risk lookup is unavailable; do not invent holiday dates.",
        )
    return json.dumps({
        "start_date": start_date,
        "end_date": end_date,
        "days": days,
        "risk_basis": "Preliminary calendar and TRA station-entry estimate; not HSR demand, train occupancy, or seat availability.",
        "historical_source": {
            "url": CALIBRATION["ridership_source"],
            "period_start": CALIBRATION["period_start"],
            "period_end": CALIBRATION["period_end"],
            "method": CALIBRATION["method"],
        },
        "source": SOURCE,
    }, ensure_ascii=False)
