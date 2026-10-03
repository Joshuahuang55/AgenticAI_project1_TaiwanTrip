"""List well-known food places that no award covers (豪大大雞排, 阿宗麵線), for scripts/build_food_fame.py.

Awards miss places every traveler hears of. An LLM lists them per county, each is checked against the
OpenStreetMap and official places it must match, a person reviews the list, and only reviewed rows ship:

  1. uv run python scripts/list_known_food.py list --review data/known_food_review.csv
     (asks Qwen on Vertex AI, about 3 minutes; needs Google Cloud credentials as in README.md)
  2. Open the CSV and set `keep` to no for wrong, closed or unremarkable places.
  3. uv run python scripts/list_known_food.py apply data/known_food_review.csv
     (writes data/food_awards/known_food.json, which build_food_fame.py reads)
"""

import argparse
import csv
import datetime as dt
import gzip
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_food_fame import name_forms, names_match  # noqa: E402
from tools import tourism_data  # noqa: E402
from tools.attractions import _in_order  # noqa: E402
from tools.tdx_client import CITIES  # noqa: E402

OSM_PATH = ROOT / "tools" / "data" / "osm_food.json.gz"
OUTPUT = ROOT / "data" / "food_awards" / "known_food.json"
MODELS = {"gemini": "vertex_ai/gemini-3.5-flash-lite", "qwen": "vertex_ai/qwen/qwen3-235b-a22b-instruct-2507-maas"}
MODEL = MODELS["gemini"]  # 2026 test: Qwen named 41 real places of 434, inventing the rest
# Places to ask for; others 12. Asked for more, the model pads the list with invented names (高雄 阿婆冰,
# 阿婆碗粿, 阿婆豆花...), which the data check catches but which crowd out real ones.
BIG = {"臺北市": 30, "臺南市": 30, "新北市": 20, "臺中市": 20, "高雄市": 20}
PROMPT = """List up to {n} well-known food places in {county}, Taiwan: restaurants, food stalls, snack
shops, bakeries, dessert or drink shops that locals and travelers widely know by name, including ones
that never won an award. One branch-independent shop name each, as written on its sign in Traditional
Chinese, with its signature item. Leave out night markets, food streets, chains with many branches
across Taiwan (except where the original shop is famous), and places you are unsure still operate.
Only name places you are certain exist under that exact name; a shorter list is better than a guess.
Reply with a JSON array of objects: {{"name": "...", "known_for": "..."}}."""


def match(name: str, places: list[dict]) -> list[dict]:
    """Places with a matching name, or whose name has the listed one's characters in order (or the other
    way round) and is at least 60% of its length: 阿明豬心冬粉 finds 阿明豬心."""
    forms = name_forms(name)
    out = []
    for p in places:
        if names_match(forms, p["forms"]):
            out.append(p)
            continue
        for a in forms:
            for b in p["forms"]:
                short, long_ = sorted((a, b), key=len)
                if len(short) >= 4 and len(short) / len(long_) >= 0.6 and _in_order(short, long_):
                    out.append(p)
                    break
            else:
                continue
            break
    return out


def ask(county: str, n: int, model: str = MODEL) -> list[dict]:
    import time
    import litellm
    for attempt in range(5):
        try:
            resp = litellm.completion(model=model, vertex_location="global", temperature=0, messages=[
                {"role": "user", "content": PROMPT.format(county=county, n=n)}])
            break
        except litellm.RateLimitError:
            if attempt == 4:
                raise
            time.sleep(20 * (attempt + 1))
    m = re.search(r"\[.*\]", resp.choices[0].message.content, re.S)
    got = json.loads(m.group(0)) if m else []
    return [g for g in got if isinstance(g, dict) and g.get("name")]


def list_places(review: Path, model: str = MODEL) -> None:
    """Ask for every county at once (4 requests at a time), then check each name against OSM places."""
    from concurrent.futures import ThreadPoolExecutor
    import app  # noqa: F401  loads .env (Google Cloud project) like the app does
    with gzip.open(OSM_PATH, "rt", encoding="utf-8") as f:
        places = json.load(f)["places"]
    by_county = defaultdict(list)
    for p in places:
        p["forms"] = name_forms(p["n"])
        by_county[p["city"]].append(p)
    counties = sorted(set(CITIES.values()))
    with ThreadPoolExecutor(4) as pool:
        lists = dict(zip(counties, pool.map(lambda c: ask(c, BIG.get(c, 12), model), counties)))
    rows = []
    for county in counties:
        listed = lists[county]
        for g in listed:
            hits = match(g["name"], by_county.get(county, []))
            rows.append({"county": county, "name": g["name"], "known_for": g.get("known_for", ""),
                         "matches": len(hits), "matched_names": " / ".join(sorted({h["n"] for h in hits})[:4]),
                         "keep": "yes" if hits else "no"})
        print(f"{county}: {len(listed)} listed, {sum(r['matches'] > 0 for r in rows if r['county'] == county)} found")
    with review.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {review}. Rows not found in the data start as keep=no.")


def apply(review: Path, output: Path = OUTPUT) -> list[dict]:
    with review.open(encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r["keep"].strip().lower() == "yes"]
    kept = [{"county": r["county"], "name": r["name"], "known_for": r["known_for"]} for r in rows]
    output.write_text(json.dumps({
        "generated": dt.date.today().isoformat(),
        "method": "LLM (Gemini, Qwen) list of well-known food places per county, checked against OpenStreetMap and "
                  "reviewed by a second model (Claude) for the project owner",
        "places": kept,
    }, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"Wrote {len(kept)} well-known places to {output}")
    return kept


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="step", required=True)
    p = sub.add_parser("list", help="ask the model and write a CSV to review")
    p.add_argument("--review", type=Path, default=ROOT / "data" / "known_food_review.csv")
    p.add_argument("--model", choices=list(MODELS), default="gemini")
    p = sub.add_parser("apply", help="write the reviewed places to data/food_awards/known_food.json")
    p.add_argument("review", type=Path)
    args = parser.parse_args()
    list_places(args.review, MODELS[args.model]) if args.step == "list" else apply(args.review)


if __name__ == "__main__":
    main()
