"""Build tools/data/attraction_fame.json (how well known each attraction is) and
tools/data/extra_attractions.json (sights the official register lacks, from Wikidata).

Run from the repository root with `uv run python scripts/build_fame.py` (about 20 minutes, no TDX).
For every listing in the Tourism Administration's daily file, find its Wikidata item (similar name,
nearby coordinates), then score three signals, each as a percentile within the county so that
less-documented regions are not ranked below Taipei by default:
  - zh.wikipedia pageviews over the last 12 months
  - Wikipedia language editions with an article (Wikidata sitelinks)
  - zh.wikipedia article length
Fame is their mean, halved for campuses, stations and airports: Wikipedia covers them well, but most
are not sights, and the model can still pick a famous one (勝興車站) from the other listings.
A trail matched to its mountain's item scores only by en.wikipedia pageviews of that mountain (see
TRAIL). Listings without a Wikidata item are left out (fame 0).

Two more scores per listing: English fame (en.wikipedia views, see EN_FAME_MIN), and local fame, high
for places well known in Chinese but little read about in English (see local_scores).

Extra attractions: Wikidata items with a zh.wikipedia article that match no official listing, whose
name says they are a sight (temple, night market, old street, lake, trail, waterfall, beach, island,
lighthouse, museum...) and that are not closed. An item matching a closed listing (砂卡礑步道, closed with
Taroko) is closed too: Wikidata does not say so, the register does. Each takes the county and district
of the nearest official listing, and is scored like the listings it is ranked with.

Sources: Wikidata (CC0) and Wikimedia pageviews and page info (public, CC0).
"""

import argparse
import datetime as dt
import json
import math
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, unquote

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import tourism_data  # noqa: E402
from tools.attractions import NAME_VARIANTS, _in_order  # noqa: E402

OUTPUT = ROOT / "tools" / "data" / "attraction_fame.json"
EXTRA_OUTPUT = ROOT / "tools" / "data" / "extra_attractions.json"
HEADERS = {"User-Agent": "TaiwanLikeALocal/1.0 (https://github.com/Joshuahuang55/AgenticAI_project1_TaiwanTrip)"}
# Ecology, parks, scenery, trails, waterside: large areas whose listed point can sit far from the item's.
LARGE_AREA_CLASSES = {2, 7, 8, 11, 13, 16, 17, 18, 24}
# Villages and townships (案山里, 大埔鄉): their names sit inside many listings' names (案山里˙平安寶塔),
# so they never stand for a listing. Scenic areas and parks (三仙台風景區, 南科園區) are kept.
ADMIN_AREA = re.compile(r"^.{1,3}(里|村|鄉|鎮|(?<![園景憩特])區)$")
# A mountain's zh article says nothing about its trail's popularity with travelers: 象山 (a top Taipei
# hike) and 天上山 (rarely hiked) get about 1,000 zh views a year each. Its en article does: about 11,000
# for 象山, and 天上山 has none. So a trail matched to a mountain (not a trail item) scores by en views alone,
# on an absolute scale: nearly every listing has no en views, so a county percentile ranks any view high.
TRAIL = re.compile(r"步道|古道|登山")
MIN_EN_VIEWS = 1000  # a year; below this a mountain gives its trail no score
FULL_EN_VIEWS = 20000  # a year: fame 1
MOUNTAIN_NAME = re.compile(r"[山峰岳]$")
# Names that say an item is a sight, and names that say it is not (checked first). Bare peaks are left
# out: Taiwan has thousands, and the register lists their trails.
SIGHT_NAME = re.compile(r"(寺|宮|廟|殿|祠|教堂|天主堂|夜市|老街|商圈|湖|潭|埤|步道|古道|瀑布|海灘|沙灘|海水浴場|島|嶼|"
                        r"燈塔|砲台|炮台|古厝|故居|博物館|美術館|紀念館|文學館|公園|森林遊樂區|溫泉|峽谷|地質公園|濕地|"
                        r"觀景台|漁港|牧場|農場|園區|特區|洞|塔|動物園|樂園|鼻|岬|藝術村|書院|碼頭)$")
NOT_SIGHT_NAME = re.compile(r"(學校|國小|國中|高中|高級中學|大學|學院|醫院|車站|站|公司|大樓|路|街道|線|機場|港口|發電廠|"
                            r"工廠|監獄|軍營|基地|墓|公所|辦公室)$")
# English fame: en.wikipedia views a year, on an absolute log scale from EN_FAME_MIN (0) to EN_FAME_FULL (1).
# Absolute, not per county: it measures what foreign travelers have heard of, and few listings have any.
EN_FAME_MIN = 100
EN_FAME_FULL = 50000

# Well covered by Wikipedia, rarely what a traveler means by a sight.
NOT_USUALLY_SIGHTS = re.compile(r"大學|車站|機場|科學園區")

SPARQL = """
SELECT ?item ?coord ?sitelinks ?zhwiki ?enwiki ?closed ?label WHERE {
  SERVICE wikibase:box { ?item wdt:P625 ?coord .
    bd:serviceParam wikibase:cornerSouthWest "Point(%f %f)"^^geo:wktLiteral ;
                    wikibase:cornerNorthEast "Point(%f %f)"^^geo:wktLiteral . }
  ?item wikibase:sitelinks ?sitelinks .
  FILTER(?sitelinks > 0)
  OPTIONAL { ?zhwiki schema:about ?item ; schema:isPartOf <https://zh.wikipedia.org/> }
  OPTIONAL { ?enwiki schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> }
  OPTIONAL { ?item wdt:P576|wdt:P3999 ?closed }
  ?item rdfs:label ?label FILTER(lang(?label) IN ("zh-tw", "zh-hant", "zh"))
}"""


# --- Matching a listing to a Wikidata item ---

def _norm(name: str) -> str:
    name = re.sub(r"[（(「『].*?[)）」』]", "", name or "").translate(NAME_VARIANTS)
    return re.sub(r"[\s\-‧・·,，、_:：]", "", name)


def name_forms(name: str) -> set[str]:
    """臺灣祀典武廟 -> its normalized forms, plus series parts (南港山系_象山親山步道) and bracketed names.
    A trail drops its range part (南港山系): the range is not the trail, and would match 南港山."""
    out = {_norm(name)}
    if "_" in (name or ""):
        parts = name.split("_")
        if TRAIL.search(name):
            parts = [p for p in parts if not p.endswith("山系")]
            out = {_norm("".join(parts))}
        out |= {_norm(p) for p in parts}
    out |= {_norm(i) for i in re.findall(r"[（(「『](.*?)[)）」』]", name or "")}
    return {f for f in out if len(f) >= 2}


def similarity(a: set[str], b: set[str]) -> float:
    """1 for the same name; otherwise the best of containment, the in-order rule find_attractions uses
    for names (多良車站 fits 多良火車站), and shared character pairs, scaled by how close the lengths are."""
    best = 0.0
    for x in a:
        for y in b:
            if x == y:
                return 1.0
            short, long = sorted((x, y), key=len)
            if len(short) >= 3 and _in_order(short, long):
                best = max(best, 0.6 + 0.3 * len(short) / len(long))
            if len(x) >= 3 and len(y) >= 3 and (x in y or y in x):
                best = max(best, 0.9 * min(len(x), len(y)) / max(len(x), len(y)) + 0.1)
            bx = {x[i:i + 2] for i in range(len(x) - 1)} or {x}
            by = {y[i:i + 2] for i in range(len(y) - 1)} or {y}
            best = max(best, 2 * len(bx & by) / (len(bx) + len(by)))
    return best


def km(lat1, lon1, lat2, lon2) -> float:
    dy = (lat1 - lat2) * 111.0
    dx = (lon1 - lon2) * 111.0 * math.cos(math.radians(lat1))
    return math.hypot(dx, dy)


def best_item(row: dict, items: list[dict]) -> dict | None:
    """The Wikidata item a listing describes: name similarity of 0.6 or more within 600 m (3 km for parks,
    trails and scenery), best similarity first, then nearest."""
    if not row.get("PositionLat") or not row.get("PositionLon"):
        return None
    radius = 3.0 if set(row.get("AttractionClasses") or []) & LARGE_AREA_CLASSES else 0.6
    mine = name_forms(row.get("AttractionName"))
    for alt in row.get("AlternateNames") or []:
        mine |= name_forms(alt)
    best = None
    trail = bool(TRAIL.search(row.get("AttractionName") or ""))
    for item in items:
        if all(ADMIN_AREA.match(label) for label in item["labels"]):
            continue
        d = km(row["PositionLat"], row["PositionLon"], item["lat"], item["lon"])
        if d > radius:
            continue
        theirs = {f for label in item["labels"] for f in name_forms(label)}
        s = similarity(mine, theirs)
        # A trail named after a two-character mountain: 象山 for 象山親山步道 (scored by en views only).
        if trail and any(len(t) == 2 and MOUNTAIN_NAME.search(t) and f.startswith(t) for f in mine for t in theirs):
            s = max(s, 0.7)
        if s >= 0.6 and (best is None or (s, -d) > (best[0], -best[1])):
            best = (s, d, item)
    return best[2] if best else None


def via_mountain(row: dict, item: dict) -> bool:
    """A trail matched to its mountain (or another non-trail item) rather than to a trail item."""
    return bool(TRAIL.search(row.get("AttractionName") or "")) and not any(TRAIL.search(l) for l in item["labels"])


def percentiles(values: list[float]) -> list[float]:
    """Share of values below each value, ties counted half: 0 to 1 within the list."""
    order = sorted(values)
    out = []
    for v in values:
        below = next((i for i, x in enumerate(order) if x >= v), len(order))
        equal = sum(1 for x in order if x == v)
        out.append((below + 0.5 * equal) / len(order))
    return out


def fame_scores(rows_by_county: dict[str, list[dict]], signals: dict[str, dict]) -> dict[str, float]:
    """AttractionID -> mean of the county percentiles of views, log sitelinks and article length.
    A trail scored via its mountain (signals {"via_mountain": True, "en_views": n}) gets log(views) on a
    scale where FULL_EN_VIEWS is 1, and no score below MIN_EN_VIEWS. Only matched listings get a score;
    percentiles still count unmatched ones as zeros."""
    scores = {}
    for county, rows in rows_by_county.items():
        sig = [signals.get(r["AttractionID"]) or {} for r in rows]
        columns = [percentiles([s.get(k, 0) for s in sig]) for k in ("views", "sitelinks", "length")]
        for r, s, *pcts in zip(rows, sig, *columns):
            if s.get("via_mountain"):
                if s.get("en_views", 0) >= MIN_EN_VIEWS:
                    scores[r["AttractionID"]] = round(min(1.0, math.log10(s["en_views"]) / math.log10(FULL_EN_VIEWS)), 3)
            elif s:
                fame = sum(pcts) / 3
                if NOT_USUALLY_SIGHTS.search(r.get("AttractionName") or ""):
                    fame /= 2
                scores[r["AttractionID"]] = round(fame, 3)
    return scores


def en_fame(views: int) -> float:
    """0 to 1: log of a year's en.wikipedia views between EN_FAME_MIN and EN_FAME_FULL."""
    if views <= EN_FAME_MIN:
        return 0.0
    return round(min(1.0, math.log10(views / EN_FAME_MIN) / math.log10(EN_FAME_FULL / EN_FAME_MIN)), 3)


def local_scores(fame: dict[str, float], en: dict[str, float]) -> dict[str, float]:
    """Known at home, little known abroad: fame (zh-based) times (1 - English fame). A place locals
    write and read about that few English readers look up scores high; a world-famous one or an
    unknown one scores low."""
    out = {aid: round(f * (1 - en.get(aid, 0.0)), 3) for aid, f in fame.items()}
    return {aid: v for aid, v in out.items() if v > 0}


def extra_name(item: dict) -> str:
    """The zh.wikipedia title without its disambiguation: 琵琶湖 (台灣) -> 琵琶湖."""
    return re.sub(r"\s*[(（].*?[)）]$", "", item["zhwiki"] or "").strip()


def is_extra_sight(item: dict) -> bool:
    name = extra_name(item)
    return (bool(item["zhwiki"]) and not item["closed"] and len(name) >= 2 and not NOT_SIGHT_NAME.search(name)
            and not ADMIN_AREA.match(name) and bool(SIGHT_NAME.search(name)))


def extra_rows(items: list[dict], rows: list[dict], closed_rows: list[dict] = ()) -> list[dict]:
    """Rows, shaped like official listings, for sights the register lacks. County and district come from
    the nearest open official listing (within 10 km; the district only within 2 km). Items named like a
    listing within 1 km, open or closed, are taken as that listing under another name and dropped."""
    located = [r for r in rows if r.get("PositionLat") and r.get("PositionLon")]
    closed = [r for r in closed_rows if r.get("PositionLat") and r.get("PositionLon")]
    out = []
    for item in items:
        if not is_extra_sight(item):
            continue
        near = sorted(((km(item["lat"], item["lon"], r["PositionLat"], r["PositionLon"]), r) for r in located),
                      key=lambda x: x[0])
        if not near or near[0][0] > 10:
            continue
        mine = name_forms(extra_name(item))
        near_closed = [(km(item["lat"], item["lon"], r["PositionLat"], r["PositionLon"]), r) for r in closed]
        if any(d <= 1 and similarity(mine, name_forms(r["AttractionName"])) >= 0.7 for d, r in near[:20] + near_closed):
            continue
        d, nearest = near[0]
        addr = nearest.get("PostalAddress") or {}
        out.append({
            "AttractionID": f"wikidata:{item['qid']}", "AttractionName": extra_name(item),
            "PositionLat": item["lat"], "PositionLon": item["lon"], "AttractionClasses": [], "ServiceStatus": 1,
            "PostalAddress": {"City": addr.get("City"), "Town": addr.get("Town") if d <= 2 else None},
            "Description": "", "Source": "Wikidata",
        })
    return out


# --- Network ---

def _get(session, url, **kwargs):
    """GET with retries on rate limits, server errors, and truncated JSON bodies."""
    for attempt in range(6):
        resp = session.get(url, timeout=120, **kwargs)
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        if resp.ok:
            try:
                resp.json()
            except ValueError:  # a cut-off response
                time.sleep(5 * (attempt + 1))
                continue
        return resp
    resp.raise_for_status()
    return resp


def wikidata_items(session, rows: list[dict]) -> list[dict]:
    lats = [r["PositionLat"] for r in rows if r.get("PositionLat")]
    lons = [r["PositionLon"] for r in rows if r.get("PositionLon")]
    if not lats:
        return []
    box = (min(lons) - 0.02, min(lats) - 0.02, max(lons) + 0.02, max(lats) + 0.02)
    resp = _get(session, "https://query.wikidata.org/sparql", params={"query": SPARQL % box},
                headers={"Accept": "application/sparql-results+json"})
    resp.raise_for_status()
    items = {}
    for b in resp.json()["results"]["bindings"]:
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        lon, lat = b["coord"]["value"].removeprefix("Point(").rstrip(")").split()
        it = items.setdefault(qid, {"qid": qid, "lat": float(lat), "lon": float(lon), "labels": set(),
                                    "sitelinks": int(b["sitelinks"]["value"]), "zhwiki": None, "enwiki": None,
                                    "closed": False})
        it["closed"] = it["closed"] or "closed" in b
        it["labels"].add(b["label"]["value"])
        for lang in ("zhwiki", "enwiki"):
            if lang in b:
                it[lang] = unquote(b[lang]["value"].rsplit("/wiki/", 1)[1]).replace("_", " ")
    return list(items.values())


def article_lengths(session, titles: list[str]) -> dict[str, int]:
    out = {}
    for i in range(0, len(titles), 50):
        body = _get(session, "https://zh.wikipedia.org/w/api.php", params={
            "action": "query", "prop": "info", "titles": "|".join(titles[i:i + 50]), "redirects": 1,
            "format": "json", "formatversion": 2}).json()["query"]
        back = {n["to"]: n["from"] for n in body.get("normalized", []) + body.get("redirects", [])}
        for page in body["pages"]:
            out[back.get(page["title"], page["title"])] = page.get("length", 0)
        time.sleep(0.5)
    return out


def pageviews(session, title: str, start: dt.date, end: dt.date, lang: str = "zh") -> int:
    url = (f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{lang}.wikipedia/all-access/user/"
           f"{quote(title.replace(' ', '_'), safe='')}/monthly/{start:%Y%m}0100/{end:%Y%m}0100")
    resp = _get(session, url)
    if resp.status_code == 404:  # no views recorded
        return 0
    resp.raise_for_status()
    return sum(i["views"] for i in resp.json().get("items", []))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--extra-output", type=Path, default=EXTRA_OUTPUT)
    args = parser.parse_args()

    print("Downloading the official attraction file...")
    all_rows = tourism_data._download("attractions")["rows"]
    rows = [r for r in all_rows if r.get("ServiceStatus") not in (0, 3)]
    closed_rows = [r for r in all_rows if r.get("ServiceStatus") in (0, 3)]
    by_county = defaultdict(list)
    for r in rows:
        by_county[(r.get("PostalAddress") or {}).get("City")].append(r)
    by_county.pop(None, None)

    session = requests.Session()
    session.headers.update(HEADERS)
    matched, mountain, all_items, closed_qids = {}, set(), {}, set()
    for county, county_rows in sorted(by_county.items()):
        items = wikidata_items(session, county_rows)
        all_items.update({it["qid"]: it for it in items})
        for r in closed_rows:
            if (r.get("PostalAddress") or {}).get("City") == county and (item := best_item(r, items)):
                closed_qids.add(item["qid"])
        for r in county_rows:
            item = best_item(r, items)
            if item:
                matched[r["AttractionID"]] = item
                if via_mountain(r, item):
                    mountain.add(r["AttractionID"])
        print(f"{county}: {len(county_rows)} listings, {len(items)} Wikidata items, "
              f"{sum(r['AttractionID'] in matched for r in county_rows)} matched")
        time.sleep(2)

    matched_qids = {it["qid"] for it in matched.values()} | closed_qids
    extras = extra_rows([it for q, it in all_items.items() if q not in matched_qids], rows, closed_rows)
    for r in extras:
        by_county[r["PostalAddress"]["City"]].append(r)
        matched[r["AttractionID"]] = all_items[r["AttractionID"].removeprefix("wikidata:")]
    print(f"{len(extras)} extra sights from Wikidata")

    titles = sorted({it["zhwiki"] for aid, it in matched.items() if it["zhwiki"] and aid not in mountain})
    print(f"Fetching page info and 12-month views for {len(titles)} zh.wikipedia articles...")
    lengths = article_lengths(session, titles)
    end = dt.date.today().replace(day=1) - dt.timedelta(days=1)
    start = (end.replace(day=1) - dt.timedelta(days=330)).replace(day=1)
    views = {}
    for n, title in enumerate(titles):
        views[title] = pageviews(session, title, start, end)
        if n % 100 == 0:
            print(f"  {n}/{len(titles)}")
        time.sleep(0.1)
    en_titles = sorted({it["enwiki"] for it in matched.values() if it["enwiki"]})
    print(f"Fetching 12-month views for {len(en_titles)} en.wikipedia articles...")
    en_views = {}
    for n, title in enumerate(en_titles):
        en_views[title] = pageviews(session, title, start, end, lang="en")
        if n % 100 == 0:
            print(f"  {n}/{len(en_titles)}")
        time.sleep(0.1)

    signals = {}
    for aid, it in matched.items():
        if aid in mountain:
            signals[aid] = {"via_mountain": True, "en_views": en_views.get(it["enwiki"], 0) if it["enwiki"] else 0}
        else:
            signals[aid] = {"sitelinks": math.log1p(it["sitelinks"]),
                            "length": lengths.get(it["zhwiki"], 0) if it["zhwiki"] else 0,
                            "views": views.get(it["zhwiki"], 0) if it["zhwiki"] else 0}
    scores = fame_scores(by_county, signals)
    en_scores = {aid: en_fame(en_views.get(it["enwiki"], 0)) for aid, it in matched.items() if it["enwiki"]}
    en_scores = {aid: v for aid, v in en_scores.items() if v > 0}
    args.output.write_text(json.dumps({
        "generated": dt.date.today().isoformat(),
        "pageviews": f"{start:%Y-%m} to {end:%Y-%m}",
        "sources": "Wikidata (CC0), Wikimedia pageviews (zh and en) and zh.wikipedia page info",
        "scores": dict(sorted(scores.items())),
        "en_scores": dict(sorted(en_scores.items())),
        "local_scores": dict(sorted(local_scores(scores, en_scores).items())),
    }, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    args.extra_output.write_text(json.dumps({
        "generated": dt.date.today().isoformat(),
        "source": "Wikidata (CC0): sights not in the Tourism Administration register",
        "places": sorted(extras, key=lambda r: r["AttractionID"]),
    }, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"Wrote {len(scores)} scores for {len(rows)} open listings and {len(extras)} extra sights")


if __name__ == "__main__":
    main()
