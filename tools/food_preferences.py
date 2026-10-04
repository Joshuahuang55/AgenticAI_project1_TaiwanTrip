"""Facts and preference comparisons for food; unknown values never count as matches."""

import re

DIETARY = ("vegetarian", "vegan")
DIET_KEYWORDS = {"vegetarian": "vegetarian", "vegan": "vegan", "素": "vegetarian",
                 "素食": "vegetarian", "蔬食": "vegetarian", "純素": "vegan", "全素": "vegan"}
PRICE_PREFERENCES = {"budget": 1, "mid_range": 2, "any": 4}
DIET_VALUES = {"yes", "only", "limited", "no"}


def fact(value, source=None, status=None, evidence=None, **extra):
    return {"value": value, "status": status or ("reported" if value else "unknown"),
            "source": source if value or status == "conflicting" else None,
            **({"evidence": evidence} if evidence else {}), **extra}


def dietary_fact(row: dict, diet: str, source: str) -> dict:
    tags = row.get("DietaryTags") or {}
    tags = {key: value for key, value in tags.items() if value in DIET_VALUES}
    vegan, vegetarian = tags.get("vegan"), tags.get("vegetarian")
    if vegan in ("yes", "only", "limited") and vegetarian == "no":
        return fact(None, source, status="conflicting", evidence=f"diet:vegan={vegan}; diet:vegetarian=no")
    value = tags.get(diet)
    if value:
        return fact(value, source, evidence=f"diet:{diet}={value}")
    if diet == "vegetarian" and vegan in ("yes", "only", "limited"):
        return fact("yes" if vegan == "only" else vegan, source, evidence=f"diet:vegan={vegan}")
    if diet == "vegan" and vegetarian == "no":
        return fact("no", source, evidence="diet:vegetarian=no")
    cuisines = (row.get("Cuisine") or "").lower().split(";")
    related = (diet, "vegan") if diet == "vegetarian" else (diet,)
    if any(c in cuisines for c in related):
        return fact(diet, source, status="indication", evidence="cuisine=" + row["Cuisine"])
    # A name is a search lead, not proof of ingredients or available dishes.
    name = row.get("RestaurantName") or ""
    words = ("vegetarian", "vegan", "素食", "蔬食", "純素", "全素") if diet == "vegetarian" else ("vegan", "純素", "全素")
    word = next((w for w in words if (re.search(r"\b" + w + r"\b", name, re.I) if w.isascii() else w in name)), None)
    return fact(diet, source, status="indication", evidence=f"name contains {word}") if word else fact(None)


def facts_for(row: dict, fame: dict, listing_source: str, award_source: str) -> dict:
    address = row.get("PostalAddress") or {}
    fields = row.get("FieldSources") or {}
    price = fame.get("price")
    if price not in ("$", "$$", "$$$", "$$$$"):
        price = None
    cuisine = [c.strip() for c in (row.get("Cuisine") or "").split(";") if c.strip()]
    return {
        "district": fact(address.get("Town"), listing_source,
                         status=row.get("DistrictStatus", "reported") if address.get("Town") else "unknown"),
        "cuisine": fact(cuisine or None, fields.get("cuisine", listing_source)),
        "price": fact(price, award_source, kind="relative_band", exact_twd=None),
        "opening_hours": fact(row.get("ServiceTimeInfo") or None, fields.get("opening_hours", listing_source)),
        **{diet: dietary_fact(row, diet, fields.get("dietary", listing_source)) for diet in DIETARY},
    }


def compare(facts: dict, *, district=None, dietary=None, price_preference=None, max_price_twd=None,
            confirmed_only=False) -> dict:
    checks = {}
    if district:
        f = facts["district"]
        status = "unknown" if f["status"] in ("unknown", "estimated") else (
            "match" if f["value"] == district else "conflict")
        checks["district"] = {"status": status, "reason": (
            f"Estimated district: {f['value']}." if f["status"] == "estimated"
            else "District is not stored." if f["status"] == "unknown"
            else f"Reported district: {f['value']}.")}
    if dietary:
        f = facts[dietary]
        availability = {"yes": "options available; not necessarily an exclusive menu",
                        "only": "exclusive menu reported", "limited": "limited options reported"}.get(f["value"])
        status = ("conflict" if f["status"] == "reported" and f["value"] == "no"
                  else "match" if f["status"] == "reported" and f["value"] in ("yes", "only", "limited")
                  else "unknown")
        checks["dietary"] = {"status": status, "evidence_status": f["status"], "source": f["source"], "reason": (
            f"Source reports {dietary} options ({f['value']}: {availability})." if status == "match"
            else f"Source reports no {dietary} options." if status == "conflict"
            else f"Listed {dietary} name/cuisine: {f['evidence']}." if f["status"] == "indication"
            else f"Conflicting dietary tags: {f['evidence']}." if f["status"] == "conflicting"
            else f"No {dietary} tag is stored.")}
        if status == "match":
            checks["dietary"]["availability"] = f["value"]
    if price_preference and price_preference != "any":
        band = facts["price"]["value"]
        status = "unknown" if not band else (
            "match" if len(band) <= PRICE_PREFERENCES[price_preference] else "conflict")
        checks["price_preference"] = {"status": status, "source": facts["price"]["source"], "reason": (
            f"Relative price band {band}; {price_preference} preference uses up to "
            f"{'$' * PRICE_PREFERENCES[price_preference]}."
            if band else "No relative price band is stored.")}
    if max_price_twd is not None:
        checks["max_price_twd"] = {"status": "unknown", "reason": (
            f"Exact meal price is not stored; requested cap is TWD {max_price_twd:g} per person."),
            "relative_price_band": facts["price"]["value"]}
    statuses = [c["status"] for c in checks.values()]
    fit = "conflict" if "conflict" in statuses else "needs_confirmation" if "unknown" in statuses else (
        "reported_match" if checks else "not_requested")
    return {"fit": fit, "checks": checks}


def priority(comparison: dict) -> tuple:
    statuses = [c["status"] for c in comparison["checks"].values()]
    diet = comparison["checks"].get("dietary", {})
    # A cheap award winner with no dietary evidence must not displace a dietary lead.
    dietary_support = (2 if diet.get("status") == "match" else
                       1 if diet.get("evidence_status") == "indication" else
                       -1 if diet.get("evidence_status") == "conflicting" else 0)
    variety = int(diet.get("availability") in ("yes", "only"))
    budget = comparison["checks"].get("max_price_twd")
    affordability = -(len(budget["relative_price_band"]) if budget["relative_price_band"] else 5) if budget else 0
    return dietary_support, -statuses.count("unknown"), statuses.count("match"), variety, affordability


def prepare(rows: list[dict], criteria: dict, default_source: str, award_source: str) -> tuple[list[dict], list[dict]]:
    eligible, excluded = [], []
    for original in rows:
        row = dict(original)
        source = row.get("Source") or default_source
        facts = facts_for(row, row.get("_Fame", {}), source, award_source)
        comparison = compare(facts, **criteria)
        row["_Facts"], row["_Comparison"] = facts, comparison
        if comparison["fit"] == "conflict":
            excluded.append({"name": row.get("RestaurantName"), "kind": "conflict", "reasons": [c["reason"] for c in comparison["checks"].values()
                                                                               if c["status"] == "conflict"]})
        elif criteria.get("confirmed_only") and comparison["fit"] == "needs_confirmation":
            # Keep counts/reasons, without offering unconfirmed names as recommendation candidates.
            excluded.append({"kind": "unconfirmed", "reasons": [c["reason"] for c in comparison["checks"].values()
                                                                  if c["status"] == "unknown"]})
        else:
            eligible.append(row)
    return eligible, excluded


def summary(results: list[dict], excluded: list[dict]) -> dict:
    return {"returned": len(results), "reported_matches": sum(r["comparison"]["fit"] == "reported_match" for r in results),
            "needs_confirmation": sum(r["comparison"]["fit"] == "needs_confirmation" for r in results),
            "excluded_conflicts": sum(r["kind"] == "conflict" for r in excluded),
            "excluded_unconfirmed": sum(r["kind"] == "unconfirmed" for r in excluded),
            "meaning": "Statuses describe listing evidence for ranking. Name/cuisine indications support ordinary "
                       "suggestions; missing fields are data gaps. Strict filtering applies only with confirmed_only."}
