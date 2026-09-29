"""Rebuild the compact crowd calibration from official calendar and TRA files.

Run from the repository root with `uv run python scripts/calibrate_crowd_risk.py`.
Pass --calendar-file and --ridership-file to reuse previously downloaded files.
"""

import argparse
import collections
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import holidays  # noqa: E402

RIDERSHIP_URL = "https://ods.railway.gov.tw/tra-ods-web/ods/download/dataResource/8ae4cabf6973990e0169947ed32454b9"
PERIOD_YEAR = 2026


def _read(path: Path | None, url: str) -> bytes:
    if path:
        return path.read_bytes()
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.content


def build_calibration(calendar_bytes: bytes, ridership_bytes: bytes) -> dict:
    calendar = holidays._parse_calendar(calendar_bytes, PERIOD_YEAR)
    rows = json.loads(ridership_bytes)
    if not isinstance(rows, list):
        raise ValueError("TRA data must be a list")

    entries = collections.Counter()
    for row in rows:
        day = dt.datetime.strptime(row["trnOpDate"], "%Y%m%d").date()
        if day.year != PERIOD_YEAR:
            raise ValueError("Unexpected ridership year")
        count = int(row["gateInComingCnt"])
        if count < 0:
            raise ValueError("Negative station entries")
        entries[day] += count
    if len(entries) < 180 or min(entries) != dt.date(PERIOD_YEAR, 1, 1):
        raise ValueError("TRA sample is too short")
    if len(entries) != (max(entries) - min(entries)).days + 1:
        raise ValueError("TRA sample has missing dates")

    record = calendar.get
    patterns = {day: holidays._day_type(day, record(day), record) for day in entries}
    normal = {day: value for day, value in entries.items() if patterns[day] in {"workday", "weekend"}}
    ratios = collections.defaultdict(list)
    for day, value in entries.items():
        peers = [count for peer, count in normal.items()
                 if peer.weekday() == day.weekday() and abs((peer - day).days) <= 56]
        if len(peers) >= 4:
            ratios[patterns[day]].append(value / statistics.median(peers))

    return {
        "period_start": min(entries).isoformat(),
        "period_end": max(entries).isoformat(),
        "calendar_source": holidays.SOURCE,
        "ridership_source": "https://data.gov.tw/dataset/8792",
        "ridership_download": RIDERSHIP_URL,
        "method": "Median daily TRA station entries versus ordinary days of the same weekday within 56 days; network-wide, not route-specific.",
        "minimum_samples": 5,
        "high_ratio_threshold": 1.2,
        "categories": {
            pattern: {"sample_days": len(values), "median_ratio": round(statistics.median(values), 3)}
            for pattern, values in sorted(ratios.items())
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calendar-file", type=Path)
    parser.add_argument("--ridership-file", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "tools/data/crowd_calibration.json")
    args = parser.parse_args()
    calendar_bytes = _read(args.calendar_file, holidays.CALENDAR_URLS[PERIOD_YEAR])
    ridership_bytes = _read(args.ridership_file, RIDERSHIP_URL)
    result = build_calibration(calendar_bytes, ridership_bytes)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} using {result['period_start']} to {result['period_end']}")


if __name__ == "__main__":
    main()
