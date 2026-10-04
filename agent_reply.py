"""Small SDK reply schema and references resolved against accepted session evidence."""

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from planning_context import KINDS


class ProposedPlace(BaseModel):
    reference_id: str = Field(description="Copy a candidate reference_id from planning context.")
    label: str = Field(description="The display name exactly as used in message, or train number; English for English answers.")
    role: Literal["recommended", "alternative"]


class TravelReply(BaseModel):
    message: str = Field(description="Helpful Markdown answer with human-readable names; no internal reference IDs or source footer.")
    places: list[ProposedPlace] = Field(description="Only proposed places/trains actually named in message, in order; otherwise [].")
    sources: list[str] = Field(description="Record reference_ids supporting this answer's facts; otherwise [].")


def reply_text(output) -> str:
    """Helpers and guardrails read the message, including from serialized history."""
    if isinstance(output, TravelReply):
        return output.message
    if isinstance(output, str):
        try:
            data = json.loads(output)
        except ValueError:
            return output
        if isinstance(data, dict) and {"message", "places", "sources"} <= data.keys():
            return data["message"] if isinstance(data["message"], str) else ""
        return output
    return str(output or "")


@dataclass
class ResolvedReply:
    message: str
    places: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)

    def render(self) -> str:
        labels = list(dict.fromkeys(source["source"] for source in self.sources))
        return self.message + ("\n\nSource: " + "; ".join(labels) if labels else "")


SOURCE_LINE = re.compile(r"^\s*(?:\*\*)?(?:sources?|來源|資料來源)(?:\*\*)?\s*[:：].*$", re.I | re.M)
REFERENCE_PATTERN = r"r_[0-9a-f]{16}(?:_\d+)?"
REFERENCE_ID = re.compile(r"(?<![A-Za-z0-9_])" + REFERENCE_PATTERN + r"(?![A-Za-z0-9_])")
REFERENCE_LINK = re.compile(r"\[([^\]\n]+)\]\(\s*" + REFERENCE_PATTERN + r"\s*\)")
REFERENCE_ASIDE = re.compile(r"[ \t]*[（(]\s*(?:`|\*\*)?" + REFERENCE_PATTERN + r"(?:`|\*\*)?\s*[）)]")
REFERENCE_CODE = re.compile(r"`(" + REFERENCE_PATTERN + r")`")


def clean_reference_text(text: str, names: dict[str, str]) -> str:
    """Hide internal IDs while preserving visible names and ordinary Markdown links."""
    text = REFERENCE_LINK.sub(r"\1", text)
    text = REFERENCE_ASIDE.sub("", text)
    text = REFERENCE_CODE.sub(lambda match: names.get(match[1], ""), text)
    return REFERENCE_ID.sub(lambda match: names.get(match[0], ""), text).strip()


def resolve_reply(output: TravelReply, planning) -> ResolvedReply:
    """Invalid references are omitted without withholding the useful answer."""
    records = planning.evidence_records()
    candidates = {candidate["reference_id"]: (record, candidate)
                  for record in records for candidate in record.get("candidates", [])}
    names = {reference: str(candidate.get("name_en") or candidate.get("name") or candidate.get("train_no") or "")
             for reference, (_, candidate) in candidates.items()}
    for place in output.places:
        label = clean_reference_text(place.label, {})
        if place.reference_id in candidates and label:
            names[place.reference_id] = label
    message = clean_reference_text(SOURCE_LINE.sub("", output.message), names)
    result = ResolvedReply(message)
    seen = set()
    # Accepted places are bounded by the session's candidate records, not a two-day itinerary.
    for place in output.places:
        match = candidates.get(place.reference_id)
        label = clean_reference_text(place.label, names)
        if not match or place.reference_id in seen or not label or label.casefold() not in message.casefold():
            continue
        record, candidate = match
        seen.add(place.reference_id)
        result.places.append(dict(candidate, tool=record["tool"], kind=KINDS[record["tool"]],
                                  label=label[:120], role=place.role))
    by_id = {record["reference_id"]: record for record in records}
    for reference in dict.fromkeys(output.sources[:10]):
        record = by_id.get(reference)
        if record and record.get("source"):
            result.sources.append({"reference_id": reference, "source": record["source"]})
    return result
