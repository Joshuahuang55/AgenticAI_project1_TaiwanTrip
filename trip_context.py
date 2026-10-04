"""Session preferences extracted from user messages, separate from bounded chat history."""

import asyncio
import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import litellm
from agents import Agent, Runner
from pydantic import BaseModel, Field

log = logging.getLogger("taiwan_trip")
TripField = Literal["city", "area", "start_date", "end_date", "departure_point", "budget",
                    "interests", "dietary_needs", "follow_up_questions", "available_minutes", "setting",
                    "outing_start_time", "outing_end_time"]


class PreferenceUpdate(BaseModel):
    field: TripField
    operation: Literal["set", "clear", "no_preference"]
    value: str | None = Field(default=None, max_length=200)
    evidence: str = Field(min_length=1, max_length=500)


class TripUpdates(BaseModel):
    updates: list[PreferenceUpdate] = Field(default_factory=list, max_length=13)


@dataclass(frozen=True)
class Preference:
    value: str | None
    status: str
    evidence: str


@dataclass
class TripContext:
    preferences: dict[str, Preference] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {key: {"value": pref.value, "status": pref.status, "evidence": pref.evidence}
                for key, pref in self.preferences.items()}

    def apply(self, updates: TripUpdates, message: str) -> "TripContext":
        """Return a new context; ignore changes without a quote from this user message."""
        accepted = {}
        for update in updates.updates:
            if not update.evidence.strip() or update.evidence not in message:
                continue
            value = update.value.strip() if update.value else None
            if update.operation == "set":
                if not value:
                    continue
                if update.field in ("start_date", "end_date"):
                    try:
                        if dt.date.fromisoformat(value).isoformat() != value:
                            continue
                    except ValueError:
                        continue
                if update.field == "follow_up_questions" and value not in ("allowed", "avoid"):
                    continue
                if update.field == "setting" and value not in ("indoor", "outdoor", "any"):
                    continue
                if update.field == "available_minutes":
                    if not value.isascii() or not value.isdecimal() or not 15 <= int(value) <= 720:
                        continue
                    value = str(int(value))
                if update.field in ("outing_start_time", "outing_end_time"):
                    try:
                        if dt.time.fromisoformat(value).strftime("%H:%M") != value:
                            continue
                    except ValueError:
                        continue
            elif update.operation == "no_preference" and update.field not in (
                "city", "area", "departure_point", "budget", "interests", "dietary_needs", "setting", "available_minutes"
            ):
                continue
            accepted[update.field] = Preference(
                value if update.operation == "set" else None,
                {"set": "specified", "clear": "unknown", "no_preference": "no_preference"}[update.operation],
                update.evidence,
            )

        preferences = dict(self.preferences)
        # An area belongs to the active destination city. Keep it only if the message sets both.
        if "city" in accepted:
            old_city = preferences.get("city")
            new_city = accepted["city"]
            if old_city is None or (old_city.value, old_city.status) != (new_city.value, new_city.status):
                preferences.pop("area", None)
                preferences.pop("available_minutes", None)  # The old outing budget belongs to its destination.
                preferences.pop("outing_start_time", None)
                preferences.pop("outing_end_time", None)
        preferences.update(accepted)
        outing_start, outing_end = preferences.get("outing_start_time"), preferences.get("outing_end_time")
        if outing_start and outing_end and outing_start.value and outing_end.value and outing_start.value >= outing_end.value:
            # A corrected start can supersede an old end, and vice versa; reject an inverted new pair.
            changed = {key for key in ("outing_start_time", "outing_end_time") if key in accepted}
            if len(changed) == 1:
                preferences.pop("outing_end_time" if "outing_start_time" in changed else "outing_start_time", None)
            else:
                for key in changed:
                    if key in self.preferences:
                        preferences[key] = self.preferences[key]
                    else:
                        preferences.pop(key, None)
        start, end = preferences.get("start_date"), preferences.get("end_date")
        if start and end and start.value and end.value and start.value > end.value:
            changed = {key for key in ("start_date", "end_date") if key in accepted}
            if len(changed) == 1:
                # A corrected boundary can make the old other boundary unusable.
                preferences.pop("end_date" if "start_date" in changed else "start_date", None)
            else:
                # Do not save an inverted range supplied by the extractor.
                for key in ("start_date", "end_date"):
                    if key in self.preferences:
                        preferences[key] = self.preferences[key]
                    else:
                        preferences.pop(key, None)
        return TripContext(preferences)


EXTRACTOR_PROMPT = (Path(__file__).parent / "prompts" / "trip_context.txt").read_text(encoding="utf-8")
EXTRACTOR = Agent(name="trip_context_extractor", instructions=EXTRACTOR_PROMPT, output_type=TripUpdates)


def taiwan_today() -> dt.date:
    return dt.datetime.now(ZoneInfo("Asia/Taipei")).date()


async def extract_trip_context(current: TripContext, message: str, last_reply: str,
                               model, settings) -> TripContext:
    """One bounded call; ordinary failures retain preferences, provider rate limits stop the turn."""
    payload = json.dumps({"today_in_taiwan": taiwan_today().isoformat(),
                          "saved_preferences": current.as_dict(),
                          "last_assistant_message": last_reply[:2000],
                          "new_user_message": message}, ensure_ascii=False)
    extractor = EXTRACTOR.clone(model=model, model_settings=settings)
    try:
        result = await asyncio.wait_for(Runner.run(extractor, payload, max_turns=1), timeout=10)
        if not isinstance(result.final_output, TripUpdates):
            raise ValueError("Unexpected trip context output")
        return current.apply(result.final_output, message)
    except litellm.RateLimitError:
        raise
    except Exception:
        # Do not put user preferences or provider errors into logs.
        log.warning("Trip context extraction failed; retaining previous preferences")
        return current
