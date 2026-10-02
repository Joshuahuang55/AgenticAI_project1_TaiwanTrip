"""find_attractions (member B): sights, temples, museums, and trails from the Tourism Administration via TDX."""

import json
import re
from contextvars import ContextVar

from tools.tdx_client import city_choices, odata_quote, resolve_city, tdx_get

ATTRACTION_PATH = "tourism/service/odata/V2/Tourism/Attraction"
SERVICE_TIME_PATH = "tourism/service/odata/V2/Tourism/Attraction/ServiceTime"
FEE_PATH = "tourism/service/odata/V2/Tourism/AttractionFee"

# Official AttractionClassEnum (Tourism Data Standard V2.1, section 14), in English for the model.
CLASS_NAMES = {
    1: "Culture", 2: "Ecology", 3: "Heritage site", 4: "Temple/religious site", 5: "Art",
    6: "Shopping street/market", 7: "National park", 8: "National scenic area", 9: "Leisure farm",
    10: "Hot spring", 11: "Natural scenery", 12: "Recreation", 13: "Sports/trail", 14: "Tourist factory",
    15: "Metropolitan park", 16: "Forest recreation area", 17: "Plains forest park",
    18: "National nature park", 19: "Park", 20: "Theme park", 21: "Indigenous culture",
    22: "Hakka culture", 23: "Transport hub", 24: "Waterside (beach, lake, falls)",
    25: "Museum/gallery", 26: "Zoo/aquarium", 27: "Entertainment venue",
}
# ServiceStatusEnum: 0 permanently closed, 3 temporarily closed.
CLOSED_STATUS = {0, 3}
CLOSED_TEXT = {0: "permanently closed", 3: "temporarily closed"}

# TDX data is in Chinese. Map what a foreign traveler types to words the listings use; a place
# matches if its name or description contains any of them. Class codes are not used for matching:
# they are inconsistent between data sources (e.g. New Taipei museums are not tagged 25).
KEYWORDS = {
    "temple": ("廟", "宮", "寺"), "shrine": ("廟", "宮", "祠"), "church": ("教堂",),
    "museum": ("博物館", "美術館", "文物館", "紀念館"), "art": ("美術館", "藝術"), "gallery": ("美術館", "藝廊"),
    "history": ("古蹟", "歷史"), "heritage": ("古蹟",), "historic site": ("古蹟",), "culture": ("文化",),
    "hiking": ("步道", "登山"), "trail": ("步道",), "mountain": ("登山", "山區", "步道"),
    "nature": ("自然", "生態"), "forest": ("森林",), "national park": ("國家公園",), "waterfall": ("瀑布",),
    "lake": ("湖", "潭"), "beach": ("海灘", "沙灘", "海水浴場"), "hot spring": ("溫泉",), "old street": ("老街",),
    "shopping": ("商圈", "老街"), "market": ("市場", "市集"), "park": ("公園",), "garden": ("花園", "庭園"),
    "night view": ("夜景",), "view": ("觀景", "景觀"), "sunset": ("夕陽",), "farm": ("農場", "牧場"),
    "zoo": ("動物園",), "aquarium": ("水族館", "海生館"), "theme park": ("樂園",), "amusement park": ("樂園",),
    "factory": ("觀光工廠",), "aboriginal": ("原住民", "部落"), "indigenous": ("原住民", "部落"),
    "hakka": ("客家",), "bike": ("自行車",), "cycling": ("自行車",), "harbor": ("港",), "port": ("港",),
    "island": ("島",),
}
# Longer words that contain a search word but mean something else: 故宮 (the Palace Museum) is not a temple.
FALSE_MATCHES = {"宮": ("故宮",)}

for _plural in ("temples", "museums", "trails", "mountains", "waterfalls", "lakes", "beaches", "hot springs",
                "parks", "gardens", "farms", "markets"):
    KEYWORDS[_plural] = KEYWORDS[_plural[:-2] if _plural.endswith("hes") else _plural[:-1]]

MAX_ROWS = 500  # TDX rejects $top above 500 (HTTP 400).
# TDX returns rows in ID order (Tainan's first 50 are all rural north), so fetch the whole city up to
# the cap and let the model pick famous places from every district. Tainan has 545 listings.
RANK_POOL = MAX_ROWS
# session_id -> {AttractionName: full row} from that session's recent searches, so a place picked from
# more_candidates can be expanded (address, coordinates for a map pin) without another TDX call.
# Kept per session so one traveler's searches never show up on another traveler's map. In-process only.
_seen_by_session: dict[str, dict[str, dict]] = {}
_session: ContextVar[str] = ContextVar("attractions_session", default="")
MAX_SESSIONS = 200


def use_session(session_id: str) -> None:
    """Scope recent-search memory to this chat session. Call once per /chat request."""
    _session.set(session_id)


def forget_session(session_id: str) -> None:
    _seen_by_session.pop(session_id, None)


def _seen() -> dict[str, dict]:
    sid = _session.get()
    if sid not in _seen_by_session and len(_seen_by_session) >= MAX_SESSIONS:
        _seen_by_session.pop(next(iter(_seen_by_session)))  # drop the oldest session
    return _seen_by_session.setdefault(sid, {})

DAY_ABBR = {"Monday": "Mon", "Tuesday": "Tue", "Wednesday": "Wed", "Thursday": "Thu",
            "Friday": "Fri", "Saturday": "Sat", "Sunday": "Sun"}
WEEK = list(DAY_ABBR)
SPECIAL_DAYS = {"PublicHolidays": "public holidays", "DayBeforeHolidays": "day before holidays",
                "DayAfterHolidays": "day after holidays", "TyphoonDay": "typhoon days"}


def _days_text(days: list[str]) -> str:
    """['Monday', ..., 'Friday', 'PublicHolidays'] -> 'Mon-Fri, public holidays'; all seven -> 'Daily'."""
    idx = sorted({WEEK.index(d) for d in days if d in DAY_ABBR})
    special = [SPECIAL_DAYS[d] for d in days if d in SPECIAL_DAYS]
    if len(idx) == 7:
        return ", ".join(["Daily", *special])
    runs, start = [], None
    for i, d in enumerate(idx):
        if start is None:
            start = d
        if i + 1 == len(idx) or idx[i + 1] != d + 1:
            a, b = DAY_ABBR[WEEK[start]], DAY_ABBR[WEEK[d]]
            runs.append(a if start == d else f"{a}-{b}")
            start = None
    return ", ".join(runs + special)


def _open_time(periods: list[dict]) -> str | None:
    parts = []
    for p in periods:
        start, end = (p.get("StartTime") or "")[:5], (p.get("EndTime") or "")[:5]
        hours = f"{start}-{end}" if start and end else (p.get("Description") or p.get("Name") or "")
        days = _days_text(p.get("ServiceDays") or [])
        text = f"{days} {hours}".strip()
        if text:
            parts.append(text)
    return "; ".join(parts) or None


def _ticket_info(fees: list[dict]) -> str | None:
    parts = []
    has_paid = any((f.get("Price") or 0) > 0 for f in fees)
    for f in fees:
        name, price = (f.get("Name") or "").strip(), f.get("Price")
        if price == 0 or (price is None and name == "免費"):
            if name in ("", "免費"):
                parts.append("free for eligible visitors" if has_paid else "Free")
            else:
                parts.append(f"{name} free")
        elif price is not None:
            parts.append(f"{name} NT${price}".strip())
        elif name or f.get("Description"):
            parts.append(name or f.get("Description"))
    return "; ".join(dict.fromkeys(parts)) or None


def _lookup(path: str, field: str) -> dict[str, list]:
    """AttractionID -> ServiceTimes/Fees. Only a few sources publish these (~1% of attractions), so the
    whole endpoint fits in one cached call. Missing data never fails the search."""
    rows = tdx_get(path, {"$top": MAX_ROWS})
    if isinstance(rows, dict):
        return {}
    return {r["AttractionID"]: r.get(field) or [] for r in rows if r.get("AttractionID")}


def _summarize(r: dict, hours: dict, fees: dict, pin: bool = True) -> dict:
    addr = r.get("PostalAddress") or {}
    phones = [t["Tel"] for t in r.get("Telephones") or [] if t.get("Tel")]
    desc = (r.get("Description") or "").strip()
    aid = r.get("AttractionID")
    out = {
        "name": r.get("AttractionName"),
        "categories": [CLASS_NAMES[c] for c in r.get("AttractionClasses") or [] if c in CLASS_NAMES],
        "description": desc[:160] + ("…" if len(desc) > 160 else ""),
        "address": f"{addr.get('City', '')}{addr.get('Town', '')}{addr.get('StreetAddress', '')}",
        "open_time": _open_time(hours.get(aid, [])) or (r.get("ServiceTimeInfo") or "").strip() or None,
        "ticket_info": _ticket_info(fees.get(aid, [])) or (r.get("FeeInfo") or "").strip() or None,
        "phone": phones[0] if phones else None,
    }
    if pin:  # the frontend pins any result with lat/lon, so only the final picks carry them
        out.update(lat=r.get("PositionLat"), lon=r.get("PositionLon"))
    return out


def _search_words(kw: str) -> tuple[str, ...]:
    """'hiking' -> ('步道', '登山'); a phrase like 'mountain trails' merges its known words;
    anything else (e.g. Chinese) is searched as typed."""
    if not kw:
        return ()
    if kw.lower() in KEYWORDS:
        return KEYWORDS[kw.lower()]
    words = [w for part in kw.lower().split() for w in KEYWORDS.get(part, ())]
    return tuple(dict.fromkeys(words)) or (kw,)


def _has(text: str, word: str) -> bool:
    for longer in FALSE_MATCHES.get(word, ()):
        text = text.replace(longer, "")
    return word in text


def _rank(rows: list[dict], words: tuple[str, ...]) -> list[dict]:
    """Drop rows that only matched a false word (故宮 for 宮), then best first: keyword in the name,
    then well-documented listings (photo, real description)."""
    def text(r: dict) -> str:
        return f"{r.get('AttractionName') or ''} {r.get('Description') or ''}"
    if words:
        rows = [r for r in rows if any(_has(text(r), w) for w in words)]
    def score(r: dict) -> int:
        name = r.get("AttractionName") or ""
        return (2 * any(_has(name, w) for w in words) + bool(r.get("Images"))
                + (len(r.get("Description") or "") >= 80))
    return sorted(rows, key=score, reverse=True)  # stable: ties keep TDX order


def _spread(rows: list[dict], limit: int) -> list[dict]:
    """Take one place per group before seconds, so ten results are not all one trail network.
    Group = the series prefix TDX puts in names ('大屯山系_中正山步道' -> '大屯山系'), else the district."""
    def group(r: dict) -> str:
        name = r.get("AttractionName") or ""
        return name.split("_")[0] if "_" in name else (r.get("PostalAddress") or {}).get("Town", "")
    seen, first, rest = set(), [], []
    for r in rows:
        (rest if group(r) in seen else first).append(r)
        seen.add(group(r))
    return (first + rest)[:limit]


def _by_district(rows: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault((r.get("PostalAddress") or {}).get("Town") or "?", []).append(r.get("AttractionName"))
    return out


def _dedupe(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        addr = r.get("PostalAddress") or {}
        key = (r.get("AttractionName"), addr.get("Town"), addr.get("StreetAddress"))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


# Variant characters travelers and listings mix: 台/臺, 赤嵌樓/赤崁樓, 赤柯山/赤科山.
NAME_VARIANTS = str.maketrans({"台": "臺", "崁": "嵌", "柯": "科"})
MAX_NAME_MATCHES = 3


def _clean(name: str) -> str:
    return re.sub(r"[\s\-‧・·_()（）「」『』]", "", name or "").translate(NAME_VARIANTS)


def _in_order(short: str, long: str) -> bool:
    """Every character of `short` appears in `long`, in order: 士林夜市 fits %士%林%夜%市% in 士林觀光夜市."""
    chars = iter(long)
    return all(c in chars for c in short)


def _name_matches(name: str, rows: list[dict]) -> list[dict]:
    """Listings a picked name refers to: the exact name if listed, else every in-order match, closest
    length first. 臺北市孔廟 also finds 臺北孔廟 (the listing's characters in the name's order)."""
    q = _clean(name)
    exact = [r for r in rows if _clean(r.get("AttractionName")) == q]
    if exact:
        return exact[:1]
    out, seen = [], set()
    for r in rows:
        c = _clean(r.get("AttractionName"))
        if c and c not in seen and (_in_order(q, c) or (len(c) >= 3 and _in_order(c, q))):
            seen.add(c)
            out.append(r)
    return sorted(out, key=lambda r: abs(len(_clean(r["AttractionName"])) - len(q)))


def _details(county: str, names: list[str]) -> str:
    """Full listings for places the model picked by name, matched against the city's listings.

    Names need not be exact: 士林夜市 finds 士林觀光夜市. When a name fits several listings, all of them
    come back under possible_matches and the model picks the one the user means."""
    names = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
    names, not_checked = names[:10], names[10:]
    recent = [r for r in _seen().values() if (r.get("PostalAddress") or {}).get("City") == county]
    found = {n: _name_matches(n, recent) for n in names}  # usually picks from the last search: no TDX call
    closed_rows = []
    # Then the whole city (the query a keyword-less search makes, so the two share the cache), then a
    # direct query by name, since a city can list more than the 500 rows one query returns.
    for params in ({"$filter": f"PostalAddress/City eq '{county}'", "$top": RANK_POOL}, None):
        missing = [n for n, hits in found.items() if not hits]
        if not missing:
            break
        if params is None:
            match = " or ".join(f"contains(AttractionName,'{odata_quote(n)}')" for n in missing)
            params = {"$filter": f"PostalAddress/City eq '{county}' and ({match})", "$top": len(missing) * 3}
        rows = tdx_get(ATTRACTION_PATH, params)
        if isinstance(rows, dict):
            return json.dumps(rows, ensure_ascii=False)
        closed_rows += [r for r in rows if r.get("ServiceStatus") in CLOSED_STATUS]
        rows = [r for r in rows if r.get("ServiceStatus") not in CLOSED_STATUS]
        for n in missing:
            found[n] = _name_matches(n, rows)
    # A name that only matches closed listings is reported as closed, not as missing.
    closed = {n: [f"{r['AttractionName']} ({CLOSED_TEXT[r['ServiceStatus']]})"
                  for r in _name_matches(n, closed_rows)[:MAX_NAME_MATCHES]]
              for n, hits in found.items() if not hits}
    closed = {n: listed for n, listed in closed.items() if listed}
    too_many = {n: len(hits) for n, hits in found.items() if len(hits) > MAX_NAME_MATCHES}
    found = {n: hits[:MAX_NAME_MATCHES] for n, hits in found.items()}
    picked = []
    for hits in found.values():
        picked += [r for r in hits if r not in picked]
    # Remember them like search results, so the answer's picks get map pins.
    _seen().update({r["AttractionName"]: r for r in picked if r.get("AttractionName")})
    hours = _lookup(SERVICE_TIME_PATH, "ServiceTimes")
    fees = _lookup(FEE_PATH, "Fees")
    possible = {n: [r["AttractionName"] for r in hits] for n, hits in found.items()
                if hits and [r["AttractionName"] for r in hits] != [n]}
    out = {
        "city": county,
        "results": [_summarize(r, hours, fees) for r in picked],
        "not_found": [n for n, hits in found.items() if not hits and n not in closed],
        "source": "Taiwan Tourism Administration via TDX",
    }
    hints = []
    if closed:
        out["closed"] = closed
        hints.append("Places in closed are closed in the official listing: tell the user and do not recommend them.")
    if possible:
        out["possible_matches"] = possible
        hints.append("possible_matches maps your names to official listings with different names. Use the "
                     "official name, and where a name has several, recommend only the one the user means.")
    if too_many:
        out["too_many_matches"] = too_many
        hints.append(f"Names in too_many_matches fit more listings than the {MAX_NAME_MATCHES} shown: call again "
                     "with a more specific name (e.g. the full temple name) if the one you mean is missing.")
    if not_checked:
        out["not_checked"] = not_checked
        hints.append("Only 10 names are checked per call: call again with the names in not_checked.")
    if hints:
        out["hint"] = " ".join(hints)
    return json.dumps(out, ensure_ascii=False)


def pins_from_answer(answer: str) -> list[dict]:
    """Map pins for the places the final answer actually recommends: listings from this session's searches
    whose Chinese name (or the part after the series prefix, e.g. 象山親山步道) appears in the text.
    Needs no TDX call and no extra model step, so the map shows picks, not every candidate."""
    # Longest names first, and each match is blanked out, so 擎天崗 does not also match inside
    # 擎天崗系_坪頂古圳步道. Results keep the order the answer mentions them.
    seen = _seen()
    forms = sorted(((form, r) for name, r in seen.items()
                    for form in {name, name.rsplit("_", 1)[-1]} if len(form) >= 3),
                   key=lambda fr: len(fr[0]), reverse=True)
    found = []
    for form, r in forms:
        at = answer.find(form)
        if at >= 0:
            answer = answer.replace(form, "\0" * len(form))
            if all(r is not other for _, other in found):
                found.append((at, r))
    # Then shortened names in parentheses, e.g. (臺南孔子廟) for 孔廟文化園區「臺南孔子廟」.
    # "(安平老街 / 延平老街)" is tried part by part.
    for m in re.finditer(r"[(（]([^()（）]{3,40})[)）]", answer):
        for part in re.split(r"\s*[/／、,，]\s*", m.group(1).strip()):
            hits = [r for name, r in seen.items() if len(part) >= 3 and part in name]
            if len(hits) == 1 and all(hits[0] is not other for _, other in found):
                found.append((m.start(), hits[0]))
    picked = [r for _, r in sorted(found, key=lambda x: x[0])]
    return [_summarize(r, {}, {}) for r in picked]


def find_attractions(city: str, keyword: str | None = None, district: str | None = None, limit: int = 10,
                     names: list[str] | None = None) -> str:
    place = resolve_city(city)
    if place is None:
        return json.dumps({
            "error": f"Unknown city '{city}'.",
            "hint": "Use a Taiwan city or county name.",
            "valid_cities": city_choices(),
        })
    county, town = place
    if names:
        return _details(county, names)
    town = district or town
    limit = max(1, min(int(limit), 10))

    kw = (keyword or "").strip()
    words = _search_words(kw)
    # Filter on the TDX side: a big city (New Taipei) has more than the 500-row cap.
    filters = [f"PostalAddress/City eq '{county}'"]
    if town:
        filters.append(f"PostalAddress/Town eq '{odata_quote(town)}'")
    if words:
        match = " or ".join(
            f"contains(AttractionName,'{odata_quote(w)}') or contains(Description,'{odata_quote(w)}')"
            for w in words
        )
        filters.append(f"({match})")
    rows = tdx_get(ATTRACTION_PATH, {"$filter": " and ".join(filters), "$top": RANK_POOL})
    if isinstance(rows, dict):
        return json.dumps(rows, ensure_ascii=False)

    rows = [r for r in rows if r.get("ServiceStatus") not in CLOSED_STATUS]
    seen = _seen()
    if len(seen) > 5000:
        seen.clear()
    seen.update({r["AttractionName"]: r for r in rows if r.get("AttractionName")})
    ranked = _dedupe(_rank(rows, words))
    named = [r for r in ranked if not words or any(_has(r.get("AttractionName") or "", w) for w in words)]
    rows = _spread(named, limit)
    rows += _spread([r for r in ranked if r not in named], limit - len(rows))
    if not rows:
        hint = "No listed attractions match. "
        if kw and kw.isascii() and words == (kw,):
            hint += (f"Listings are in Chinese; retry with a Traditional Chinese keyword for '{kw}' "
                     "(e.g. 博物館, 廟, 步道) or a broader one.")
        elif district:
            hint += "Retry without `district` to search the whole city."
        else:
            hint += "Retry with a broader keyword, without a keyword, or in a nearby city."
        return json.dumps({"city": county, "keyword": kw, "results": [], "hint": hint}, ensure_ascii=False)

    hours = _lookup(SERVICE_TIME_PATH, "ServiceTimes")
    fees = _lookup(FEE_PATH, "Fees")
    searched_as = ", ".join(words)
    return json.dumps({
        "city": county,
        "keyword": kw or None,
        "searched_as": searched_as if searched_as != kw else None,
        "results": [_summarize(r, hours, fees, pin=False) for r in rows],
        # Names only, by district: lets the model spot famous places the ID-ordered picks missed.
        "more_candidates": _by_district([r for r in ranked if r not in rows]),
        "note": "Names and descriptions are in Chinese: translate them and keep the Chinese name so the user can "
                "show it to a taxi driver. open_time and ticket_info are the only source for hours and prices. "
                "When one is null, say the official listing does not include it and suggest checking the "
                "place's official site.",
        "source": "Taiwan Tourism Administration via TDX",
    }, ensure_ascii=False)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_attractions",
        "description": (
            "Find sights to visit in a Taiwan city from the Tourism Administration's listings: temples, "
            "museums, hiking trails, old streets, parks, beaches, and more. Use when the user asks what to "
            "see or do. Returns up to `limit` varied candidates (not ranked by popularity) with name "
            "(Chinese), categories, short description, address, opening hours, ticket prices, phone, and "
            "coordinates, plus `more_candidates`: names of other matching listings. Pick the best-known "
            "and best-fitting ones for the user."
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
                    "description": "Optional type of place, e.g. 'temple', 'museum', 'hiking', 'old street', "
                                   "'hot spring', or a Traditional Chinese term like '老街'. Omit for any.",
                },
                "district": {
                    "type": "string",
                    "description": "Only if the user names a district: the district in Traditional Chinese, "
                                   "e.g. '中西區', '大安區'. Otherwise omit.",
                },
                "limit": {"type": "integer", "description": "How many candidates (1-10, default 10)."},
                "names": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Only after a search: Chinese names from results or more_candidates "
                                   "that you need details for (address, hours, fees). Keep `city`.",
                },
            },
            "required": ["city"],
        },
    },
}
