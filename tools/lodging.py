"""Check lodging registration or compare stays from the Tourism Administration's register."""

import json
import re

from tools import lodging_preferences
from tools.tdx_client import city_choices, odata_quote, resolve_city, tdx_get

HOTEL_PATH = "tourism/service/odata/V2/Tourism/Hotel"
CANDIDATE_LIMIT = 500  # TDX's maximum $top; one cached request, no pagination probes.

# TDX HotelClasses codes -> what the license means for a traveler.
LICENSE_TYPES = {
    1: "International tourist hotel (國際觀光旅館)",
    2: "Standard tourist hotel (一般觀光旅館)",
    3: "Registered hotel (一般旅館)",
    4: "Registered B&B / homestay (民宿)",
}
TYPE_CLASSES = {"hotel": [1, 2, 3], "bnb": [4]}

# Words that differ between how a listing is advertised and how it is registered.
NAME_NOISE = re.compile(
    r"(民宿|旅館|旅店|大飯店|飯店|酒店|客棧|行館|b\s*&\s*b|bnb|homestay|hostel|hotel|inn|resort)",
    re.IGNORECASE,
)

UNREGISTERED_ADVICE = (
    "No registration found. This does not prove it is illegal: the registered name may "
    "differ from the listing name. Ask the host for their license number (民宿登記證號 / "
    "旅館登記證號) and check it; if they cannot give one, choose a registered stay."
)


def _summarize(h: dict) -> dict:
    addr = h.get("PostalAddress") or {}
    phones = [t["Tel"] for t in h.get("Telephones") or [] if t.get("Tel")]
    classes = h.get("HotelClasses") or []
    prices = lodging_preferences.price_range(h)
    low, high = prices["minimum"], prices["maximum"]
    return {
        "name": h.get("HotelName"),
        "license_number": h.get("HotelLicenseNumber"),
        "license_type": LICENSE_TYPES.get(classes[0], "Registered lodging") if classes else "Registered lodging",
        "address": f"{addr.get('City', '')}{addr.get('Town', '')}{addr.get('StreetAddress', '')}",
        "district": addr.get("Town"),
        "phone": phones[0] if phones else None,
        "reference_price_twd": f"{low}-{high}" if low and high and low != high else low,
        "price_range_twd": prices,
        "taiwan_host_certified": lodging_preferences.certified(h),
        "stars": h.get("HotelStars") or None,
        "website": h.get("WebsiteUrl") or None,
        "lat": h.get("PositionLat"),
        "lon": h.get("PositionLon"),
    }


def _search_name(name: str, county: str | None) -> list | dict:
    core = NAME_NOISE.sub("", name).strip() or name.strip()
    filters = [f"contains(HotelName,'{odata_quote(core)}')"]
    if county:
        filters.insert(0, f"PostalAddress/City eq '{county}'")
    return tdx_get(HOTEL_PATH, {"$filter": " and ".join(filters), "$top": 10})


def legal_stay_check(
    city: str,
    name: str | None = None,
    type: str | None = None,
    max_price_twd: int | None = None,
    limit: int = 5,
    district: str | None = None,
    price_preference: str = "any",
) -> str:
    place = resolve_city(city)
    if place is None:
        return json.dumps({
            "error": f"Unknown city '{city}'.",
            "hint": "Use a Taiwan city or county name.",
            "valid_cities": city_choices(),
        })
    county, town = place
    limit = max(1, min(int(limit), 10))

    # --- Check mode: is this specific place registered? ---
    if name:
        rows = _search_name(name, county)
        if isinstance(rows, dict):
            return json.dumps(rows, ensure_ascii=False)
        searched_elsewhere = False
        if not rows:
            # Travelers often get the county wrong (e.g. Jiufen is in New Taipei); search all of Taiwan.
            rows = _search_name(name, None)
            if isinstance(rows, dict):
                return json.dumps(rows, ensure_ascii=False)
            searched_elsewhere = True
        if not rows:
            return json.dumps({
                "mode": "check",
                "query": name,
                "city": county,
                "is_registered": False,
                "advice": UNREGISTERED_ADVICE,
                "hint": "Registered names are usually in Chinese. If the user only has an English "
                        "name, ask for the Chinese name or the address, or offer registered alternatives "
                        "by calling legal_stay_check without a name.",
            }, ensure_ascii=False)
        matches = [_summarize(h) for h in rows[:5]]
        return json.dumps({
            "mode": "check",
            "query": name,
            "city": county,
            "is_registered": True,
            "note": "Found outside the given city; confirm it is the same place." if searched_elsewhere
                    else "Confirm the address matches the listing: similar names can be different places.",
            "matches": matches,
            "source": "Taiwan Tourism Administration lodging register via TDX",
        }, ensure_ascii=False)

    # --- List mode: recommend registered stays ---
    try:
        lodging_preferences.validate(district, price_preference, max_price_twd)
    except ValueError as exc:
        return json.dumps({"error": str(exc), "hint": "Correct the lodging preferences and retry."})
    town = district.strip() if district is not None else town
    if type and type not in TYPE_CLASSES:
        return json.dumps({"error": f"Unknown type '{type}'.", "hint": "Use 'hotel', 'bnb', or omit it."})
    filters = [f"PostalAddress/City eq '{county}'", "ServiceStatus eq 1"]
    if town:
        filters.append(f"PostalAddress/Town eq '{odata_quote(town)}'")
    if type:
        filters.append("(" + " or ".join(f"HotelClasses/any(c: c eq {c})" for c in TYPE_CLASSES[type]) + ")")
    if max_price_twd is not None:
        # Keep absent/zero rates as suggestions; do not claim they meet the booking budget.
        filters.append(f"(LowestPrice le {max_price_twd} or LowestPrice eq null)")
    rows = tdx_get(HOTEL_PATH, {
        "$filter": " and ".join(filters),
        # Retrieve a pool, then rank by the traveler's preferences before limiting the answer.
        "$orderby": "TaiwanHost desc",
        "$top": CANDIDATE_LIMIT,
    })
    if isinstance(rows, dict):
        return json.dumps(rows, ensure_ascii=False)
    fetched_count = len(rows)
    selected, seen = [], set()
    for row in rows:
        address = row.get("PostalAddress") or {}
        if row.get("ServiceStatus") is not None and row["ServiceStatus"] != 1:
            continue
        if address.get("City") and address["City"] != county:
            continue
        if town and address.get("Town") != town:
            continue
        if type and not set(row.get("HotelClasses") or []) & set(TYPE_CLASSES[type]):
            continue
        minimum = lodging_preferences.price_range(row)["minimum"]
        if max_price_twd is not None and minimum is not None and minimum > max_price_twd:
            continue
        key = row.get("HotelID") or (row.get("HotelName"), row.get("HotelLicenseNumber"),
                                     address.get("Town"), address.get("StreetAddress"))
        if key not in seen:
            seen.add(key)
            selected.append(row)
    rows = lodging_preferences.rank(selected, price_preference, max_price_twd)
    comparison = lodging_preferences.compare(rows[:limit], town, price_preference, max_price_twd,
                                              fetched_count, CANDIDATE_LIMIT, len(rows))
    preferences = {"district": town, "type": type, "price_preference": price_preference,
                   "max_price_twd": max_price_twd}
    if not rows:
        return json.dumps({
            "mode": "list",
            "city": county,
            "town": town,
            "preferences": preferences,
            "comparison": comparison,
            "stays": [],
            "hint": "No returned registered stays match. Offer a broader district, type, or budget search; "
                    "do not silently relax the user's criteria.",
        }, ensure_ascii=False)
    return json.dumps({
        "mode": "list",
        "city": county,
        "town": town,
        "preferences": preferences,
        "comparison": comparison,
        "stays": [dict(_summarize(h), preference_match=lodging_preferences.fit(h, town, max_price_twd))
                  for h in rows[:limit]],
        "note": "Prices are the owner-reported reference range, not live availability. Book on a booking site.",
        "source": "Taiwan Tourism Administration lodging register via TDX",
    }, ensure_ascii=False)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "legal_stay_check",
        "description": (
            "Check whether a hotel or B&B in Taiwan is officially registered with the Tourism "
            "Administration, or list registered stays in a city. Use this for ANY lodging question: "
            "never recommend a stay that this tool did not return. With `name`, checks that place "
            "(returns license number, type, address, phone). Without `name`, lists registered stays, "
            "ranked by district, type and budget preferences before certification, with owner-reported "
            "reference prices, a recommended stay and alternatives. Missing rates allow suggestions. "
            "No live availability or booking."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Taiwan city/county in English, e.g. 'Hualien', 'Tainan', 'New Taipei'. "
                                   "Famous spots also work: 'Jiufen', 'Kenting', 'Sun Moon Lake'.",
                },
                "name": {
                    "type": "string",
                    "description": "Name of the lodging to check. The register is in Chinese, so pass the "
                                   "Chinese name if the user gave one. Omit to list registered stays.",
                },
                "type": {
                    "type": "string",
                    "enum": ["hotel", "bnb"],
                    "description": "List mode only: 'hotel' or 'bnb' (民宿 homestay). Use 'bnb' when the user "
                                   "talks about B&Bs, homestays or guesthouses. Omit for both.",
                },
                "max_price_twd": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "List mode only: user-stated numeric nightly reference budget in TWD. "
                                   "Never invent a cap for cheap/budget alone; use price_preference instead. "
                                   "Convert a stated foreign-currency budget with twd_exchange first. "
                                   "Starting reference rates are not booking quotes; missing rates remain eligible.",
                },
                "district": {"type": "string", "description": "List mode only: requested district in Traditional Chinese, "
                             "e.g. 萬華區 or 中西區, from this message or session context. A district match "
                             "does not verify proximity to an MRT station or landmark."},
                "price_preference": {"type": "string", "enum": list(lodging_preferences.PRICE_PREFERENCES),
                                     "description": "List mode only: budget for cheap/budget-friendly requests, "
                                                    "ranking by lower reported starting rates without an invented cap; default any."},
                "limit": {"type": "integer", "description": "List mode only: how many stays (1-10, default 5)."},
            },
            "required": ["city"],
        },
    },
}
