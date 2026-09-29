"""Holiday travel-pressure rules with official CSV downloads mocked."""

import csv
import datetime as dt
import io
import json

import pytest
import requests

from tools import holidays, run_tool
from scripts.calibrate_crowd_risk import build_calibration


def _calendar_csv(year, changes=None):
    changes = changes or {}
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["西元日期", "星期", "是否放假", "備註"])
    day = dt.date(year, 1, 1)
    while day.year == year:
        flag, note = changes.get(day.isoformat(), ("2" if day.weekday() >= 5 else "0", ""))
        writer.writerow([day.strftime("%Y%m%d"), day.strftime("%a"), flag, note])
        day += dt.timedelta(days=1)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


@pytest.fixture
def fake_calendars(monkeypatch):
    holidays._cache.clear()
    files = {
        2026: _calendar_csv(2026, {"2026-10-09": ("2", "國慶日補假"),
                                  "2026-10-17": ("0", "補行上班")}),
        2027: _calendar_csv(2027, {"2027-01-01": ("2", "開國紀念日"),
                                  "2027-12-31": ("2", "補假")}),
    }
    calls = []

    class Response:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    def get(url, timeout):
        assert timeout == 10
        year = next(year for year, source in holidays.CALENDAR_URLS.items() if source == url)
        calls.append(year)
        return Response(files[year])

    monkeypatch.setattr(holidays.requests, "get", get)
    yield files, calls
    holidays._cache.clear()


def test_tra_calibration_highlights_pre_break_eve(fake_calendars):
    _, calls = fake_calendars
    result = json.loads(run_tool("crowd_risk_check", {"start_date": "2026-10-08", "end_date": "2026-10-12"}))
    days = {day["date"]: day for day in result["days"]}

    assert [days[f"2026-10-{day:02d}"]["risk"] for day in range(8, 13)] == [
        "high", "medium", "medium", "medium", "low",
    ]
    assert days["2026-10-09"]["holiday_name"] == "國慶日補假"
    assert days["2026-10-08"]["historical_tra_evidence"] == {
        "tra_median_ratio": 1.266, "sample_days": 5,
    }
    assert "0.99x" in days["2026-10-09"]["reason"]
    assert "not HSR demand" in result["risk_basis"]
    assert calls == [2026]


def test_weekend_and_working_saturday(fake_calendars):
    weekend = json.loads(holidays.crowd_risk_check("2026-10-03", "2026-10-04"))
    assert [day["risk"] for day in weekend["days"]] == ["medium", "medium"]

    workday = json.loads(holidays.crowd_risk_check("2026-10-17", "2026-10-17"))["days"][0]
    assert workday["is_holiday"] is False
    assert workday["risk"] == "low"
    assert workday["calendar_note"] == "補行上班"


def test_break_crosses_year_and_reuses_downloads(fake_calendars):
    _, calls = fake_calendars
    result = json.loads(holidays.crowd_risk_check("2026-12-31", "2027-01-03"))
    assert [day["risk"] for day in result["days"]] == ["high", "medium", "medium", "medium"]
    assert calls == [2026, 2027]

    holidays.crowd_risk_check("2027-01-01", "2027-01-01")
    assert calls == [2026, 2027]


def test_unpublished_next_year_keeps_estimate_uncertain(fake_calendars):
    day = json.loads(holidays.crowd_risk_check("2027-12-31", "2027-12-31"))["days"][0]
    assert day["risk"] == "medium"
    assert "unavailable" in day["reason"]


def test_risk_responds_to_calibration_data(fake_calendars, monkeypatch):
    baseline = json.loads(holidays.crowd_risk_check("2026-10-08", "2026-10-08"))["days"][0]
    assert baseline["risk"] == "high"
    changed = {**holidays.CALIBRATION, "categories": {
        **holidays.CALIBRATION["categories"],
        "long_break_eve": {"sample_days": 5, "median_ratio": 1.05},
    }}
    monkeypatch.setattr(holidays, "CALIBRATION", changed)
    revised = json.loads(holidays.crowd_risk_check("2026-10-08", "2026-10-08"))["days"][0]
    assert revised["risk"] == "medium"


def test_calibration_compares_entries_to_nearby_matching_weekdays():
    calendar = _calendar_csv(2026, {"2026-02-27": ("2", "補假")})
    rows = []
    day = dt.date(2026, 1, 1)
    for _ in range(180):
        rows.append({"trnOpDate": day.strftime("%Y%m%d"),
                     "gateInComingCnt": "130" if day == dt.date(2026, 2, 26) else "100"})
        day += dt.timedelta(days=1)
    calibration = build_calibration(calendar, json.dumps(rows).encode())
    assert calibration["categories"]["long_break_eve"] == {
        "sample_days": 1, "median_ratio": 1.3,
    }


@pytest.mark.parametrize("start,end", [
    ("2026-2-01", "2026-02-02"),
    ("2026-02-30", "2026-03-01"),
    ("2026-10-02", "2026-10-01"),
    ("2026-10-01", "2026-10-31"),
    ("2028-01-01", "2028-01-03"),
])
def test_invalid_ranges_do_not_fetch(fake_calendars, start, end):
    _, calls = fake_calendars
    result = json.loads(holidays.crowd_risk_check(start, end))
    assert "error" in result and "hint" in result
    assert calls == []


def test_download_and_format_failures_return_hints(fake_calendars, monkeypatch):
    files, _ = fake_calendars
    files[2026] = b"wrong,headers\n"
    result = json.loads(holidays.crowd_risk_check("2026-10-09", "2026-10-09"))
    assert "error" in result and "hint" in result

    def fail(*args, **kwargs):
        raise requests.Timeout("service unavailable")

    monkeypatch.setattr(holidays.requests, "get", fail)
    result = json.loads(holidays.crowd_risk_check("2026-10-09", "2026-10-09"))
    assert "error" in result and "hint" in result
