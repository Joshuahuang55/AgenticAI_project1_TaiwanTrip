"""Compare registered stays using reported reference prices, never live booking claims."""

import math
import re

SOURCE = "Taiwan Tourism Administration lodging register via TDX"
PRICE_PREFERENCES = ("any", "budget")


def requested_limit(message):
    """Honor a direct count request without mistaking guests, nights, or a price for stay options."""
    words = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
    english = re.compile(
        r"(?:^|[.!?]\s+)(?:please\s+)?(?:recommend|suggest|list|show|give)\s+"
        r"(?:(?:me|us)\s+)?(?:(?:the\s+)?(?:top|best)\s+)?"
        r"(one|two|three|four|five|six|seven|eight|nine|ten|10|[1-9])\b"
        r"(?![.,]\d)(?!\s+or\b)"
        r"(?![\s-]+(?:hours?|minutes?|nights?|people|guests?|rooms?|dollars?|tickets?|stars?)\b)", re.I)
    matches = list(english.finditer(message))
    if matches:
        value = matches[-1].group(1).lower()
        return int(value) if value.isdigit() else words.index(value) + 1
    chinese = list(re.finditer(
        r"(?<!不要)(?<!別)(?<!别)(?:推薦|推荐|介紹|介绍|列出)\s*"
        r"([一二兩两三四五六七八九十]|10|[1-9])\s*(?:家|間|间|個|个)"
        r"(?!\s*(?:人|成人|[單单雙双三四]人房|房|晚|夜|小時|小时))", message))
    if chinese:
        value = chinese[-1].group(1).replace("兩", "二").replace("两", "二")
        return int(value) if value.isdigit() else "一二三四五六七八九十".index(value) + 1
    return None


def validate(district, price_preference, max_price_twd):
    if district is not None and (not isinstance(district, str) or not district.strip()):
        raise ValueError("district must be a nonempty district name or omitted")
    if price_preference not in PRICE_PREFERENCES:
        raise ValueError("price_preference must be any or budget")
    if max_price_twd is not None and (
        isinstance(max_price_twd, bool) or not isinstance(max_price_twd, int) or max_price_twd <= 0
    ):
        raise ValueError("max_price_twd must be a positive integer nightly reference budget in TWD")


def _price(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return int(number) if number.is_integer() else number


def price_range(row):
    minimum, maximum = _price(row.get("LowestPrice")), _price(row.get("CeilingPrice"))
    if minimum is not None and maximum is not None and maximum < minimum:
        maximum = None  # Preserve a valid starting rate, not a reversed range.
    return {"minimum": minimum, "maximum": maximum,
            "status": "reported" if minimum is not None or maximum is not None else "unknown",
            "source": SOURCE, "kind": "owner_reported_reference", "booking_price": None}


def certified(row):
    return row.get("TaiwanHost") in (True, 1, "1", "true", "True")


def fit(row, district, max_price_twd):
    town = (row.get("PostalAddress") or {}).get("Town")
    minimum = price_range(row)["minimum"]
    return {"district": "not_requested" if not district else "match" if town == district
            else "unknown" if not town else "different_district",
            "budget": "not_requested" if max_price_twd is None else "unknown" if minimum is None
            else "reference_start_within_cap" if minimum <= max_price_twd else "reference_start_over_cap"}


def rank(rows, price_preference, max_price_twd):
    def score(row):
        minimum = price_range(row)["minimum"]
        # Preferences precede certification. Missing prices remain eligible, never free.
        price_key = (minimum is None, minimum if minimum is not None else math.inf)
        details = bool(row.get("WebsiteUrl")) + bool(row.get("Telephones"))
        if price_preference == "budget" or max_price_twd is not None:
            return (*price_key, not certified(row), -details)
        return (not certified(row), *price_key, -details)
    return sorted(rows, key=score)


def _reason(row, district, price_preference, max_price_twd):
    parts = []
    town = (row.get("PostalAddress") or {}).get("Town")
    if district and town == district:
        parts.append(f"Listed in your requested district, {district}")
    elif town:
        parts.append(f"Listed in {town}")
    minimum = price_range(row)["minimum"]
    if minimum is not None:
        parts.append(f"Reported starting rate NT${minimum:,}")
        if max_price_twd is not None and minimum <= max_price_twd:
            parts.append(f"Starting reference rate is within your NT${max_price_twd:,} cap")
        elif price_preference == "budget":
            parts.append("Ranked by lower reported starting rates")
    elif price_preference == "budget" or max_price_twd is not None:
        parts.append("A possible option without a listed starting rate")
    if certified(row):
        parts.append("Marked Taiwan Host certified")
    return "; ".join(parts) or "An option from the official lodging register"


def compare(rows, district, price_preference, max_price_twd, candidate_count, pool_limit, matching_count):
    out = {"recommended_name": rows[0].get("HotelName") if rows else None,
           "reason": _reason(rows[0], district, price_preference, max_price_twd) if rows else None,
           "matching_candidate_count": matching_count, "fetched_candidate_count": candidate_count,
           "candidate_pool_limit": pool_limit, "candidate_pool_may_be_capped": candidate_count >= pool_limit,
           "alternatives": [],
           "price_note": "Owner-reported reference rates, not prices or availability for your travel dates."}
    if not rows:
        return out
    best = rows[0]
    best_price = price_range(best)["minimum"]
    for row in rows[1:]:
        price = price_range(row)["minimum"]
        trade_offs = []
        if best_price is not None and price is not None:
            delta = price - best_price
            trade_offs.append("Same reported starting rate" if not delta else
                              f"Reported starting rate NT${abs(delta):,} {'higher' if delta > 0 else 'lower'}")
        elif price is None:
            trade_offs.append("Starting rate not listed; compare the booking quote")
        else:
            trade_offs.append(f"Has a listed starting reference rate of NT${price:,}")
        if certified(row) != certified(best):
            trade_offs.append("Marked Taiwan Host certified" if certified(row) else "Not marked Taiwan Host certified")
        if row.get("HotelClasses") != best.get("HotelClasses"):
            trade_offs.append("Different licensed lodging type; see license_type")
        out["alternatives"].append({"name": row.get("HotelName"),
                                    "reason": _reason(row, district, price_preference, max_price_twd),
                                    "trade_offs": trade_offs})
    return out
