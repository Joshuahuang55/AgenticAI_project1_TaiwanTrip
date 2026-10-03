"""Published THSR and TRA schedules and adult one-way fares from TDX."""

import datetime as dt
import json
import math
import re

from tools.tdx_client import tdx_get

RAIL_PATHS = {
    "THSR": {
        "stations": "basic/v2/Rail/THSR/Station",
        "timetable": "basic/v2/Rail/THSR/DailyTimetable/OD/{origin}/to/{destination}/{date}",
        "fares": "basic/v2/Rail/THSR/ODFare/{origin}/to/{destination}",
        "station_rows": None,
        "timetable_rows": None,
        "fare_rows": None,
    },
    "TRA": {
        "stations": "basic/v3/Rail/TRA/Station",
        "timetable": "basic/v3/Rail/TRA/DailyTrainTimetable/OD/{origin}/to/{destination}/{date}",
        "fares": "basic/v3/Rail/TRA/ODFare/{origin}/to/{destination}",
        "station_rows": "Stations",
        "timetable_rows": "TrainTimetables",
        "fare_rows": "ODFares",
    },
}

# The high-speed rail station serving Kaohsiung is named Zuoying in TDX.
THSR_ALIASES = {"kaohsiung": "zuoying", "高雄": "左營", "高雄市": "左營"}
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d\Z")
PREFERENCES = ("earliest_arrival", "fastest", "cheapest", "earliest_departure")


def _error(message: str, hint: str, **extra) -> str:
    return json.dumps({"error": message, "hint": hint, **extra}, ensure_ascii=False)


def _rows(data: list | dict, field: str | None) -> list | dict:
    if isinstance(data, dict) and "error" in data:
        return data
    rows = data if field is None else data.get(field) if isinstance(data, dict) else None
    if isinstance(rows, list):
        return rows
    return {
        "error": "TDX returned an unexpected rail data format.",
        "hint": "Tell the user the rail lookup is unavailable right now; do not invent a schedule or fare.",
    }


def _normalize(name: str) -> str:
    name = " ".join(name.strip().lower().replace("台", "臺").replace("-", " ").split())
    for suffix in (" hsr station", " train station", " railway station", " station", " 車站", "站"):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    return name


def _station(name: str, stations: list[dict], rail: str) -> dict | None:
    wanted = _normalize(name)
    if rail == "THSR":
        wanted = _normalize(THSR_ALIASES.get(wanted, wanted))
    matches = [station for station in stations if wanted in {
        _normalize(station.get("StationID", "")),
        _normalize((station.get("StationName") or {}).get("En", "")),
        _normalize((station.get("StationName") or {}).get("Zh_tw", "")),
    }]
    return matches[0] if len(matches) == 1 else None


def _adult_standard_fare(fares: list[dict]) -> int | None:
    for fare in fares:
        if (fare.get("TicketType"), fare.get("FareClass"), fare.get("CabinClass")) == (1, 1, 1):
            price = fare.get("Price")
            if isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price) and price >= 0:
                return price
    return None


def _train_details(row: dict, rail: str, origin_id: str, destination_id: str) -> dict | None:
    if rail == "THSR":
        info = row.get("DailyTrainInfo") or {}
        start, end = row.get("OriginStopTime") or {}, row.get("DestinationStopTime") or {}
    else:
        info = row.get("TrainInfo") or {}
        if info.get("SuspendedFlag"):
            return None
        stops = row.get("StopTimes") or []
        start = next((stop for stop in stops if stop.get("StationID") == origin_id), {})
        end = next((stop for stop in stops if stop.get("StationID") == destination_id), {})
    if start.get("StationID") != origin_id or end.get("StationID") != destination_id:
        return None
    departure, arrival = start.get("DepartureTime"), end.get("ArrivalTime")
    if not info.get("TrainNo") or not departure or not arrival:
        return None
    try:
        start_time = dt.time.fromisoformat(departure)
        end_time = dt.time.fromisoformat(arrival)
    except ValueError:
        return None
    start_min = start_time.hour * 60 + start_time.minute
    end_min = end_time.hour * 60 + end_time.minute
    if end_min < start_min:
        end_min += 24 * 60
    return {
        "train_no": str(info["TrainNo"]),
        "departure": departure,
        "arrival": arrival,
        "duration_min": end_min - start_min,
        "train_type": ((info.get("TrainTypeName") or {}).get("En") or
                       (info.get("TrainTypeName") or {}).get("Zh_tw")) if rail == "TRA" else "High Speed Rail",
        "direction": info.get("Direction"),
        "fare_type": info.get("TrainTypeCode"),
    }


def _minutes(clock: str) -> int:
    time = dt.time.fromisoformat(clock)
    return time.hour * 60 + time.minute


def _arrival_minutes(train: dict) -> int:
    return _minutes(train["departure"]) + train["duration_min"]


def _rank_trains(trains: list[dict], preference: str) -> list[dict]:
    def key(train):
        departure, arrival = _minutes(train["departure"]), _arrival_minutes(train)
        duration, fare = train["duration_min"], train["fare_twd"]
        if preference == "fastest":
            return duration, arrival, departure, train["train_no"]
        if preference == "cheapest":
            return fare is None, fare if fare is not None else math.inf, duration, arrival, train["train_no"]
        if preference == "earliest_arrival":
            return arrival, duration, departure, train["train_no"]
        return departure, arrival, duration, train["train_no"]

    return sorted(trains, key=key)


def _comparison(trains: list[dict], picked: list[dict], preference: str, effective_preference: str) -> dict:
    best = picked[0]
    reasons = {
        "earliest_departure": f"First departure in the requested window: {best['departure']}.",
        "fastest": f"Shortest matching journey: {best['duration_min']} minutes.",
        "earliest_arrival": f"Earliest matching arrival: {best['arrival']} on {best['arrival_date']}.",
    }
    reason = (f"Lowest published fare among priced matches: TWD {best['fare_twd']:g}."
              if effective_preference == "cheapest" else reasons[effective_preference])
    if preference != effective_preference:
        reason = "Fares are unavailable; this train arrives earliest."
    alternatives = []
    for train in picked[1:]:
        duration_difference = train["duration_min"] - best["duration_min"]
        arrival_difference = _arrival_minutes(train) - _arrival_minutes(best)
        fare_difference = (train["fare_twd"] - best["fare_twd"]
                           if train["fare_twd"] is not None and best["fare_twd"] is not None else None)
        trade_offs = [f"{abs(duration_difference)} min {'longer' if duration_difference > 0 else 'shorter'} journey"
                      if duration_difference else "same journey length"]
        trade_offs.append(f"arrives {abs(arrival_difference)} min {'later' if arrival_difference > 0 else 'earlier'}"
                         if arrival_difference else "same arrival time")
        if fare_difference is not None:
            trade_offs.append(f"TWD {abs(fare_difference):g} {'more' if fare_difference > 0 else 'less'}"
                             if fare_difference else "same fare")
        alternatives.append({"train_no": train["train_no"], "duration_difference_min": duration_difference,
                             "arrival_difference_min": arrival_difference, "fare_difference_twd": fare_difference,
                             "trade_off": "; ".join(trade_offs)})
    known_fares = [t["fare_twd"] for t in trains if t["fare_twd"] is not None]
    return {"preference": preference, "effective_preference": effective_preference,
            "matching_train_count": len(trains), "returned_train_count": len(picked),
            "recommended_train_no": best["train_no"], "reason": reason,
            "same_fare": len(trains) > 1 and len(known_fares) == len(trains) and len(set(known_fares)) == 1,
            "alternatives": alternatives}


def hsr_trip_planner(
    origin: str,
    destination: str,
    date: str,
    depart_after: str | None = None,
    rail: str = "THSR",
    depart_before: str | None = None,
    arrive_by: str | None = None,
    preference: str = "earliest_arrival",
    limit: int = 3,
) -> str:
    """Rank the matching timetable by time/fare preferences, then return selected trains."""
    rail = rail.upper() if isinstance(rail, str) else ""
    if rail not in RAIL_PATHS:
        return _error(f"Unknown rail service '{rail}'.", "Use 'THSR' or 'TRA'.")
    if not isinstance(date, str) or not DATE_PATTERN.fullmatch(date):
        return _error("Date must be YYYY-MM-DD.", "Ask for a travel date in YYYY-MM-DD format.")
    try:
        travel_date = dt.date.fromisoformat(date)
    except ValueError:
        return _error(f"Invalid date '{date}'.", "Ask for a real calendar date in YYYY-MM-DD format.")
    for field, value in (("depart_after", depart_after), ("depart_before", depart_before), ("arrive_by", arrive_by)):
        if value is not None and (not isinstance(value, str) or not TIME_PATTERN.fullmatch(value)):
            return _error(f"{field} must be HH:MM in 24-hour time.", f"Use a time such as 09:30, or omit {field}.")
    if depart_after is not None and depart_before is not None and depart_after > depart_before:
        return _error("Departure window is inverted.", "Use depart_after at or before depart_before on the travel date.")
    if preference not in PREFERENCES:
        return _error("Unknown train preference.", "Use earliest_departure, fastest, cheapest, or earliest_arrival.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
        return _error("limit must be an integer from 1 to 10.", "Use the requested number of options, up to 10.")

    paths = RAIL_PATHS[rail]
    stations = _rows(tdx_get(paths["stations"], {"$top": 300}), paths["station_rows"])
    if isinstance(stations, dict):
        return json.dumps(stations, ensure_ascii=False)
    start_station = _station(origin, stations, rail)
    end_station = _station(destination, stations, rail)
    if not start_station or not end_station:
        choices = sorted({(s.get("StationName") or {}).get("En", "") for s in stations})
        examples = choices if rail == "THSR" else ["Taipei", "Taichung", "Tainan", "Kaohsiung"]
        return _error(
            f"Unknown or ambiguous {rail} station: {origin if not start_station else destination}.",
            "Use the station name, not just a county or nearby attraction. Ask the user to clarify the station.",
            example_stations=examples,
        )
    origin_id, destination_id = start_station["StationID"], end_station["StationID"]
    if origin_id == destination_id:
        return _error("Origin and destination are the same station.", "Ask for a different destination.")

    timetable_path = paths["timetable"].format(origin=origin_id, destination=destination_id, date=date)
    timetable = _rows(tdx_get(timetable_path, {}), paths["timetable_rows"])
    if isinstance(timetable, dict):
        return json.dumps(timetable, ensure_ascii=False)

    trains = []
    for row in timetable:
        train = _train_details(row, rail, origin_id, destination_id)
        if not train:
            continue
        departure, arrival = _minutes(train["departure"]), _arrival_minutes(train)
        if ((depart_after is not None and departure < _minutes(depart_after)) or
                (depart_before is not None and departure > _minutes(depart_before)) or
                (arrive_by is not None and arrival > _minutes(arrive_by))):
            continue
        train["arrival_date"] = (travel_date + dt.timedelta(days=arrival // 1440)).isoformat()
        trains.append(train)
    if not trains:
        return json.dumps({
            "rail": rail, "origin": origin, "destination": destination, "date": date,
            "trains": [],
            "depart_after": depart_after, "depart_before": depart_before, "arrive_by": arrive_by,
            "preference": preference,
            "hint": "No published trains match this date and time. Try an earlier time or a different date; "
                    "the requested date may be outside TDX's published timetable range.",
        }, ensure_ascii=False)

    fare_path = paths["fares"].format(origin=origin_id, destination=destination_id)
    fare_rows = _rows(tdx_get(fare_path, {}), paths["fare_rows"])
    fare_error = fare_rows if isinstance(fare_rows, dict) else None
    if fare_error:
        fare_rows = []  # A fare outage should not discard usable timetable options.

    thsr_fare = _adult_standard_fare(fare_rows[0].get("Fares", [])) if rail == "THSR" and fare_rows else None
    for train in trains:
        if rail == "THSR":
            train["fare_twd"] = thsr_fare
        else:
            matching = [row for row in fare_rows if row.get("Direction") == train["direction"]
                        and str(row.get("TrainType")) == str(train["fare_type"])]
            amounts = {_adult_standard_fare(row.get("Fares", [])) for row in matching}
            amounts.discard(None)
            train["fare_twd"] = amounts.pop() if len(amounts) == 1 else None
        del train["direction"]
        del train["fare_type"]

    known_fare_count = sum(t["fare_twd"] is not None for t in trains)
    effective_preference = "earliest_arrival" if preference == "cheapest" and not known_fare_count else preference
    picked = _rank_trains(trains, effective_preference)[:limit]
    result = {
        "rail": rail,
        "origin": (start_station.get("StationName") or {}).get("En") or origin,
        "destination": (end_station.get("StationName") or {}).get("En") or destination,
        "date": date,
        "depart_after": depart_after,
        "depart_before": depart_before,
        "arrive_by": arrive_by,
        "preference": preference,
        "trains": picked,
        "comparison": _comparison(trains, picked, preference, effective_preference),
        "fare_status": "available" if known_fare_count == len(trains) else "partial" if known_fare_count else "unavailable",
        "fare_note": "Published adult one-way standard-class fare in TWD; discounts and seat availability are not included.",
        "schedule_note": "Published timetable, not live delay or seat information.",
        "source": "Taiwan Transport Data eXchange (TDX)",
    }
    if fare_error:
        result["fare_error"] = fare_error
    if any(train["fare_twd"] is None for train in picked):
        result["hint"] = "Use the returned times to recommend a train; fare information is missing for some options."
    return json.dumps(result, ensure_ascii=False)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "hsr_trip_planner",
        "description": "Compare published high-speed rail (THSR) or Taiwan Railways (TRA) "
                       "trains between two stations on a date, with adult one-way standard-class fares. "
                       "Ranks the matching timetable by journey time, fare, departure, or arrival and returns "
                       "selected trains plus a recommendation and computed trade-offs. Default three options; "
                       "still returns schedules when fares are missing. This is not live seat availability.",
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Origin station name in English or Chinese, e.g. Taipei."},
                "destination": {"type": "string", "description": "Destination station name, e.g. Tainan. "
                                "For THSR travel to Kaohsiung, use Zuoying or Kaohsiung."},
                "date": {"type": "string", "description": "Travel date in YYYY-MM-DD format."},
                "depart_after": {"type": "string", "description": "Optional earliest departure, inclusive, in HH:MM on the travel date."},
                "depart_before": {"type": "string", "description": "Optional latest departure, inclusive, in HH:MM on the travel date."},
                "arrive_by": {"type": "string", "description": "Optional arrival deadline, inclusive HH:MM on the same travel date. Omit for next-day arrival deadlines."},
                "preference": {"type": "string", "enum": list(PREFERENCES),
                               "description": "Default earliest_arrival: get to the destination soonest; ties favor shorter journeys. "
                                              "Use fastest for shortest journey, cheapest for lowest known fare, "
                                              "or earliest_departure only when the user wants to leave as early as possible."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10,
                          "description": "Number of options requested, 1-10; default 3."},
                "rail": {"type": "string", "enum": ["THSR", "TRA"], "description": "Rail service; defaults to THSR."},
            },
            "required": ["origin", "destination", "date"],
        },
    },
}
