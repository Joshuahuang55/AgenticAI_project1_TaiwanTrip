"""find_local_food (member C): restaurants and night markets from the Tourism Administration via TDX."""

import json

from tools.tdx_client import city_choices, odata_quote, resolve_city, tdx_get

RESTAURANT_PATH = "tourism/service/odata/V2/Tourism/Restaurant"
ATTRACTION_PATH = "tourism/service/odata/V2/Tourism/Attraction"

# TDX data is in Chinese. Map what a foreign traveler types to what the listings say.
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
NIGHT_MARKET_WORDS = {"night market", "night markets", "夜市"}

# Local knowledge: rotating night markets the TDX register does not list, and when they open.
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


def _summarize(r: dict, name_key: str) -> dict:
    addr = r.get("PostalAddress") or {}
    phones = [t["Tel"] for t in r.get("Telephones") or [] if t.get("Tel")]
    desc = (r.get("Description") or "").strip()
    return {
        "name": r.get(name_key),
        "description": desc[:160] + ("…" if len(desc) > 160 else ""),
        "address": f"{addr.get('City', '')}{addr.get('Town', '')}{addr.get('StreetAddress', '')}",
        "open_time": r.get("ServiceTimeInfo") or None,
        "phone": phones[0] if phones else None,
        "lat": r.get("PositionLat"),
        "lon": r.get("PositionLon"),
    }


def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        key = (it["name"], it["address"])
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _night_markets(county: str, town: str | None, limit: int) -> str:
    filters = [f"PostalAddress/City eq '{county}'", "contains(AttractionName,'夜市')"]
    if town:
        filters.append(f"PostalAddress/Town eq '{odata_quote(town)}'")
    rows = tdx_get(ATTRACTION_PATH, {"$filter": " and ".join(filters), "$top": limit * 2})
    if isinstance(rows, dict):
        return json.dumps(rows, ensure_ascii=False)
    markets = _dedupe([_summarize(r, "AttractionName") for r in rows])[:limit]
    local = LOCAL_NIGHT_MARKETS.get(county, [])
    if not markets and not local:
        return json.dumps({
            "city": county,
            "results": [],
            "hint": "No night markets are registered for this city. Suggest a nearby big city "
                    "(Taipei, Taichung, Tainan, Kaohsiung) or search food with another keyword.",
        }, ensure_ascii=False)
    return json.dumps({
        "city": county,
        "kind": "night_market",
        "results": markets,
        "local_tips": local,
        "note": "Most night markets open around 17:00-24:00. Rotating markets only open on the listed days.",
        "source": "Taiwan Tourism Administration via TDX" + (", plus local knowledge" if local else ""),
    }, ensure_ascii=False)


def find_local_food(city: str, keyword: str | None = None, district: str | None = None, limit: int = 5) -> str:
    place = resolve_city(city)
    if place is None:
        return json.dumps({
            "error": f"Unknown city '{city}'.",
            "hint": "Use a Taiwan city or county name.",
            "valid_cities": city_choices(),
        })
    county, town = place
    town = district or town
    limit = max(1, min(int(limit), 10))

    kw = (keyword or "").strip()
    if kw.lower() in NIGHT_MARKET_WORDS:
        return _night_markets(county, town, limit)

    term = KEYWORDS_ZH.get(kw.lower(), kw)
    filters = [f"PostalAddress/City eq '{county}'"]
    if town:
        filters.append(f"PostalAddress/Town eq '{odata_quote(town)}'")
    if term:
        q = odata_quote(term)
        filters.append(f"(contains(RestaurantName,'{q}') or contains(Description,'{q}'))")
    rows = tdx_get(RESTAURANT_PATH, {"$filter": " and ".join(filters), "$top": limit * 3})
    if isinstance(rows, dict):
        return json.dumps(rows, ensure_ascii=False)

    results = _dedupe([_summarize(r, "RestaurantName") for r in rows])[:limit]
    if not results:
        hint = "No listed restaurants match. "
        if term and term.isascii():
            hint += (f"Listings are in Chinese; retry with a Traditional Chinese keyword for '{kw}' "
                     "(e.g. 牛肉麵, 小吃, 海鮮) or a broader one.")
        elif district:
            hint += "Retry without `district` to search the whole city."
        else:
            hint += "Retry with a broader keyword, or without a keyword."
        return json.dumps({"city": county, "keyword": kw, "results": [], "hint": hint}, ensure_ascii=False)

    return json.dumps({
        "city": county,
        "keyword": kw or None,
        "searched_as": term if term != kw else None,
        "results": results,
        "note": "Names and descriptions are in Chinese: translate them for the user and keep the "
                "Chinese name so they can show it to a taxi driver.",
        "source": "Taiwan Tourism Administration via TDX",
    }, ensure_ascii=False)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_local_food",
        "description": (
            "Find restaurants, local specialties, or night markets in a Taiwan city from the Tourism "
            "Administration's listings. Use when the user asks where or what to eat. Returns name "
            "(Chinese), short description, address, opening hours, phone, and coordinates. For night "
            "markets, pass keyword 'night market' to also get which days rotating markets open."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Taiwan city/county in English, e.g. 'Tainan', 'Taipei', 'Hualien'. "
                                   "Famous spots also work: 'Jiufen', 'Kenting'.",
                },
                "keyword": {
                    "type": "string",
                    "description": "Optional dish or type, e.g. 'beef noodle', 'vegetarian', 'shaved ice', "
                                   "'night market', or a Traditional Chinese term like '牛肉湯'. Omit for any.",
                },
                "district": {
                    "type": "string",
                    "description": "Optional district in Traditional Chinese to narrow the area, e.g. '中西區', '大安區'.",
                },
                "limit": {"type": "integer", "description": "How many places (1-10, default 5)."},
            },
            "required": ["city"],
        },
    },
}
