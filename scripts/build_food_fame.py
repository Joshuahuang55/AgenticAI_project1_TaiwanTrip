"""Build tools/data/food_fame.json: fame scores for the OpenStreetMap food places, from three award lists.

Run from the repository root with `uv run python scripts/build_food_fame.py` (about 1 minute, no TDX).
Needs tools/data/osm_food.json.gz (scripts/build_osm_food.py).

Sources, all used for research and education only, not commercially:
  - Michelin Guide Taiwan (stars, Bib Gourmand, selected), via the michelin-my-maps project
    (https://github.com/ngshiheng/michelin-my-maps; its data is for research use only). Its names are
    English, so an LLM gives each its Chinese shop name once (data/food_awards/michelin_zh.json, reviewable);
    a place matches by that name, or by English name, within MICHELIN_KM.
  - 500盤 (2021-2026) and 500碗 (2023-2026) by 500輯 (udn), transcribed into data/food_awards/ (research use
    only; the lists are © 500輯). Matched by name within the listed city.

Two scores per place, as for attractions:
  - fame: the strongest award (Michelin, 500盤 or 500碗), so the default search leads with awarded places.
  - local: the strongest of 500盤 and 500碗 (Taiwanese critics' picks, from street stalls to fine dining)
    times (1 - Michelin), high for places locals rate that foreign guidebooks have not picked up.
Michelin restaurants OSM lacks become extra places (Chinese name from the LLM, Michelin's English name,
address and coordinates), so 500盤 and 500碗 entries can match them too.
Well-known places no award covers (阿宗麵線, 阜杭豆漿) come from data/food_awards/known_food.json (an LLM list
checked against OSM and reviewed, see scripts/list_known_food.py): fame at least KNOWN_FAME.
A place with an English name in OSM and no award gets a small fame (EN_NAME_FAME), except chains.
Chains (a name on CHAIN_MIN or more OSM places) keep their fame but get CHAIN_LOCAL of their local score.
"""

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OSM_PATH = ROOT / "tools" / "data" / "osm_food.json.gz"
OUTPUT = ROOT / "tools" / "data" / "food_fame.json"
BOWLS = ROOT / "data" / "food_awards" / "500bowls_2023_2026.csv"
DISHES = ROOT / "data" / "food_awards" / "500dishes_2021_2026.csv"
MICHELIN_URL = "https://raw.githubusercontent.com/ngshiheng/michelin-my-maps/main/data/michelin_my_maps.csv"
MICHELIN_FAME = {"3 Stars": 1.0, "2 Stars": 0.95, "1 Star": 0.9, "Bib Gourmand": 0.75, "Selected Restaurants": 0.6}
MICHELIN_KM = 0.3  # a matching name this close is the same restaurant (Michelin's points are rough)
MICHELIN_SAME_NAME_KM = 3  # an identical name this close is the same restaurant, mapped elsewhere
MICHELIN_ZH = ROOT / "data" / "food_awards" / "michelin_zh.json"
MODEL = "vertex_ai/qwen/qwen3-235b-a22b-instruct-2507-maas"
FULL_PLATES = 12  # total 500盤 plates over the years for fame 1 (top places get 20-40)
FULL_BOWLS = 6  # total 500碗 bowls over the years for fame 1 (top stalls get 6-9)
EN_NAME_FAME = 0.2
KNOWN_FAME = 0.7  # between Michelin Selected (0.6) and Bib Gourmand (0.75)
KNOWN = ROOT / "data" / "food_awards" / "known_food.json"
CHAIN_MIN = 5  # a name on this many OSM places is a chain
CHAIN_LOCAL = 0.3
# Awards for nameless stalls (無名米粉湯) cannot be told apart from others of that name.
NAMELESS = re.compile(r"無名|無店名|無招牌")
# 500輯 city names -> counties. Blank and joint cities (台北/新北) search each county listed.
CITY = {"台北": ["臺北市"], "新北": ["新北市"], "台中": ["臺中市"], "台南": ["臺南市"], "高雄": ["高雄市"],
        "基隆": ["基隆市"], "桃園": ["桃園市"], "新竹": ["新竹市", "新竹縣"], "苗栗": ["苗栗縣"], "彰化": ["彰化縣"],
        "南投": ["南投縣"], "雲林": ["雲林縣"], "嘉義": ["嘉義市", "嘉義縣"], "屏東": ["屏東縣"], "宜蘭": ["宜蘭縣"],
        "花蓮": ["花蓮縣"], "台東": ["臺東縣"], "澎湖": ["澎湖縣"], "金門": ["金門縣"], "連江": ["連江縣"],
        "馬祖": ["連江縣"]}
TAIWAN = re.compile(r",\s*Taiwan( Region)?$")  # the source has written both
MICHELIN_CITY = {"Taipei": "臺北市", "New Taipei": "新北市", "Taichung": "臺中市", "Tainan": "臺南市",
                 "Kaohsiung": "高雄市", "Hsinchu County": "新竹縣", "Hsinchu City": "新竹市"}
BRANCH = re.compile(r"(\w{2}(分店|店|門市)|總店|本店|創始店|旗艦店)$")
LAST_EDITION = 2026  # newest year of both 500 lists
# Names a place used in other years -> the name it uses now (500輯 groups years by exact name only).
ALIASES = {
    "牡丹天ぷら": "牡丹極上天ぷら", "晶華軒 Silks House": "晶華軒", "Silks House 晶華軒": "晶華軒",
    "頤宮中餐廳 Le Palais": "頤宮中餐廳", "君品頤宮": "頤宮中餐廳", "明福台菜海產": "明福台菜", "明福": "明福台菜",
    "朧粵 Longyue": "朧粵", "鄒記食鋪": "鄒記食舖", "台北喜來登大飯店 請客樓": "請客樓", "台北喜來登請客樓": "請客樓",
    "金蓬萊遵古台菜": "金蓬萊遵古台菜餐廳", "Amamoto 鮨天本": "鮨天本", "鮨天本 Amamoto": "鮨天本",
    "欣葉台菜": "欣葉台菜創始店", "欣葉台灣料理": "欣葉台菜創始店", "紅棉 Red Cotton": "紅棉", "盈科EIKA": "EIKA 盈科",
    "Adachi足立": "Adachi 足立壽司", "鳥苑 Torien Yakitori": "鳥苑", "鳥苑地雞燒 Yakitori&Wine": "鳥苑",
    "台北亞都麗緻大飯店天香樓": "天香樓", "台北亞都麗緻大飯店 巴賽麗廳": "巴賽麗廳", "巴賽麗廳 La Brasserie": "巴賽麗廳",
    "台北喜來登大飯店辰園": "辰園", "大地酒店.奇岩一號": "奇岩一號", "北投奇岩一號": "奇岩一號", "奇岩一號中餐廳": "奇岩一號",
    "D-Place 涼州游嚴行": "涼州游嚴行 D-Place", "La Vie by Thomas Bühner 睿麗餐廳": "La Vie by Thomas Bühner",
    "Ephernité 法緹": "Ephernité", "Orchid 蘭餐廳": "Orchid by Nobu Lee 蘭", "成海 寿司": "成海壽司",
    "成海 壽司 narumi sushi": "成海壽司", "捌伍添第85TD": "捌伍添第", "夜上海 Ye Shanghai (Taipei)": "夜上海",
    "夜上海 Ye Shanghai Taipei": "夜上海", "夜上海Ye Shanghai": "夜上海", "香色 XIANG SE": "香色", "香色 Xiang Se": "香色",
    "雋 GEN by Matt Chen": "雋 中餐廳 GEN", "雋 中餐廳 GEN by Matt Chen": "雋 中餐廳 GEN",
    "Restaurant 12, YMS by Onefifteen": "YMS by onefifteen - Restaurant 12",
}
# Closed for good, though OSM or older lists still have them. Award names, checked by hand.
CLOSED = {"RAW"}
LICENSE = ("Research and education use only. Michelin Guide data via michelin-my-maps (research use only); "
           "500盤 and 500碗 lists © 500輯 (udn). Places © OpenStreetMap contributors, ODbL 1.0.")


def norm(name: str) -> str:
    """Lowercase, no spaces or punctuation, 臺 and 滷 spellings: '名家魯肉飯' -> '名家滷肉飯'."""
    name = name.replace("台", "臺").replace("魯肉", "滷肉").replace("焿", "羹").lower()
    return re.sub(r"[\W_]", "", name)


def name_forms(name: str) -> set[str]:
    """The name without branch notes, plus its Chinese and Latin parts on their own:
    '晶華軒 Silks House' -> {'晶華軒silkshouse', '晶華軒', 'silkshouse'}.
    '橋頭臭豆腐（玉里店／礁溪店）' -> {'橋頭臭豆腐'}: one award for every branch."""
    base = re.sub(r"[（(].*?[）)]", "", name)
    base = re.split(r"\s*[-－–|｜]\s*(?=[^\s]*店$)", base)[0]  # 'X - 敦南本店' -> 'X'
    forms = {norm(base)}
    full = norm(base)
    if len(full) >= 6:  # '林家乾麵中正店' -> '林家乾麵'; short names ('老麵店') are left whole
        trimmed = BRANCH.sub("", full)
        if len(trimmed) >= 3:
            forms.add(trimmed)
    cjk = norm(re.sub(r"[A-Za-z0-9&'.\s-]+", "", base))
    latin = norm(re.sub(r"[^A-Za-z0-9\s]+", " ", base))
    forms |= {f for f in (cjk, latin) if len(f) >= 2}
    return {f for f in forms if f}


def names_match(award: set[str], place: set[str]) -> bool:
    """Same name, or one inside the other when the shorter has at least 4 characters and is most of the
    longer: '林家乾麵' and '林家乾麵中正店' match; '牛肉麵' and '阿銘牛肉麵' do not."""
    for a in award:
        for p in place:
            if a == p:
                return True
            short, long_ = sorted((a, p), key=len)
            if len(short) >= 4 and short in long_ and len(short) / len(long_) >= 0.7:
                return True
    return False


def km(lat1, lon1, lat2, lon2) -> float:
    dy = (lat1 - lat2) * 111.0
    dx = (lon1 - lon2) * 111.0 * math.cos(math.radians(lat1))
    return math.hypot(dx, dy)


def chains(places: list[dict]) -> set[str]:
    counts = Counter(norm(re.sub(r"[（(].*|\s.*$", "", p["n"])) for p in places)
    return {n for n, c in counts.items() if c >= CHAIN_MIN}


def award_totals(rows: list[dict], amount: str) -> list[dict]:
    """One entry per award name (renames joined via ALIASES) and city: total plates or bowls, years listed,
    every name form used. A row with no city joins its name's entry that has one."""
    aliases = {norm(k): v for k, v in ALIASES.items()}
    grouped: dict[tuple, dict] = {}
    for r in sorted(rows, key=lambda r: not r["city"]):  # rows with a city first
        name = aliases.get(norm(r["name"]), r["name"])
        canon = norm(re.sub(r"[（(].*?[）)]", "", name))
        key = (canon, r["city"])
        if not r["city"]:
            key = next((k for k in grouped if k[0] == canon), key)
        g = grouped.setdefault(key, {"name": name, "forms": set(), "city": key[1], "total": 0, "years": set()})
        g["forms"] |= name_forms(name) | name_forms(r["name"])
        g["total"] += int(r[amount])
        g["years"].add(int(r["year"]))
    return list(grouped.values())


def recency(years: set[int]) -> float:
    """1 if listed in either of the last two editions, less for awards only older lists give (the place
    may have changed or closed)."""
    gap = LAST_EDITION - max(years)
    return 1.0 if gap <= 1 else 0.7 if gap == 2 else 0.5


def match_awards(awards: list[dict], places_by_county: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """OSM id -> awards matched to it. A blank city searches every county."""
    out = defaultdict(list)
    for a in awards:
        if NAMELESS.search(a["name"]):
            continue
        cities = a["city"].split("/") if a["city"] else []
        counties = [c for city in cities for c in CITY.get(city, [])] or list(places_by_county)
        for county in counties:
            for p in places_by_county.get(county, []):
                if names_match(a["forms"], p["forms"]):
                    out[p["id"]].append(a)
    return out


def match_michelin(entries: list[dict], places: list[dict], zh: dict[str, str]) -> dict[str, dict]:
    """OSM id -> Michelin entry: the nearest place within MICHELIN_KM whose name matches the entry's
    Chinese name (zh: Michelin URL -> name) or whose English name matches the entry's; failing that, one
    with the identical name within MICHELIN_SAME_NAME_KM, or the county's only place with that name
    (OSM and Michelin put logy 5 km apart)."""
    out = {}
    by_form = defaultdict(list)
    for p in places:
        for f in p["forms"]:
            by_form[(p["city"], f)].append(p)
    box = MICHELIN_SAME_NAME_KM / 100
    for m in entries:
        lat, lon = float(m["Latitude"]), float(m["Longitude"])
        near = sorted(((km(lat, lon, p["lat"], p["lon"]), p) for p in places
                       if abs(p["lat"] - lat) < box and abs(p["lon"] - lon) < box), key=lambda x: x[0])
        mine = name_forms(zh.get(m["Url"]) or "") | name_forms(m["Name"])
        named = [p for d, p in near if d <= MICHELIN_KM and names_match(mine, p["forms"] | name_forms(p.get("en", "")))]
        same = [p for d, p in near if d <= MICHELIN_SAME_NAME_KM
                and (mine & (p["forms"] | name_forms(p.get("en", ""))) - {""})]
        city = MICHELIN_CITY.get(TAIWAN.sub("", m["Location"]))
        only = [ps[0] for f in mine if len(f) >= 3 and len(ps := by_form.get((city, f), [])) == 1]
        pick = named[0] if named else (same[0] if same else (only[0] if only else None))
        if pick and MICHELIN_FAME.get(m["Award"], 0) > MICHELIN_FAME.get(out.get(pick["id"], {}).get("Award"), 0):
            out[pick["id"]] = m
    return out


def michelin_extras(entries: list[dict], matched: dict[str, dict], zh: dict[str, str]) -> list[dict]:
    """Places, shaped like OSM ones, for Michelin restaurants no OSM place matched."""
    used = {m["Url"] for m in matched.values()}
    out = []
    for m in entries:
        city = MICHELIN_CITY.get(TAIWAN.sub("", m["Location"]))
        if m["Url"] in used or not city:
            continue
        slug = m["Url"].rstrip("/").rsplit("/", 1)[-1]
        out.append({"id": f"michelin:{slug}", "n": zh.get(m["Url"]) or m["Name"], "en": m["Name"],
                    "lat": round(float(m["Latitude"]), 6), "lon": round(float(m["Longitude"]), 6), "city": city,
                    "addr": m["Address"], "k": "restaurant", "url": m["Url"]})
    return out


def score(total: int, full: int) -> float:
    """0 to 1: log of plates or bowls, 1 at `full`."""
    return round(min(1.0, math.log1p(total) / math.log1p(full)), 3) if total > 0 else 0.0


def match_known(known: list[dict], places_by_county: dict[str, list[dict]]) -> dict[str, str]:
    """OSM id -> what a well-known place is known for, matched by name in its county."""
    from scripts.list_known_food import match
    out = {}
    for k in known:
        for p in match(k["name"], places_by_county.get(k["county"], [])):
            out[p["id"]] = k.get("known_for") or "well known"
    return out


def scores(places: list[dict], michelin: dict[str, dict], dishes: dict[str, list], bowls: dict[str, list],
           chain_names: set[str], known: dict[str, str] | None = None) -> dict[str, dict]:
    known = known or {}
    out = {}
    closed = {norm(n) for n in CLOSED}
    for p in places:
        pid = p["id"]
        m = MICHELIN_FAME.get(michelin.get(pid, {}).get("Award"), 0.0)
        d = max((score(a["total"], FULL_PLATES) * recency(a["years"]) for a in dishes.get(pid, [])), default=0.0)
        b = max((score(a["total"], FULL_BOWLS) * recency(a["years"]) for a in bowls.get(pid, [])), default=0.0)
        if any(norm(a["name"]) in closed for a in dishes.get(pid, []) + bowls.get(pid, [])):
            out[pid] = {"fame": 0.0, "local": 0.0, "closed": True}
            continue
        chain = norm(re.sub(r"[（(].*|\s.*$", "", p["n"])) in chain_names
        en = EN_NAME_FAME if p.get("en") and not chain else 0.0
        fame = max(m, d, b, en, KNOWN_FAME if pid in known else 0.0)
        if not fame:
            continue
        local = max(d, b) * (1 - m) * (CHAIN_LOCAL if chain else 1)
        entry = {"fame": round(fame, 3), "local": round(local, 3)}
        awards = []
        if pid in michelin:
            awards.append(f"Michelin Guide Taiwan: {michelin[pid]['Award']}")
            entry["name_en"] = michelin[pid]["Name"]
            if michelin[pid].get("Price"):
                entry["price"] = michelin[pid]["Price"]
        price = len(michelin.get(pid, {}).get("Price") or "")
        # Fine dining: a Michelin star or $$$+, or a 500盤 place (sit-down, skewed high-end) that neither
        # 500碗 nor a Michelin $-$$ price marks as everyday food.
        if ("Star" in michelin.get(pid, {}).get("Award", "") or price >= 3
                or (dishes.get(pid) and not bowls.get(pid) and not 1 <= price <= 2)):
            entry["fine_dining"] = True
        for label, found, unit in (("500盤", dishes, "plates"), ("500碗", bowls, "bowls")):
            for a in found.get(pid, [])[:1]:
                years = sorted(a["years"])
                awards.append(f"{label} {years[0]}-{years[-1]}" if len(years) > 1 else f"{label} {years[0]}")
        if awards:
            entry["awards"] = awards
        if pid in known:
            entry["known_for"] = known[pid]
        out[pid] = entry
    return out


def load_michelin(session) -> list[dict]:
    resp = session.get(MICHELIN_URL, timeout=120)
    resp.raise_for_status()
    return [r for r in csv.DictReader(io.StringIO(resp.text)) if TAIWAN.search(r["Location"])]


def chinese_names(entries: list[dict]) -> dict[str, str]:
    """Michelin URL -> Chinese shop name, asked of an LLM once and kept in MICHELIN_ZH for review."""
    known = json.loads(MICHELIN_ZH.read_text(encoding="utf-8")) if MICHELIN_ZH.exists() else {}
    todo = [m for m in entries if m["Url"] not in known]
    if todo:
        import litellm
        import app  # noqa: F401  loads .env (Google Cloud project) like the app does
        for i in range(0, len(todo), 40):
            batch = todo[i:i + 40]
            lines = "\n".join(f"{n}. {m['Name']} | {m['Address']}" for n, m in enumerate(batch))
            resp = litellm.completion(model=MODEL, vertex_location="global", temperature=0, messages=[{
                "role": "user", "content": "These are Michelin Guide Taiwan restaurants (English name | address). "
                "Give each one's Chinese shop name as written on its sign, in Traditional Chinese. Use an empty "
                "string when you are not sure. Reply with a JSON object mapping each number to the name.\n\n" + lines}])
            match = re.search(r"\{.*\}", resp.choices[0].message.content, re.S)
            got = json.loads(match.group(0)) if match else {}
            for n, m in enumerate(batch):
                known[m["Url"]] = str(got.get(str(n), "")).strip()
        MICHELIN_ZH.write_text(json.dumps(dict(sorted(known.items())), ensure_ascii=False, indent=0) + "\n",
                               encoding="utf-8")
    return known


def load_awards(path: Path, name_col: str, amount: str) -> list[dict]:
    with path.open(encoding="utf-8-sig") as f:
        rows = [dict(r, name=r[name_col]) for r in csv.DictReader(f)]
    return award_totals(rows, amount)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    with gzip.open(OSM_PATH, "rt", encoding="utf-8") as f:
        places = json.load(f)["places"]
    for p in places:
        p["forms"] = name_forms(p["n"])
    by_county = defaultdict(list)
    for p in places:
        by_county[p["city"]].append(p)

    session = requests.Session()
    session.headers.update({"User-Agent": "TaiwanLikeALocal/1.0 (research)"})
    michelin_rows = load_michelin(session)
    zh = chinese_names(michelin_rows)
    michelin = match_michelin(michelin_rows, places, zh)
    matched_osm = len(michelin)
    extras = michelin_extras(michelin_rows, michelin, zh)
    from scripts.build_osm_food import assign_towns, official_rows
    assign_towns(extras, official_rows())  # their English addresses give no Chinese district
    by_url = {m["Url"]: m for m in michelin_rows}
    for e in extras:
        e["forms"] = name_forms(e["n"])
        michelin[e["id"]] = by_url[e["url"]]
        by_county[e["city"]].append(e)
    places += extras
    dish_awards = load_awards(DISHES, "restaurant", "plates")
    bowl_awards = load_awards(BOWLS, "stall", "bowls")
    dishes = match_awards(dish_awards, by_county)
    bowls = match_awards(bowl_awards, by_county)
    known_list = json.loads(KNOWN.read_text(encoding="utf-8"))["places"] if KNOWN.exists() else []
    known = match_known(known_list, by_county)
    result = scores(places, michelin, dishes, bowls, chains(places), known)
    print(f"Well-known list: {len(known_list)} places, {len(known)} OSM places matched")
    print(f"Michelin: {len(michelin_rows)} Taiwan entries, {matched_osm} matched to OSM places, "
          f"{len(extras)} added as extra places")
    for label, awards, found in (("500盤", dish_awards, dishes), ("500碗", bowl_awards, bowls)):
        hit = {id(a) for lst in found.values() for a in lst}
        print(f"{label}: {sum(id(a) in hit for a in awards)} of {len(awards)} matched")
    args.output.write_text(json.dumps({
        "generated": dt.date.today().isoformat(),
        "license": LICENSE,
        "places": dict(sorted(result.items())),
        "extras": [{k: v for k, v in e.items() if k not in ("forms", "url")} for e in extras],
    }, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"Wrote {len(result)} scored places to {args.output}")


if __name__ == "__main__":
    main()
