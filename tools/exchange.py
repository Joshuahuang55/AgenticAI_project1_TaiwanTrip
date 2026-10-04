"""twd_exchange (member C): convert to/from New Taiwan Dollars and compare weekly samples over four weeks.

Data: fawazahmed0/exchange-api, a free daily snapshot of market rates with dated history
and no API key. (The Bank of Taiwan CSV sits behind a bot challenge servers cannot pass.)
"""

import datetime as dt
import json
import math
from concurrent.futures import ThreadPoolExecutor

import requests

TIMEOUT = 10
MIRRORS = [
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{date}/v1/currencies/{cur}.json",
    "https://{date}.currency-api.pages.dev/v1/currencies/{cur}.json",
]
HISTORY_DAYS = [7, 14, 21, 28]  # Weekly samples for the 30-day comparison: 5 requests, not 30.

# Past days never change, so they are cached forever; "latest" is cached per calendar day.
_cache: dict[tuple[str, str], dict] = {}


def _fetch(cur: str, date: str) -> dict | None:
    """Return {date, rates} for 1 unit of `cur`, or None if every mirror fails."""
    key = (cur, date if date != "latest" else f"latest:{dt.date.today()}")
    if key in _cache:
        return _cache[key]
    for url in MIRRORS:
        try:
            resp = requests.get(url.format(date=date, cur=cur), timeout=TIMEOUT)
            if resp.status_code == 200:
                body = resp.json()
                _cache[key] = {"date": body["date"], "rates": body[cur]}
                return _cache[key]
        except (requests.RequestException, ValueError, KeyError):
            continue
    return None


def twd_exchange(amount: float, currency: str = "USD", direction: str = "to_twd") -> str:
    cur = currency.strip().lower()
    if direction not in ("to_twd", "from_twd"):
        return json.dumps({"error": f"Unknown direction '{direction}'.", "hint": "Use 'to_twd' or 'from_twd'."})
    if cur == "twd":
        return json.dumps({"error": "currency is the non-TWD side.", "hint": "Pass e.g. USD with direction 'from_twd'."})
    if amount is None or not math.isfinite(float(amount)) or float(amount) <= 0:
        return json.dumps({"error": "amount must be a positive number.", "hint": "Ask the user how much they want to convert."})

    today = _fetch(cur, "latest")
    if today is None:
        return json.dumps({
            "error": "Exchange-rate service is unreachable.",
            "hint": "Do not guess a rate. Tell the user to check https://rate.bot.com.tw/xrt?Lang=en-US (Bank of Taiwan).",
        })
    if "twd" not in today["rates"]:
        return json.dumps({"error": f"'{currency}' is not a supported currency code.",
                           "hint": "Use an ISO code like USD, EUR, JPY, KRW, GBP, HKD, SGD, AUD."})
    rate = today["rates"]["twd"]  # 1 unit of `cur` in TWD
    if not isinstance(rate, (float, int)) or not math.isfinite(rate) or rate <= 0:
        return json.dumps({"error": "Exchange-rate data is invalid.", "hint": "Retry the conversion later."})

    base = dt.date.fromisoformat(today["date"])
    dates = [(base - dt.timedelta(days=d)).isoformat() for d in HISTORY_DAYS]
    with ThreadPoolExecutor(len(dates)) as pool:
        history = [h["rates"]["twd"] for h in pool.map(lambda d: _fetch(cur, d), dates)
                   if h and isinstance(h.get("rates", {}).get("twd"), (float, int))
                   and math.isfinite(h["rates"]["twd"]) and h["rates"]["twd"] > 0]
    samples = [rate] + history
    avg = sum(samples) / len(samples) if history else None
    diff = (rate - avg) / avg * 100 if avg else None

    amt = float(amount)
    converted = amt * rate if direction == "to_twd" else amt / rate
    code = currency.strip().upper()
    if diff is None:
        verdict = None
    elif direction == "to_twd":
        verdict = "better than" if diff > 0.3 else "worse than" if diff < -0.3 else "about the same as"
    else:
        verdict = "worse than" if diff > 0.3 else "better than" if diff < -0.3 else "about the same as"

    return json.dumps({
        "amount": amt,
        "currency": code,
        "direction": direction,
        "rate": round(rate, 4),
        "rate_text": f"1 {code} = {rate:.3f} TWD",
        "converted_amount": round(converted, 2 if direction == "from_twd" else 0),
        "converted_currency": "TWD" if direction == "to_twd" else code,
        "rate_date": today["date"],
        "comparison_method": "Weekly samples over four weeks, including today; not a daily average",
        "comparison_status": "available" if history else "unavailable",
        "sampled_average": round(avg, 4) if avg else None,
        "avg_30d": round(avg, 4) if avg else None,
        "avg_samples": len(samples),
        "diff_percent": round(diff, 2) if diff is not None else None,
        "vs_30d": f"Today's rate is {verdict} the weekly sampled average over four weeks for this conversion." if history else "Historical comparison unavailable; today's conversion is still valid.",
        "tip": "This is the mid-market rate. Cash at bank or airport counters is typically 1-2% worse.",
        "source": "Daily market rates (fawazahmed0/exchange-api)",
    })
