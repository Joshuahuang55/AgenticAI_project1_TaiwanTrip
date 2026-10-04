"""Bounded session planning facts and explicitly selected candidates, separate from preferences."""

import json
import re
from hashlib import sha256
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field
from tools.tdx_client import resolve_city

MAX_RECORDS = 10
MAX_SUMMARY_CHARS = 9000


class PlanningChoice(BaseModel):
    kind: Literal["stay", "attraction", "food", "train"]
    operation: Literal["select", "clear"] = "select"
    name: str | None = Field(default=None, max_length=120)
    label: str | None = Field(default=None, max_length=120)
    evidence: str = Field(min_length=1, max_length=500)


def object_value(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return {}
    return result if isinstance(result, dict) else {}


def city_key(value):
    resolved = resolve_city(value) if isinstance(value, str) else None
    return resolved[0] if resolved else value


def bounded_excerpt(text, limit):
    """Keep both context and the latest question when a long reply needs shortening."""
    if len(text) <= limit:
        return text
    marker = " … "
    left = (limit - len(marker)) // 2
    right = limit - len(marker) - left
    return text[:left] + marker + text[-right:]


def pick(value, *keys):
    """Retain a small allowlist; external descriptions and next-step instructions stay out."""
    value = value if isinstance(value, dict) else {}
    return {key: item[:120] if isinstance(item, str) else item
            for key in keys if (item := value.get(key)) is not None}


KINDS = {"legal_stay_check": "stay", "find_attractions": "attraction",
         "find_local_food": "food", "hsr_trip_planner": "train"}


@dataclass
class PlanningContext:
    records: dict[str, dict] = field(default_factory=dict)
    confirmed_choices: dict[str, list[dict]] = field(default_factory=dict)
    proposals: dict[str, dict] = field(default_factory=dict)

    def remember(self, name, args, result):
        data = object_value(result)
        if not data or data.get("error"):
            return
        city = city_key(data.get("city") or args.get("city") or args.get("destination"))
        identity = json.dumps([name, args, data], ensure_ascii=False, sort_keys=True)
        reference = "r_" + sha256(identity.encode()).hexdigest()[:16]
        record = {"tool": name, "city": city, "lookup": pick(args, "district", "date", "available_minutes",
                  "start_time", "end_time", "origin", "destination", "dietary", "price_preference", "max_price_twd"),
                  "reference_id": reference,
                  "source": data["source"][:500] if isinstance(data.get("source"), str) else None}
        comparison = object_value(data.get("comparison"))
        if name in KINDS:
            rows = data.get("stays" if name == "legal_stay_check" else "trains" if name == "hsr_trip_planner" else "results") or []
            if name == "legal_stay_check" and data.get("is_registered"):
                rows = data.get("matches") or rows
            candidates = []
            for row in rows[:10] if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                candidate = pick(row, "name", "name_en", "district", "address", "price_range_twd",
                                 "license_type", "ticket_info", "train_no", "train_type", "departure", "arrival",
                                 "arrival_date", "duration_min", "fare_twd", "lat", "lon")
                candidate["city"] = city
                candidate["reference_id"] = f"{reference}_{len(candidates)}"
                facts = object_value(row.get("facts"))
                for key in ("district", "price", "vegetarian", "vegan"):
                    if fact := facts.get(key):
                        candidate[key + "_fact"] = pick(fact, "value", "status")
                if not candidate.get("district") and candidate.get("address") and city:
                    rest = candidate["address"].removeprefix(city)
                    district = re.match(r"([^區鄉鎮市縣]{1,6}[區鄉鎮市])", rest)
                    if district:
                        candidate["district"] = district[1]
                candidate["visit_fit"] = pick(row.get("preference_match"), "setting", "visit_minutes", "fits_typical_visit")
                candidates.append(candidate)
            record["candidates"] = candidates
            record["tool_recommendation"] = pick(comparison, "recommended_name", "recommended_train_no", "reason")
            visit = object_value(comparison.get("suggested_visit"))
            if visit:
                record["suggested_outing"] = pick(visit, "names", "estimated_minutes", "available_minutes",
                                                   "remaining_minutes", "break_minutes", "status")
                record["suggested_outing"]["timeline"] = [pick(step, "kind", "name", "from", "to",
                    "estimated_minutes", "start_minute", "end_minute", "relative_time") for step in visit.get("timeline", [])]
        elif name == "typhoon_backup_plan":
            record.update(pick(data, "date", "seasonal_note"))
            record["weather"] = pick(comparison, "strategy", "reason", "outing_window", "available_minutes",
                                      "rain_chance", "forecast_covers_window", "warning_applies_to_outing")
        elif name == "twd_exchange":
            record.update(pick(data, "amount", "currency", "direction", "rate", "converted_amount", "converted_currency", "rate_date"))
        elif name == "crowd_risk_check":
            record["days"] = [pick(day, "date", "risk", "reason") for day in (data.get("days") or data.get("results") or [])[:30]]
        else:
            return
        key = json.dumps([name, city, record.get("date") or args.get("date"), args.get("district")], ensure_ascii=False)
        self.records.pop(key, None)
        self.records[key] = record
        while len(self.records) > MAX_RECORDS:
            self.records.pop(next(iter(self.records)))

    def confirm(self, choices, message):
        for choice in choices:
            if not choice.evidence.strip() or choice.evidence not in message:
                continue
            if choice.operation == "clear":
                self.confirmed_choices.pop(choice.kind, None)
                continue
            # A named selection is required. Agreement and references such as "your plan" stay provisional.
            label = choice.label or choice.name
            if not choice.name or not label or label not in choice.evidence or re.fullmatch(
                r"(?:(?:your|the|that|this)\s+)?(?:(?:recommended|suggested|first|second)\s+)?"
                r"(?:hotel|train|place|plan|option|choice|pick|it)|(?:你的)?(?:推薦|建議|方案|計畫)", label.strip(), re.I
            ):
                continue
            candidates = [item for record in reversed(list(self.records.values()))
                          if KINDS.get(record["tool"]) == choice.kind for item in record.get("candidates", [])]
            match = next((item for item in candidates if choice.name in
                          (item.get("name"), item.get("name_en"), item.get("train_no"))), None)
            if match:
                selected = dict(match, user_evidence=choice.evidence)
                existing = self.confirmed_choices.setdefault(choice.kind, [])
                identity = match.get("name") or match.get("train_no")
                existing[:] = [item for item in existing if (item.get("name") or item.get("train_no")) != identity]
                existing.append(selected)
                del existing[:-3]

    def remember_proposal(self, output, city, broad_plan, places=None, sources=None):
        text = str(output)
        excerpt = bounded_excerpt(text, 1000)
        self.proposals["trip_outline" if broad_plan else "latest_refinement"] = {
            "city": city_key(city), "text": excerpt, "truncated": len(text) > 1000}
        if places is not None:
            proposal = self.proposals["trip_outline" if broad_plan else "latest_refinement"]
            proposal.update(
                places=[pick(place, "reference_id", "kind", "name", "train_no", "label", "role",
                             "city", "district", "address") for place in places[:20]],
                sources=list(sources or []))
            while len(json.dumps(proposal, ensure_ascii=False)) > 3000:
                if proposal["sources"]:
                    proposal["sources"].pop()
                elif proposal["places"]:
                    proposal["places"].pop()
                else:
                    break
                proposal["truncated"] = True

    def as_dict(self, city=None, include_other_cities=False):
        city = city_key(city)
        matches = lambda value: not city or not value.get("city") or value.get("city") == city
        relevant = lambda value: include_other_cities or matches(value)
        result = {"confirmed_choices": {},
                  "assistant_proposals": {key: value for key, value in self.proposals.items() if relevant(value)},
                  "tool_evidence": []}
        for kind, items in self.confirmed_choices.items():
            selected = result["confirmed_choices"][kind] = []
            for item in reversed(items):
                if relevant(item):
                    selected.append(dict(item, user_evidence=item["user_evidence"][:120]))
                    if len(json.dumps(result, ensure_ascii=False)) > MAX_SUMMARY_CHARS:
                        selected.pop()
                        break
        records = list(reversed(list(self.records.values())))
        if include_other_cities and city:
            records.sort(key=lambda record: not matches(record))
        for record in records:
            if relevant(record):
                result["tool_evidence"].append(record)
                if len(json.dumps(result, ensure_ascii=False)) > MAX_SUMMARY_CHARS:
                    result["tool_evidence"].pop()
                    break
        return result
