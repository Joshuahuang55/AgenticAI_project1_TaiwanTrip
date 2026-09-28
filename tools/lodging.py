"""legal_stay_check (original tool, member C): is a hotel/B&B registered with Taiwan's Tourism Administration?

Taiwan has many unregistered B&Bs listed on booking sites. Staying in one means no
fire-safety inspection and no insurance if something goes wrong. The Tourism
Administration's register (via TDX) is the ground truth.
"""

import json
import re

from tools.tdx_client import city_choices, odata_quote, resolve_city, tdx_get

HOTEL_PATH = "tourism/service/odata/V2/Tourism/Hotel"

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
    low, high = h.get("LowestPrice") or None, h.get("CeilingPrice") or None
    return {
        "name": h.get("HotelName"),
        "license_number": h.get("HotelLicenseNumber"),
        "license_type": LICENSE_TYPES.get(classes[0], "Registered lodging") if classes else "Registered lodging",
        "address": f"{addr.get('City', '')}{addr.get('Town', '')}{addr.get('StreetAddress', '')}",
        "phone": phones[0] if phones else None,
        "reference_price_twd": f"{low}-{high}" if low and high and low != high else low,
        "taiwan_host_certified": bool(h.get("TaiwanHost")),
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
    if type and type not in TYPE_CLASSES:
        return json.dumps({"error": f"Unknown type '{type}'.", "hint": "Use 'hotel', 'bnb', or omit it."})
    filters = [f"PostalAddress/City eq '{county}'", "ServiceStatus eq 1"]
    if town:
        filters.append(f"PostalAddress/Town eq '{town}'")
    if type:
        filters.append("(" + " or ".join(f"HotelClasses/any(c: c eq {c})" for c in TYPE_CLASSES[type]) + ")")
    if max_price_twd:
        filters.append(f"LowestPrice gt 0 and LowestPrice le {int(max_price_twd)}")
    rows = tdx_get(HOTEL_PATH, {
        "$filter": " and ".join(filters),
        # Taiwan Host (好客民宿) certified places first. Not by stars: that surfaces luxury hotels.
        "$orderby": "TaiwanHost desc",
        "$top": limit,
    })
    if isinstance(rows, dict):
        return json.dumps(rows, ensure_ascii=False)
    if not rows:
        return json.dumps({
            "mode": "list",
            "city": county,
            "stays": [],
            "hint": "No registered stays match. Drop max_price_twd or type, or try a nearby city.",
        }, ensure_ascii=False)
    return json.dumps({
        "mode": "list",
        "city": county,
        "town": town,
        "stays": [_summarize(h) for h in rows],
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
            "Taiwan Host (好客民宿) certified ones first, with owner-reported reference prices in TWD. "
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
                    "description": "List mode only: max reference nightly price in TWD. Set it whenever the user "
                                   "says cheap/budget or gives a price; convert other currencies with twd_exchange first.",
                },
                "limit": {"type": "integer", "description": "List mode only: how many stays (1-10, default 5)."},
            },
            "required": ["city"],
        },
    },
}
