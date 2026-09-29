"""Published THSR and TRA schedules and adult one-way fares from TDX."""

import datetime as dt
import json
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
            return fare.get("Price")
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
        "train_type": (info.get("TrainTypeName") or {}).get("En") if rail == "TRA" else "High Speed Rail",
        "direction": info.get("Direction"),
        "fare_type": info.get("TrainTypeCode"),
    }


def hsr_trip_planner(
    origin: str,
    destination: str,
    date: str,
    depart_after: str | None = None,
    rail: str = "THSR",
) -> str:
    """Return up to three published trains and adult one-way standard-class fares."""
    rail = rail.upper()
    if rail not in RAIL_PATHS:
        return _error(f"Unknown rail service '{rail}'.", "Use 'THSR' or 'TRA'.")
    if not DATE_PATTERN.fullmatch(date):
        return _error("Date must be YYYY-MM-DD.", "Ask for a travel date in YYYY-MM-DD format.")
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        return _error(f"Invalid date '{date}'.", "Ask for a real calendar date in YYYY-MM-DD format.")
    if depart_after is not None and not TIME_PATTERN.fullmatch(depart_after):
        return _error("depart_after must be HH:MM in 24-hour time.", "Ask for a time such as 09:30, or omit depart_after.")

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
        if train and (depart_after is None or train["departure"] >= depart_after):
            trains.append(train)
    trains.sort(key=lambda train: (train["departure"], train["train_no"]))
    trains = trains[:3]
    if not trains:
        return json.dumps({
            "rail": rail, "origin": origin, "destination": destination, "date": date,
            "trains": [],
            "hint": "No published trains match this date and time. Try an earlier time or a different date; "
                    "the requested date may be outside TDX's published timetable range.",
        }, ensure_ascii=False)

    fare_path = paths["fares"].format(origin=origin_id, destination=destination_id)
    fare_rows = _rows(tdx_get(fare_path, {}), paths["fare_rows"])
    if isinstance(fare_rows, dict):
        return json.dumps(fare_rows, ensure_ascii=False)

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

    result = {
        "rail": rail,
        "origin": (start_station.get("StationName") or {}).get("En") or origin,
        "destination": (end_station.get("StationName") or {}).get("En") or destination,
        "date": date,
        "depart_after": depart_after,
        "trains": trains,
        "fare_note": "Published adult one-way standard-class fare in TWD; discounts and seat availability are not included.",
        "schedule_note": "Published timetable, not live delay or seat information.",
        "source": "Taiwan Transport Data eXchange (TDX)",
    }
    if any(train["fare_twd"] is None for train in trains):
        result["hint"] = "A matching fare was not available in TDX for every train; do not guess missing prices."
    return json.dumps(result, ensure_ascii=False)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "hsr_trip_planner",
        "description": "Find up to three published high-speed rail (THSR) or Taiwan Railways (TRA) "
                       "trains between two stations on a date, with adult one-way standard-class fares. "
                       "Use for train schedules, travel times, and fares; this is not live seat availability.",
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Origin station name in English or Chinese, e.g. Taipei."},
                "destination": {"type": "string", "description": "Destination station name, e.g. Tainan. "
                                "For THSR travel to Kaohsiung, use Zuoying or Kaohsiung."},
                "date": {"type": "string", "description": "Travel date in YYYY-MM-DD format."},
                "depart_after": {"type": "string", "description": "Optional earliest departure in 24-hour HH:MM format."},
                "rail": {"type": "string", "enum": ["THSR", "TRA"], "description": "Rail service; defaults to THSR."},
            },
            "required": ["origin", "destination", "date"],
        },
    },
}
