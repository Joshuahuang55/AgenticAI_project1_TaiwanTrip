"""Build tools/data/osm_food.json.gz: restaurants, cafes and food stalls from OpenStreetMap, per county.

Run from the repository root with `uv run python scripts/build_osm_food.py` (about 10 minutes, no TDX).
Pass --cities 臺中市 臺東縣 to refetch some counties into the existing file, or --towns-only to redo just
the districts. OSM has no district, so each place takes the district of the nearest official attraction or
restaurant within TOWN_KM (about 1 minute; a place near a border can get its neighbour's district).
The official restaurant register lists none in Taipei or Kaohsiung; OpenStreetMap lists thousands.
Kept per place: name, English and Japanese names, coordinates, kind, cuisine, opening hours, and the
street address when OSM has one (addr:full, or addr:street and addr:housenumber).

Data © OpenStreetMap contributors, under the Open Database License (ODbL 1.0):
https://www.openstreetmap.org/copyright. This file is a derived database and is shared under the ODbL.
"""

import argparse
import datetime as dt
import gzip
import json
import math
import sys
from collections import defaultdict
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import tourism_data  # noqa: E402
from tools.tdx_client import CITIES  # noqa: E402

OUTPUT = ROOT / "tools" / "data" / "osm_food.json.gz"
SERVERS = ("https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
           "https://overpass.kumi.systems/api/interpreter")
HEADERS = {"User-Agent": "TaiwanLikeALocal/1.0 (https://github.com/Joshuahuang55/AgenticAI_project1_TaiwanTrip)"}
QUERY = """[out:json][timeout:300];
area["name"="{county}"]["admin_level"="4"]->.a;
nwr["amenity"~"^(restaurant|fast_food|cafe|food_court|ice_cream)$"]["name"](area.a);
out center tags;"""
# Short keys keep the file small: about 100,000 places.
TOWN_KM = 1.5
FIELDS = {"name": "n", "name:en": "en", "name:ja": "ja", "cuisine": "c", "opening_hours": "h"}


def address(tags: dict) -> str | None:
    """'中正路16號' from addr:* tags, without the county (the tool adds it). None without a street."""
    if tags.get("addr:full"):
        return tags["addr:full"]
    street = tags.get("addr:street") or tags.get("addr:place")
    if not street:
        return None
    number = tags.get("addr:housenumber") or ""
    if number and number[-1].isdigit():
        number += "號"
    return f"{tags.get('addr:district') or ''}{street}{number}"


def place(element: dict, county: str) -> dict | None:
    tags = element.get("tags") or {}
    lat = element.get("lat", (element.get("center") or {}).get("lat"))
    lon = element.get("lon", (element.get("center") or {}).get("lon"))
    if not tags.get("name") or lat is None or lon is None or tags.get("disused") or tags.get("abandoned"):
        return None
    out = {"id": f"osm:{element['type'][0]}{element['id']}", "k": tags.get("amenity"), "lat": round(lat, 6),
           "lon": round(lon, 6), "city": county}
    out.update({short: tags[key] for key, short in FIELDS.items() if tags.get(key)})
    if addr := address(tags):
        out["a"] = addr
    return out


def assign_towns(places: list[dict], official: list[dict]) -> int:
    """Set each place's district ("t") from the nearest official listing in its county within TOWN_KM.
    Returns how many got one."""
    grid = defaultdict(list)
    for r in official:
        addr = r.get("PostalAddress") or {}
        if r.get("PositionLat") and r.get("PositionLon") and addr.get("Town"):
            grid[(round(r["PositionLat"], 2), round(r["PositionLon"], 2))].append(
                (r["PositionLat"], r["PositionLon"], addr.get("City"), addr["Town"]))
    done = 0
    for p in places:
        p.pop("t", None)
        cy, cx = round(p["lat"], 2), round(p["lon"], 2)
        best = None
        for dy in (-0.02, -0.01, 0, 0.01, 0.02):
            for dx in (-0.02, -0.01, 0, 0.01, 0.02):
                for lat, lon, city, town in grid.get((round(cy + dy, 2), round(cx + dx, 2)), ()):
                    if city != p["city"]:
                        continue
                    d = math.hypot((lat - p["lat"]) * 111, (lon - p["lon"]) * 111 * math.cos(math.radians(lat)))
                    if d <= TOWN_KM and (best is None or d < best[0]):
                        best = (d, town)
        if best:
            p["t"] = best[1]
            done += 1
    return done


def official_rows() -> list[dict]:
    return tourism_data._download("attractions")["rows"] + tourism_data._download("restaurants")["rows"]


def fetch(county: str) -> list[dict]:
    """One county's places. Tries each server, retrying busy, truncated or timed-out answers: a timed-out
    query comes back as valid JSON with no elements and a "remark", which must not read as an empty county."""
    for attempt in range(6):
        url = SERVERS[attempt % len(SERVERS)]
        try:
            resp = requests.post(url, data={"data": QUERY.format(county=county)}, headers=HEADERS, timeout=360)
            resp.raise_for_status()
            body = resp.json()
            elements = body["elements"]
            if body.get("remark") or not elements:
                raise ValueError(body.get("remark") or "no elements")
        except (requests.RequestException, ValueError, KeyError) as e:
            print(f"  {county}: {url} failed ({type(e).__name__}: {str(e)[:80]}), retrying")
            time.sleep(10 * (attempt + 1))
            continue
        return [p for p in (place(e, county) for e in elements) if p]
    raise RuntimeError(f"{county}: every Overpass server failed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--cities", nargs="*", help="refetch only these counties, keeping the others")
    parser.add_argument("--towns-only", action="store_true", help="only redo the districts of the existing file")
    args = parser.parse_args()
    if args.towns_only:
        with gzip.open(args.output, "rt", encoding="utf-8") as f:
            data = json.load(f)
        n = assign_towns(data["places"], official_rows())
        with gzip.open(args.output, "wt", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        print(f"{n} of {len(data['places'])} places have a district")
        return
    counties = args.cities or sorted(set(CITIES.values()))
    places = []
    if args.cities and args.output.exists():
        with gzip.open(args.output, "rt", encoding="utf-8") as f:
            places = [p for p in json.load(f)["places"] if p["city"] not in args.cities]
    for county in counties:
        found = fetch(county)
        places += found
        print(f"{county}: {len(found)} places")
        time.sleep(5)  # Overpass asks for gaps between heavy queries
    print(f"{assign_towns(places, official_rows())} of {len(places)} places have a district")
    with gzip.open(args.output, "wt", encoding="utf-8") as f:
        json.dump({"generated": dt.date.today().isoformat(),
                   "license": "© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)",
                   "places": places}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {len(places)} places to {args.output} ({args.output.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
