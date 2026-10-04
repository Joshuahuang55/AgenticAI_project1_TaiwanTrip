"""find_local_food (member C): restaurants and night markets from the Tourism Administration and OpenStreetMap,
ranked by awards (Michelin Guide, 500盤, 500碗) the way find_attractions ranks by fame."""

import gzip
import json
import math
import re
from pathlib import Path

from tools import attractions, food_preferences, tourism_data
from tools.tdx_client import odata_quote, resolve_city, tdx_get

RESTAURANT_PATH = "tourism/service/odata/V2/Tourism/Restaurant"
DATA = Path(__file__).parent / "data"

# OpenStreetMap places from scripts/build_osm_food.py: the official register lists no restaurants in Taipei
# or Kaohsiung. They are shaped like official rows, so both are searched and summarized alike.
OSM_PATH = DATA / "osm_food.json.gz"
OSM_SOURCE = "OpenStreetMap (© OpenStreetMap contributors, ODbL)"
OSM_NOTE = "OpenStreetMap listing, not the official register."
DUPLICATE_KM = 0.15  # an OSM place this close to an official one with a matching name is the same shop

# OSM id -> {"fame", "local", "awards", "known_for", "name_en", "price", "fine_dining", "closed"}, plus the
# Michelin restaurants OSM lacks, from scripts/build_food_fame.py. Research and education use only.
FAME_PATH = DATA / "food_fame.json"
_fame_file = json.loads(FAME_PATH.read_text(encoding="utf-8")) if FAME_PATH.exists() else {}
FOOD_FAME: dict[str, dict] = _fame_file.get("places", {})
AWARD_SOURCE = "Michelin Guide Taiwan via michelin-my-maps, and 500盤/500碗 by 500輯: research and education use only"
PRICE_SOURCE = "Michelin Guide Taiwan (bundled relative price bands; research and education use only)"
MICHELIN_NOTE = "From the Michelin Guide, not OpenStreetMap: the Chinese name is a translation; show the address."

STYLES = attractions.STYLES
LOCAL_GEMS = 2  # a must-see search also carries up to this many places with a local score of LOCAL_GEM_MIN+
LOCAL_GEM_MIN = 0.5
MORE_CANDIDATES = 30
# Stars and $$$+ prices top the fame scale, but most travelers asking where to eat want local food: unless
# they ask for fine dining, it fills at most a third of the results.
FINE_DINING_WORDS = ("fine dining", "michelin", "tasting menu", "omakase", "star", "fancy", "upscale", "米其林")

# The listings are in Chinese: what a foreign traveler types -> what the names say.
KEYWORDS_ZH = {
    "beef noodle": "牛肉麵", "beef noodles": "牛肉麵", "beef soup": "牛肉湯", "beef": "牛肉",
    "vegetarian": "素", "vegan": "素", "dumpling": "餃", "dumplings": "餃",
    "xiaolongbao": "小籠包", "soup dumpling": "小籠包", "braised pork rice": "滷肉飯",
    "danzai noodles": "擔仔麵", "milkfish": "虱目魚", "oyster": "蚵", "oyster omelette": "蚵仔煎",
    "shrimp roll": "蝦捲", "eel noodles": "鱔魚", "seafood": "海鮮", "hot pot": "火鍋",
    "breakfast": "早餐", "coffee": "咖啡", "cafe": "咖啡", "tea": "茶", "bubble tea": "奶茶",
    "dessert": "甜", "shaved ice": "冰", "ice": "冰", "bakery": "麵包", "pineapple cake": "鳳梨酥",
    "noodles": "麵", "noodle": "麵", "rice": "飯", "duck": "鴨", "chicken": "雞", "pork": "豬",
    "steak": "牛排", "japanese": "日式", "sushi": "壽司", "ramen": "拉麵", "barbecue": "燒烤",
    "bbq": "燒烤", "aboriginal": "原住民", "indigenous": "原住民", "hakka": "客家",
}
# ... and -> OSM cuisine tags, for places whose names do not say what they serve.
CUISINE_TAGS = {
    "beef noodle": "noodle", "beef noodles": "noodle", "noodles": "noodle", "noodle": "noodle", "ramen": "ramen",
    "dumpling": "dumpling", "dumplings": "dumpling",
    "coffee": "coffee_shop", "cafe": "coffee_shop", "tea": "tea", "bubble tea": "bubble_tea", "hot pot": "hot_pot",
    "barbecue": "barbecue", "bbq": "barbecue", "seafood": "seafood", "vegetarian": "vegetarian", "vegan": "vegan",
    "sushi": "sushi", "japanese": "japanese", "dessert": "dessert", "shaved ice": "ice_cream", "ice": "ice_cream",
    "breakfast": "breakfast", "steak": "steak_house", "bakery": "bakery", "duck": "duck", "chicken": "chicken",
}
NIGHT_MARKET_WORDS = {"night market", "night markets", "夜市"}
# Rotating night markets the register does not list, and the days they open.
LOCAL_NIGHT_MARKETS = {
    "臺南市": [
        {"name": "Garden Night Market (花園夜市)", "open_days": "Thu, Sat, Sun evenings",
         "address": "臺南市北區海安路三段533號"},
        {"name": "Dadong Night Market (大東夜市)", "open_days": "Mon, Tue, Fri evenings",
         "address": "臺南市東區林森路一段276號"},
        {"name": "Wusheng Night Market (武聖夜市)", "open_days": "Wed, Sat evenings",
         "address": "臺南市中西區武聖路69巷42號"},
    ],
}


def _osm_row(p: dict) -> dict:
    """An OSM place (or Michelin extra) shaped like an official row. Its district ("t") is estimated from
    the nearest official listing; the street address ("a", Michelin's "addr") is there only when known."""
    return {"RestaurantID": p["id"], "RestaurantName": p["n"], "PositionLat": p["lat"], "PositionLon": p["lon"],
            "PostalAddress": {"City": p["city"], "Town": p.get("t"), "StreetAddress": p.get("a") or p.get("addr")},
            "Description": "", "ServiceTimeInfo": p.get("h"),
            "NameEn": p.get("en"), "NameJa": p.get("ja"), "Cuisine": p.get("c") or "",
            "DietaryTags": {key: p[short] for key, short in (("vegetarian", "dv"), ("vegan", "dg")) if p.get(short)},
            "DistrictStatus": p.get("ts", "estimated") if p.get("t") else "unknown",
            "Source": "Michelin Guide" if p["id"].startswith("michelin:") else "OpenStreetMap"}


def _load_osm() -> dict[str, list[dict]]:
    if not OSM_PATH.exists():
        return {}
    with gzip.open(OSM_PATH, "rt", encoding="utf-8") as f:
        places = json.load(f)["places"]
    out: dict[str, list[dict]] = {}
    for p in places + _fame_file.get("extras", []):
        out.setdefault(p["city"], []).append(_osm_row(p))
    return out


OSM_BY_COUNTY = _load_osm()

# session id -> {name: row} from that session's searches, so the answer's picks get map pins (pins_from_answer).
_seen_by_session: dict[str, dict[str, dict]] = {}


def _seen() -> dict[str, dict]:
    sid = attractions._session.get()
    if sid not in _seen_by_session and len(_seen_by_session) >= attractions.MAX_SESSIONS:
        _seen_by_session.pop(next(iter(_seen_by_session)))  # drop the oldest session
    seen = _seen_by_session.setdefault(sid, {})
    if len(seen) > 5000:
        seen.clear()
    return seen


def forget_session(session_id: str) -> None:
    _seen_by_session.pop(session_id, None)


def _fame(r: dict) -> dict:
    return FOOD_FAME.get(r.get("FameID") or r.get("RestaurantID"), {})


def _asks_fine_dining(kw: str) -> bool:
    return any(w in kw.lower() for w in FINE_DINING_WORDS)


def _osm_places(county: str, kw: str, term: str, town: str | None = None) -> list[dict]:
    """OSM places in a county (and district) matching the keyword by Chinese name, English name or cuisine."""
    rows = OSM_BY_COUNTY.get(county, [])
    if town:
        rows = [r for r in rows if r["PostalAddress"].get("Town") == town]
    if not kw:
        return rows
    cuisine, low = CUISINE_TAGS.get(kw.lower()), kw.lower()
    return [r for r in rows if (term and term in r["RestaurantName"]) or (low in (r["NameEn"] or "").lower())
            or (cuisine and cuisine in r["Cuisine"].split(";"))]


def _km(a: dict, b: dict) -> float:
    if not (a.get("PositionLat") and b.get("PositionLat")):
        return float("inf")
    dy = (a["PositionLat"] - b["PositionLat"]) * 111.0
    dx = (a["PositionLon"] - b["PositionLon"]) * 111.0 * math.cos(math.radians(a["PositionLat"]))
    return math.hypot(dx, dy)


def _merge(official: list[dict], osm: list[dict]) -> list[dict]:
    """Official rows first. An OSM place matching an official one (name in order, within DUPLICATE_KM) is
    dropped, and lends the official row its English name, award scores and opening hours."""
    def same_name(a: dict, b: dict) -> bool:
        x, y = attractions._clean(a["RestaurantName"]), attractions._clean(b["RestaurantName"])
        return attractions._in_order(x, y) or attractions._in_order(y, x)
    out = list(official)
    for o in osm:
        twin = next((r for r in official if _km(r, o) <= DUPLICATE_KM and same_name(o, r)), None)
        if twin is None:
            out.append(o)
            continue
        twin.setdefault("NameEn", o["NameEn"])
        twin.setdefault("FameID", o["RestaurantID"])
        fields = twin.setdefault("FieldSources", {})
        if not twin.get("Cuisine") and o.get("Cuisine"):
            twin["Cuisine"] = o["Cuisine"]
            fields["cuisine"] = o["Source"]
        if not twin.get("DietaryTags") and o.get("DietaryTags"):
            twin["DietaryTags"] = o["DietaryTags"]
            fields["dietary"] = o["Source"]
        if not (twin.get("ServiceTimeInfo") or "").strip():
            twin["ServiceTimeInfo"] = o["ServiceTimeInfo"]
            if o["ServiceTimeInfo"]:
                fields["opening_hours"] = o["Source"]
    return out


def _address(addr: dict) -> str:
    """City, district and street; a full address (OSM addr:full, Michelin's English one) stands alone,
    without a leading postal code ('540南投縣...') and with 臺 for 台."""
    street = re.sub(r"^\d{3,6}", "", (addr.get("StreetAddress") or "").strip()).replace("台", "臺")
    if street and (street.startswith(addr.get("City") or "-") or street.isascii()):
        return street
    town = addr.get("Town") or ""
    return f"{addr.get('City') or ''}{'' if street.startswith(town) else town}{street}"


def _summarize(r: dict, name_key: str, pin: bool = False) -> dict:
    """A result for the model. Only pins carry lat/lon: the frontend pins anything that has them, and the
    map should show the places the answer recommends, not every candidate."""
    phones = [t["Tel"] for t in r.get("Telephones") or [] if t.get("Tel")]
    desc = (r.get("Description") or "").strip()
    out = {
        "name": r.get(name_key),
        "description": desc[:160] + ("…" if len(desc) > 160 else ""),
        "address": _address(r.get("PostalAddress") or {}),
        "open_time": r.get("ServiceTimeInfo") or None,
        "phone": phones[0] if phones else None,
    }
    lat, lon = r.get("PositionLat"), r.get("PositionLon")
    if pin:
        out.update(lat=lat, lon=lon)
    fame = _fame(r)
    facts = r.get("_Facts") or food_preferences.facts_for(r, fame, r.get("Source") or tourism_data.SOURCE, PRICE_SOURCE)
    out["facts"] = facts
    out["source"] = r.get("Source") or tourism_data.SOURCE
    out["missing_fields"] = [key for key, f in facts.items() if f["status"] == "unknown"]
    if r.get("_Comparison"):
        out["comparison"] = r["_Comparison"]
    if fame.get("name_en") or r.get("NameEn"):
        out["name_en"] = fame.get("name_en") or r["NameEn"]
    if fame.get("awards"):
        out["awards"] = fame["awards"]
    if fame.get("known_for"):  # well known without an award (reviewed list)
        out["known_for"] = fame["known_for"]
    if lat and lon:  # for places with no street address
        out["map_url"] = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
    if facts["price"]["value"]:  # a relative band, not an exact current menu price
        out["price"] = fame["price"]
    if r.get("Source") == "OpenStreetMap":
        out["note"] = OSM_NOTE
    elif r.get("Source") == "Michelin Guide":
        out["note"] = MICHELIN_NOTE
    return out


def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        key = (it["name"], it["address"])
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _rank(rows: list[dict], term: str, style: str) -> list[dict]:
    """Best first: the keyword in the name, then fame ('must_see') or local score ('local'), then listings
    with a description. Ties keep the official-then-OSM order."""
    key = "local" if style == "local" else "fame"
    def score(r: dict) -> tuple:
        named = bool(term) and term in (r.get("RestaurantName") or "")
        preference = food_preferences.priority(r["_Comparison"]) if r.get("_Comparison") else (0, 0, 0, 0, 0)
        return *preference, named, _fame(r).get(key, 0.0), len(r.get("Description") or "") >= 40
    return sorted(rows, key=score, reverse=True)


def _one_per_name(rows: list[dict]) -> list[dict]:
    """The best-ranked branch of each name: six 鼎泰豐 would fill the list."""
    seen, out = set(), []
    for r in rows:
        key = re.sub(r"[\W_]", "", attractions._clean(r.get("RestaurantName") or "")).lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _few_fine_dining(rows: list[dict], limit: int, kw: str) -> list[dict]:
    if _asks_fine_dining(kw):
        return rows
    return attractions.cap_share(rows, limit, lambda r: _fame(r).get("fine_dining", False))


def _local_gems(ranked: list[dict], picked: list[dict], limit: int, kw: str, n: int = LOCAL_GEMS) -> list[dict]:
    """The best local scores not already picked, keeping fine dining within a third of the final list."""
    gems = sorted((r for r in ranked if r not in picked and _fame(r).get("local", 0.0) >= LOCAL_GEM_MIN),
                  key=lambda r: _fame(r)["local"], reverse=True)
    if _asks_fine_dining(kw):
        return gems[:n]
    room = max(1, limit // 3) - sum(_fame(r).get("fine_dining", False) for r in picked[:limit - n])
    out = []
    for r in gems:
        if _fame(r).get("fine_dining"):
            if room <= 0:
                continue
            room -= 1
        out.append(r)
    return out[:n]


def _more_candidates(ranked: list[dict], picked: list[dict]) -> list[str]:
    """The next MORE_CANDIDATES award winners or well-known places, by name, for the model to look up with
    `names`. Not every match as in find_attractions: Taipei has 14,000 places, mostly unknown to the model,
    and listing them all made no difference to its picks."""
    def label(r: dict) -> str:
        notes = _fame(r).get("awards") or ["well known: " + _fame(r)["known_for"]]
        return f"{r['RestaurantName']} ({'; '.join(notes)})"
    return [label(r) for r in ranked if r not in picked and (_fame(r).get("awards") or _fame(r).get("known_for"))
            ][:MORE_CANDIDATES]


def _prepare(rows: list[dict], criteria: dict, source: str) -> tuple[list[dict], list[dict]]:
    return food_preferences.prepare([dict(r, _Fame=_fame(r)) for r in rows], criteria, source, PRICE_SOURCE)


def _result_source(rows: list[dict], fallback: str) -> str:
    sources = []
    for row in rows:
        for source in [row.get("Source") or fallback, *(row.get("FieldSources") or {}).values()]:
            if source == "OpenStreetMap":
                source = OSM_SOURCE
            if source not in sources:
                sources.append(source)
    if any(_fame(r) for r in rows):
        sources.append(AWARD_SOURCE)
    return ", plus ".join(sources) or fallback


def _location_note(district: str | None) -> str | None:
    if not district:
        return None
    return (f"Search scope: the whole {district} district; some district assignments are estimated. "
            "Addresses and map links provide location details.")


def _details(county: str, names: list[str], criteria: dict) -> str:
    """Restaurants the model names, matched loosely against the county's places (阿宗麵線 finds 阿宗麵線西門店),
    best known first."""
    official = tourism_data.listings("restaurants", county)
    rows = [dict(r, Source=tourism_data.SOURCE) for r in official or []
            if r.get("ServiceStatus") not in attractions.CLOSED_STATUS]
    rows = [r for r in _merge(rows, OSM_BY_COUNTY.get(county, [])) if not _fame(r).get("closed")]
    found, not_found = [], []
    for name in names[:10]:
        hits = attractions._name_matches(name, rows, "RestaurantName")[:20]
        hits = sorted(hits, key=lambda r: _fame(r).get("fame", 0.0), reverse=True)[:attractions.MAX_NAME_MATCHES]
        if hits:
            found += hits
        else:
            not_found.append(name)
    found, excluded = _prepare(found, criteria, tourism_data.SOURCE)
    found = _rank(found, "", "must_see")
    _remember(found, "RestaurantName")
    source = _result_source(found, "Bundled OpenStreetMap/Michelin data" if official is None else tourism_data.SOURCE)
    results = _dedupe([_summarize(r, "RestaurantName") for r in found])
    return json.dumps({
        "city": county,
        "results": results,
        "not_found": not_found,
        "criteria": criteria,
        "location_note": _location_note(criteria.get("district")),
        "comparison_summary": food_preferences.summary(results, excluded),
        "excluded": excluded,
        "note": "Recommend only places in results. A name in not_found is not in the sources: leave it out.",
        "coverage_note": ("Official restaurant file unavailable; only bundled OpenStreetMap/Michelin records "
                          "were searched. not_found does not establish that a restaurant does not exist."
                          if official is None else "Official file and bundled OpenStreetMap/Michelin records searched; not exhaustive."),
        "source": source,
    }, ensure_ascii=False)


def _remember(rows: list[dict], name_key: str) -> None:
    """Keep the places shown to the model (results and more_candidates) for pins_from_answer."""
    _seen().update({r[name_key]: dict(r, _name_key=name_key) for r in rows if r.get(name_key)})


def pins_from_answer(answer: str) -> list[dict]:
    """Map pins for the places the final answer names: this session's shown places whose Chinese name
    appears in the text, longest names first (each match is blanked out, so 鼎泰豐 does not also match
    inside 鼎泰豐信義店), in the order the answer mentions them."""
    seen = _seen()
    found = []
    for name in sorted(seen, key=len, reverse=True):
        at = answer.find(name)
        if len(name) >= 2 and at >= 0:
            answer = answer.replace(name, "\0" * len(name))
            found.append((at, seen[name]))
    return [_summarize(r, r["_name_key"], pin=True) for _, r in sorted(found, key=lambda x: x[0])]


def _night_markets(county: str, town: str | None, limit: int, criteria: dict) -> str:
    rows, source = attractions.search_rows(county, town, ("夜市",), name_only=True)
    if isinstance(rows, dict):
        return json.dumps(rows, ensure_ascii=False)
    rows = [r for r in rows if r.get("ServiceStatus") not in attractions.CLOSED_STATUS]
    rows = [dict(r, Source=source) for r in rows]
    rows, excluded = _prepare(rows, criteria, source)
    markets = _dedupe([_summarize(r, "AttractionName") for r in rows])[:limit]
    _remember(rows, "AttractionName")
    local = [tip for tip in LOCAL_NIGHT_MARKETS.get(county, []) if not town or town in tip["address"]]
    if not markets and not local:
        return json.dumps({
            "city": county,
            "results": [],
            "source": source,
            "criteria": criteria,
            "comparison_summary": food_preferences.summary([], excluded),
            "hint": "No night markets are registered for this city. Suggest a nearby big city "
                    "(Taipei, Taichung, Tainan, Kaohsiung) or search food with another keyword.",
        }, ensure_ascii=False)
    return json.dumps({
        "city": county,
        "kind": "night_market",
        "results": markets,
        "criteria": criteria,
        "comparison_summary": food_preferences.summary(markets, excluded),
        "local_tips": local,
        "note": "Rotating market days are local tips. Dietary/menu facts describe individual stalls, "
                "not the market as a whole.",
        "source": source + (", plus local knowledge" if local else ""),
    }, ensure_ascii=False)


def find_local_food(city: str, keyword: str | None = None, district: str | None = None, limit: int = 10,
                    style: str = "must_see", names: list[str] | None = None,
                    dietary: str | None = None, price_preference: str | None = None,
                    max_price_twd: float | None = None, confirmed_only: bool = False) -> str:
    place = resolve_city(city)
    if place is None:
        return attractions.unknown_city(city)
    county, town = place
    town = district or town
    limit = max(1, min(int(limit), 10))
    if style not in STYLES:
        return attractions.unknown_style(style, "places locals rate that few visitors know")

    kw = (keyword or "").strip()
    dietary = dietary or food_preferences.DIET_KEYWORDS.get(kw.lower())
    if not isinstance(confirmed_only, bool):
        return json.dumps({"error": "confirmed_only must be a boolean.", "hint": "Use true or false."})
    if dietary is not None and dietary not in food_preferences.DIETARY:
        return json.dumps({"error": "Unsupported dietary preference.", "hint": "Use vegetarian or vegan. "
                           "Other restrictions need direct restaurant confirmation; do not invent support."})
    if price_preference is not None and price_preference not in food_preferences.PRICE_PREFERENCES:
        return json.dumps({"error": "Unsupported price preference.", "hint": "Use budget, mid_range, or any."})
    if max_price_twd is not None and (isinstance(max_price_twd, bool) or not isinstance(max_price_twd, (int, float))
                                    or not math.isfinite(max_price_twd) or max_price_twd <= 0):
        return json.dumps({"error": "max_price_twd must be a positive finite number.",
                           "hint": "Use a stated TWD per-person meal cap; do not guess currency or scope."})
    criteria = {"district": town, "dietary": dietary, "price_preference": price_preference,
                "max_price_twd": max_price_twd, "confirmed_only": confirmed_only}
    constrained = bool(town or dietary or (price_preference and price_preference != "any") or max_price_twd)
    if names:
        return _details(county, names, criteria)
    if kw.lower() in NIGHT_MARKET_WORDS:
        return _night_markets(county, town, limit, criteria)

    fine = _asks_fine_dining(kw)
    # Dietary tags may be present even when a restaurant name contains no dietary word.
    diet_keyword = kw.lower() in food_preferences.DIET_KEYWORDS
    term = "" if fine or diet_keyword else KEYWORDS_ZH.get(kw.lower(), kw)
    rows = tourism_data.listings("restaurants", county, town, (term,) if term else ())
    source = tourism_data.SOURCE
    if rows is None:  # the daily file is not loaded: ask TDX
        filters = [f"PostalAddress/City eq '{county}'"]
        if town:
            filters.append(f"PostalAddress/Town eq '{odata_quote(town)}'")
        if term:
            q = odata_quote(term)
            filters.append(f"(contains(RestaurantName,'{q}') or contains(Description,'{q}'))")
        rows = tdx_get(RESTAURANT_PATH, {"$filter": " and ".join(filters), "$top": limit * 3})
        source = "Taiwan Tourism Administration via TDX"
    if isinstance(rows, dict):
        return json.dumps(rows, ensure_ascii=False)
    rows = [dict(r, Source=source) for r in rows if r.get("ServiceStatus") not in attractions.CLOSED_STATUS]
    osm = _osm_places(county, "" if fine or diet_keyword else kw, term, town)
    if osm:
        rows = _merge(rows, osm)
        if any(r.get("Source") == "OpenStreetMap" for r in rows):
            source += ", plus " + OSM_SOURCE
    rows = [r for r in rows if not _fame(r).get("closed")]
    rows, excluded = _prepare(rows, criteria, source)

    ranked = _one_per_name(_rank(rows, term, style))
    if not constrained:
        ranked = _few_fine_dining(ranked, limit, kw)
    if fine and not constrained:
        ranked.sort(key=lambda r: _fame(r).get("fine_dining", False), reverse=True)
    picked = ranked[:limit]
    gems = _local_gems(ranked, picked, limit, kw) if not constrained and style == "must_see" and limit >= 5 else []
    picked = picked[:limit - len(gems)] + gems
    _remember(picked + [r for r in ranked if r not in picked and (_fame(r).get("awards") or _fame(r).get("known_for"))
                        ][:MORE_CANDIDATES], "RestaurantName")
    results = _dedupe([dict(_summarize(r, "RestaurantName"), **({"local_gem": True} if r in gems else {}))
                       for r in picked])
    if picked:
        source = _result_source(picked, source)
    if not results:
        hint = "No listed restaurants match. "
        if term and term.isascii():
            hint += (f"Listings are in Chinese; retry with a Traditional Chinese keyword for '{kw}' "
                     "(e.g. 牛肉麵, 小吃, 海鮮) or a broader one.")
        elif district:
            hint += "Retry without `district` to search the whole city."
        else:
            hint += "Retry with a broader keyword, or without a keyword."
        if excluded:
            hint = ("No listings have source evidence for every required criterion; briefly explain the missing evidence."
                    if confirmed_only else
                    "No suitable listings remain after applying the criteria; offer a different search area or keyword.")
        return json.dumps({"city": county, "keyword": kw, "results": [], "hint": hint, "source": source,
                           "criteria": criteria, "comparison_summary": food_preferences.summary([], excluded),
                           "excluded": excluded[:10]}, ensure_ascii=False)

    return json.dumps({
        "city": county,
        "keyword": kw or None,
        "style": style,
        "searched_as": term if term != kw else None,
        "results": results,
        "criteria": criteria,
        "comparison_summary": food_preferences.summary(results, excluded),
        "excluded": excluded[:10],
        "location_note": _location_note(town),
        "ranking_basis": "Dietary reports first, then name/cuisine indications, then candidates with no "
                         "dietary evidence; remaining preference evidence and reported dietary variety before limited options, "
                         "then lower reported price bands for exact-budget leads, then keyword and "
                         "awards/local score.",
        "more_candidates": _more_candidates(ranked, picked),
        "note": "Names and descriptions are in Chinese: translate them for the user and keep the "
                "Chinese name so they can show it to a taxi driver. Mention awards only as listed in "
                "`awards`. Recommend relevant returned options and compare useful details. Evidence/status "
                "labels are internal metadata. For ordinary suggestions, use name/cuisine evidence when tags "
                "are missing, and combine decision-relevant gaps into one short practical note at the end.",
        "source": source,
    }, ensure_ascii=False)
