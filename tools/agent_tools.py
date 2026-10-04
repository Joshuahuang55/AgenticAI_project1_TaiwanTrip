"""Typed SDK tools; domain functions remain callable without an agent context."""

import asyncio
import json
from collections.abc import Callable
from typing import Annotated, Literal

from agents import FunctionTool, RunContextWrapper, function_tool
from pydantic import Field

import guardrails
from guardrails import ChatState
from tools import lodging_preferences, run_tool

Limit = Annotated[int, Field(ge=1, le=10, strict=True)]
Minutes = Annotated[int, Field(ge=15, le=720, strict=True)]
PositiveAmount = Annotated[float, Field(gt=0, allow_inf_nan=False, strict=True)]
NightlyBudget = Annotated[int, Field(gt=0, strict=True)]
Style = Literal["must_see", "local"]
Interest = Literal["history", "art", "nature", "hiking", "shopping", "culture", "museums", "temples"]


def _record(ctx, args, result):
    ctx.context.tool_calls.append({"id": ctx.tool_call_id, "name": ctx.tool_name,
                                   "args": args, "result": result})
    return result


def _argument_error(ctx, _error):
    """Validation failures still produce a recorded JSON reply and preserve tool-call history."""
    try:
        args = json.loads(ctx.tool_arguments or "{}")
    except json.JSONDecodeError:
        args = {"_raw": ctx.tool_arguments}
        message = "Arguments were not valid JSON."
    else:
        message = f"Bad arguments for {ctx.tool_name}."
        if not isinstance(args, dict):
            args = {"_raw": ctx.tool_arguments}
            message = "Arguments must be a JSON object."
    result = json.dumps({"error": message,
                         "hint": "Check the tool's argument types, allowed choices, and required fields, then retry."})
    return _record(ctx, args, result)


def build_tools(executor: Callable[[str, dict], str] = run_tool) -> list[FunctionTool]:
    """Build decorated tools with a shared execution adapter and the app's guardrails."""
    options = {"strict_mode": False, "docstring_style": "google",
               "failure_error_function": _argument_error,
               "tool_input_guardrails": [guardrails.check_tool_args],
               "tool_output_guardrails": [guardrails.check_tool_result]}

    async def invoke(ctx, values):
        # The SDK validates types before this point. Retain omitted arguments so domain defaults
        # and saved preferences work as before; record the validated, actually executed arguments.
        raw = json.loads(ctx.tool_arguments or "{}")
        supplied = {key: value for key, value in values.items() if key not in ("ctx", "invoke")}
        unknown = set(raw) - set(supplied)
        if unknown:
            result = json.dumps({"error": f"Unknown arguments for {ctx.tool_name}: {', '.join(sorted(unknown))}",
                                 "hint": "Use only arguments in the tool definition and retry."})
            return _record(ctx, raw, result)
        args = {key: supplied[key] for key in raw}
        if ctx.tool_name == "legal_stay_check" and not args.get("name"):
            message = ctx.context.user_texts[-1] if ctx.context.user_texts else ""
            count = lodging_preferences.requested_limit(message)
            if count is not None:
                args["limit"] = count
        if ctx.tool_name in ("find_attractions", "typhoon_backup_plan"):
            keys = ("available_minutes", "setting") if ctx.tool_name == "find_attractions" else ("available_minutes",)
            for key in keys:
                pref = ctx.context.trip.preferences.get(key)
                if args.get(key) is None and pref and pref.status == "specified":
                    args[key] = int(pref.value) if key == "available_minutes" else pref.value
            if ctx.tool_name == "typhoon_backup_plan":
                for key in ("start_time", "end_time"):
                    pref = ctx.context.trip.preferences.get("outing_" + key)
                    if args.get(key) is None and pref and pref.status == "specified":
                        args[key] = pref.value
        result = await asyncio.to_thread(executor, ctx.tool_name, args)
        return _record(ctx, args, result)

    @function_tool(**options)
    async def legal_stay_check(ctx: RunContextWrapper[ChatState], city: str,
                              name: str | None = None, type: Literal["hotel", "bnb"] | None = None,
                              max_price_twd: NightlyBudget | None = None, limit: Limit = 5,
                              district: str | None = None,
                              price_preference: Literal["any", "budget"] = "any") -> str:
        """Check a stay's registration or compare registered hotels and B&Bs in a Taiwan city.

        With name, return matching licenses and addresses. Otherwise return ranked stays,
        reported starting rates, a recommended pick and price trade-offs. No live bookings.

        Args:
            city: Taiwan city/county, e.g. Taipei, Tainan, or Hualien.
            name: Chinese lodging name to check; omit for recommendations.
            type: Licensed hotel or bnb (homestay/guesthouse); omit for both in list mode.
            max_price_twd: Explicit nightly TWD cap, list mode only. Convert other currencies first;
                never invent a cap for cheap alone. Reference starts are not booking prices.
            limit: Number of stays, 1–10; default 5, list mode only.
            district: Known Traditional Chinese district, e.g. 萬華區; list mode only.
            price_preference: Use budget for cheap stays, ranking lower reported starting rates;
                any favors Taiwan Host certification. Missing rates remain eligible.
        """
        return await invoke(ctx, locals())

    @function_tool(**options)
    async def find_local_food(ctx: RunContextWrapper[ChatState], city: str,
                              keyword: str | None = None, district: str | None = None,
                              limit: Limit = 10, style: Style = "must_see", names: list[str] | None = None,
                              dietary: Literal["vegetarian", "vegan"] | None = None,
                              price_preference: Literal["budget", "mid_range", "any"] | None = None,
                              max_price_twd: PositiveAmount | None = None,
                              confirmed_only: Annotated[bool, Field(strict=True)] = False) -> str:
        """Find food, restaurants or night markets in a Taiwan city, ranked by preferences then awards.

        Return place details, sourced dietary/price indications and comparisons; partial data
        still supports everyday suggestions. Night markets include available opening-day schedules.

        Args:
            city: Taiwan city/county, e.g. Taipei or Tainan.
            keyword: Dish, cuisine or night market; omit when dietary alone captures the request.
            district: Known Traditional Chinese district, e.g. 萬華區; district-wide search.
            limit: Number of places, 1–10; default 10.
            style: must_see for known spots; local for locals' favorites or hidden gems.
            names: Exact Chinese names to retrieve details for, including more_candidates.
            dietary: User's vegetarian or vegan requirement; preserve on follow-up/name lookups.
            price_preference: budget for cheap, mid_range for moderate prices, or any; unknown prices
                remain eligible. Preserve on name lookups.
            max_price_twd: Explicit TWD amount per person per meal; omit for cheap alone. Relative
                price bands support suggestions but do not verify an exact meal price.
            confirmed_only: True only for an explicit request for confirmed matches; default false.
        """
        return await invoke(ctx, locals())

    @function_tool(**options)
    async def find_attractions(ctx: RunContextWrapper[ChatState], city: str,
                               keyword: str | None = None, district: str | None = None,
                               limit: Limit = 10, names: list[str] | None = None,
                               style: Style = "must_see", interests: list[Interest] | None = None,
                               setting: Literal["any", "indoor", "outdoor"] = "any",
                               available_minutes: Minutes | None = None) -> str:
        """Find Taiwan attractions and compare their fit, including places for a weather backup.

        Return listed details, ranked picks, trade-offs and estimated visit durations. A time
        budget adds an ordered outing with transfer/break allowances. Hours and fees can be absent.

        Args:
            city: Taiwan city/county, e.g. Taipei or Tainan.
            keyword: Optional kind of place, e.g. museum, temple, hiking, or 老街; omit for broad search.
            district: Known Traditional Chinese district; omit when no area is known.
            limit: Candidate count, 1–10; default 10.
            names: Exact Chinese names from results/more_candidates for detail lookup; keep criteria.
            style: must_see for highlights; local for hidden gems and locals' favorites.
            interests: User-stated interests from this message or the session; omit when unknown.
            setting: any, indoor or outdoor; indoor can be a weather adjustment rather than a user preference.
            available_minutes: Total outing time, 15–720 minutes, reused on follow-ups; use the user's
                budget or the weather tool's window-capped budget. Excludes arrival/departure travel.
        """
        return await invoke(ctx, locals())

    @function_tool(**options)
    async def twd_exchange(ctx: RunContextWrapper[ChatState], amount: PositiveAmount,
                           currency: str = "USD", direction: Literal["to_twd", "from_twd"] = "to_twd") -> str:
        """Convert money between TWD and another currency using daily market rates.

        Return the converted amount, rate/date and sampled 30-day comparison. Does not supply
        travel prices or a bank/airport cash-counter quote.

        Args:
            amount: Positive amount to convert.
            currency: Non-TWD ISO currency code, e.g. USD, EUR or JPY; default USD.
            direction: to_twd converts amount in currency to TWD; from_twd converts TWD to currency.
        """
        return await invoke(ctx, locals())

    @function_tool(**options)
    async def hsr_trip_planner(ctx: RunContextWrapper[ChatState], origin: str, destination: str, date: str,
                               depart_after: str | None = None, rail: Literal["THSR", "TRA"] = "THSR",
                               depart_before: str | None = None, arrive_by: str | None = None,
                               preference: Literal["earliest_arrival", "fastest", "cheapest", "earliest_departure"] = "earliest_arrival",
                               limit: Limit = 3) -> str:
        """Compare published THSR or TRA trains on a travel date and recommend an option.

        Return train types/numbers, times, durations, adult standard-class fares and computed
        trade-offs. Rank the whole matching timetable; keep schedules when fares fail. No live seats.

        Args:
            origin: Station in English or Chinese, e.g. Taipei.
            destination: Arrival station; THSR Kaohsiung uses Zuoying or Kaohsiung.
            date: Required travel date in YYYY-MM-DD; ask the user if unknown, never guess today.
            depart_after: Inclusive earliest departure, HH:MM on the travel date.
            rail: THSR or TRA; default THSR. Use TRA for cheapest trains when service is unspecified.
            depart_before: Inclusive latest departure, HH:MM on the travel date.
            arrive_by: Inclusive same-day arrival deadline, HH:MM; omit for next-day deadlines.
            preference: Default earliest_arrival; fastest minimizes duration, cheapest known fare,
                earliest_departure only for an explicit request to leave soonest.
            limit: Number of options, 1–10; default 3.
        """
        return await invoke(ctx, locals())

    @function_tool(**options)
    async def crowd_risk_check(ctx: RunContextWrapper[ChatState], start_date: str, end_date: str) -> str:
        """Estimate holiday travel pressure using the government calendar and historical TRA entries.

        Use for dated Taiwan itineraries or holiday travel. Return daily risk, reasons and
        historical evidence; this is a TRA network estimate, not HSR demand or live seat occupancy.

        Args:
            start_date: First date, YYYY-MM-DD.
            end_date: Last date, YYYY-MM-DD; inclusive range of at most 30 days.
        """
        return await invoke(ctx, locals())

    @function_tool(**options)
    async def typhoon_backup_plan(ctx: RunContextWrapper[ChatState], city: str, date: str | None = None,
                                  available_minutes: Minutes | None = None,
                                  start_time: str | None = None, end_time: str | None = None) -> str:
        """Check CWA weather and current typhoon warnings for a Taiwan outing.

        Compare forecast periods overlapping the window and return outdoor/flexible/indoor advice.
        Current warnings recommend postponing sightseeing; future dates outside the forecast get
        a seasonal note. Returns weather only; decide separately whether to call find_attractions.

        Args:
            city: Taiwan city/county, e.g. Taipei or Hualien.
            date: Travel date, YYYY-MM-DD; omit for today.
            available_minutes: Outing duration, 15–720 minutes; reuse the user's earlier budget.
            start_time: Outing start, HH:MM Taiwan time; default 08:00.
            end_time: Same-day end, HH:MM; default 20:00 or start plus duration for a stated start.
        """
        return await invoke(ctx, locals())

    return [legal_stay_check, find_local_food, find_attractions, twd_exchange,
            hsr_trip_planner, crowd_risk_check, typhoon_backup_plan]
