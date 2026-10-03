"""Label the places with the highest local fame as local favorites, for find_attractions(style="local").

Local fame (scripts/build_fame.py) says a place is read about in Chinese but not in English. It cannot
tell a place locals love (漁光島, 四草綠色隧道) from one that is only documented (a minor shrine with a
long article). An LLM labels the top candidates per county, a person reviews the labels, and only
reviewed labels ship:

  1. uv run python scripts/label_local_favorites.py label --review local_review.csv
     (asks Qwen on Vertex AI, about 2 minutes; needs Google Cloud credentials as in README.md)
  2. Open local_review.csv and correct the `label` column: local_favorite, tourist_spot or minor.
  3. uv run python scripts/label_local_favorites.py apply local_review.csv
     (writes tools/data/local_favorites.json)
"""

import argparse
import csv
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import tourism_data  # noqa: E402

FAME_PATH = ROOT / "tools" / "data" / "attraction_fame.json"
EXTRA_PATH = ROOT / "tools" / "data" / "extra_attractions.json"
OUTPUT = ROOT / "tools" / "data" / "local_favorites.json"
MODEL = "vertex_ai/qwen/qwen3-235b-a22b-instruct-2507-maas"
LABELS = ("local_favorite", "tourist_spot", "minor")
PER_COUNTY = 30  # candidates labeled per county, by local fame
PROMPT = """You know Taiwan well. For each attraction in {county} below, answer with one label:
- local_favorite: Taiwanese people actively go and recommend it (a day out, a weekend spot, a well-loved
  temple, market, trail, beach or park), but few foreign tourists know it. Worth suggesting to a traveler
  who wants to see where locals go.
- tourist_spot: already a standard sight for foreign visitors, in most English guidebooks.
- minor: not worth suggesting: little visited, not really a sight, or you do not know it.
Judge the place itself. Reply with a JSON object mapping each exact name to one label.

{names}"""


def candidates(local: dict[str, float], rows: list[dict], per_county: int = PER_COUNTY) -> list[dict]:
    """The top places by local fame in each county, best first."""
    by_county = defaultdict(list)
    for r in rows:
        aid = r.get("AttractionID")
        county = (r.get("PostalAddress") or {}).get("City")
        if county and local.get(aid, 0) > 0:
            by_county[county].append({"id": aid, "county": county, "name": r["AttractionName"], "local": local[aid]})
    out = []
    for county in sorted(by_county):
        out += sorted(by_county[county], key=lambda c: -c["local"])[:per_county]
    return out


def parse_labels(text: str, names: list[str]) -> dict[str, str]:
    """The model's JSON object, keeping only known labels; a missing or odd label becomes minor."""
    match = re.search(r"\{.*\}", text, re.S)
    got = json.loads(match.group(0)) if match else {}
    return {n: got.get(n) if got.get(n) in LABELS else "minor" for n in names}


def label(review: Path) -> None:
    import litellm  # only this step needs a model

    fame = json.loads(FAME_PATH.read_text(encoding="utf-8"))
    rows = [r for r in tourism_data._download("attractions")["rows"] if r.get("ServiceStatus") not in (0, 3)]
    if EXTRA_PATH.exists():
        rows += json.loads(EXTRA_PATH.read_text(encoding="utf-8"))["places"]
    picked = candidates(fame.get("local_scores", {}), rows)
    by_county = defaultdict(list)
    for c in picked:
        by_county[c["county"]].append(c)
    for county, cs in by_county.items():
        names = [c["name"] for c in cs]
        resp = litellm.completion(model=MODEL, vertex_location="global", temperature=0, messages=[
            {"role": "user", "content": PROMPT.format(county=county, names="\n".join(names))}])
        labels = parse_labels(resp.choices[0].message.content, names)
        for c in cs:
            c["label"] = labels[c["name"]]
        print(f"{county}: {sum(c['label'] == 'local_favorite' for c in cs)} of {len(cs)} local favorites")
    with review.open("w", newline="", encoding="utf-8-sig") as f:  # BOM: Excel opens Chinese correctly
        w = csv.DictWriter(f, fieldnames=["id", "county", "name", "local", "label"])
        w.writeheader()
        w.writerows(picked)
    print(f"Wrote {len(picked)} labels to {review}. Correct the label column, then run `apply`.")


def apply(review: Path, output: Path = OUTPUT) -> dict[str, str]:
    with review.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    bad = [r["name"] for r in rows if r["label"] not in LABELS]
    if bad:
        raise SystemExit(f"Unknown labels for: {', '.join(bad)}. Use one of {', '.join(LABELS)}.")
    labels = {r["id"]: r["label"] for r in rows if r["label"] == "local_favorite"}
    output.write_text(json.dumps({
        "generated": dt.date.today().isoformat(),
        "method": f"{MODEL} labels of the top places by local fame, reviewed by a second model (Claude) for the project owner",
        "labels": dict(sorted(labels.items())),
    }, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"Wrote {len(labels)} local favorites to {output}")
    return labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="step", required=True)
    p = sub.add_parser("label", help="ask the model and write a CSV to review")
    p.add_argument("--review", type=Path, default=Path("local_review.csv"))
    p = sub.add_parser("apply", help="write the reviewed labels to tools/data/local_favorites.json")
    p.add_argument("review", type=Path)
    args = parser.parse_args()
    if args.step == "label":
        import app  # noqa: F401  loads .env (Google Cloud project) like the app does
        label(args.review)
    else:
        apply(args.review)


if __name__ == "__main__":
    main()
