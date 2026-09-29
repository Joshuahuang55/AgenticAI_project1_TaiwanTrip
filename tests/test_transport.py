"""Rail planner contracts, with TDX responses mocked at the shared client boundary."""

import json

import pytest

from tools import run_tool, transport


def thsr_train(number, departure, arrival):
    return {
        "DailyTrainInfo": {"TrainNo": number, "Direction": 0},
        "OriginStopTime": {"StationID": "1000", "DepartureTime": departure},
        "DestinationStopTime": {"StationID": "1060", "ArrivalTime": arrival},
    }


@pytest.fixture
def thsr_tdx(monkeypatch):
    calls = []

    def fake(path, params):
        calls.append((path, params))
        if path.endswith("/Station"):
            return [
                {"StationID": "1000", "StationName": {"En": "Taipei", "Zh_tw": "台北"}},
                {"StationID": "1060", "StationName": {"En": "Tainan", "Zh_tw": "台南"}},
                {"StationID": "1070", "StationName": {"En": "Zuoying", "Zh_tw": "左營"}},
            ]
        if "/DailyTimetable/" in path:
            return [
                thsr_train("0809", "10:30", "12:30"),
                thsr_train("0803", "06:26", "08:26"),
                thsr_train("0811", "12:30", "14:20"),
                thsr_train("0807", "08:30", "10:35"),
                thsr_train("0813", "14:30", "16:20"),
            ]
        if "/ODFare/" in path:
            return [{"Fares": [
                {"TicketType": 1, "FareClass": 1, "CabinClass": 2, "Price": 2230},
                {"TicketType": 1, "FareClass": 1, "CabinClass": 1, "Price": 1350},
            ]}]
        raise AssertionError(f"Unexpected TDX path: {path}")

    monkeypatch.setattr(transport, "tdx_get", fake)
    return calls


def test_hsr_filters_sorts_and_uses_standard_adult_fare(thsr_tdx):
    out = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", "2026-10-01", "08:00"))
    assert [train["train_no"] for train in out["trains"]] == ["0807", "0809", "0811"]
    assert out["trains"][0]["duration_min"] == 125
    assert all(train["fare_twd"] == 1350 for train in out["trains"])
    assert "/OD/1000/to/1060/2026-10-01" in thsr_tdx[1][0]
    assert "/ODFare/1000/to/1060" in thsr_tdx[2][0]


def test_thsr_kaohsiung_alias_resolves_to_zuoying(thsr_tdx):
    json.loads(transport.hsr_trip_planner("Taipei", "Kaohsiung", "2026-10-01"))
    assert "/OD/1000/to/1070/2026-10-01" in thsr_tdx[1][0]


def test_tra_uses_train_type_and_direction_for_fare(monkeypatch):
    def fake(path, params):
        if path.endswith("/Station"):
            return {"Stations": [
                {"StationID": "1000", "StationName": {"En": "Taipei", "Zh_tw": "臺北"}},
                {"StationID": "4220", "StationName": {"En": "Tainan", "Zh_tw": "臺南"}},
            ]}
        if "/DailyTrainTimetable/" in path:
            return {"TrainTimetables": [{
                "TrainInfo": {"TrainNo": "103", "Direction": 1, "TrainTypeCode": "3",
                              "TrainTypeName": {"En": "Tze-Chiang Express"}},
                "StopTimes": [
                    {"StationID": "1000", "DepartureTime": "06:27"},
                    {"StationID": "4220", "ArrivalTime": "10:56"},
                ],
            }]}
        if "/ODFare/" in path:
            return {"ODFares": [
                {"Direction": 0, "TrainType": 3, "Fares": [
                    {"TicketType": 1, "FareClass": 1, "CabinClass": 1, "Price": 1389}]},
                {"Direction": 1, "TrainType": 11, "Fares": [
                    {"TicketType": 1, "FareClass": 1, "CabinClass": 1, "Price": 930}]},
                {"Direction": 1, "TrainType": 3, "Fares": [
                    {"TicketType": 1, "FareClass": 1, "CabinClass": 1, "Price": 891}]},
            ]}
        raise AssertionError(path)

    monkeypatch.setattr(transport, "tdx_get", fake)
    out = json.loads(transport.hsr_trip_planner("臺北", "Tainan", "2026-10-01", rail="TRA"))
    assert out["rail"] == "TRA"
    assert out["trains"][0]["fare_twd"] == 891
    assert out["trains"][0]["duration_min"] == 269


@pytest.mark.parametrize("kwargs,expected", [
    ({"date": "2026-02-30"}, "Invalid date"),
    ({"date": "10/01/2026"}, "YYYY-MM-DD"),
    ({"date": "2026-10-01", "depart_after": "25:00"}, "HH:MM"),
    ({"date": "2026-10-01", "rail": "MRT"}, "Unknown rail service"),
])
def test_bad_inputs_do_not_call_tdx(monkeypatch, kwargs, expected):
    monkeypatch.setattr(transport, "tdx_get", lambda path, params: pytest.fail("TDX should not be called"))
    out = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", **kwargs))
    assert expected in out["error"] and out["hint"]


def test_unknown_and_same_station_stop_before_timetable(thsr_tdx):
    unknown = json.loads(transport.hsr_trip_planner("Atlantis", "Tainan", "2026-10-01"))
    assert "Unknown" in unknown["error"] and unknown["example_stations"]
    same = json.loads(transport.hsr_trip_planner("Taipei", "Taipei", "2026-10-01"))
    assert "same station" in same["error"]
    assert all(path.endswith("/Station") for path, _ in thsr_tdx)


def test_empty_timetable_and_tdx_failure(monkeypatch, thsr_tdx):
    original = transport.tdx_get

    def no_trains(path, params):
        return [] if "/DailyTimetable/" in path else original(path, params)

    monkeypatch.setattr(transport, "tdx_get", no_trains)
    empty = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", "2026-10-01"))
    assert empty["trains"] == [] and "hint" in empty
    assert not any("/ODFare/" in path for path, _ in thsr_tdx)

    monkeypatch.setattr(transport, "tdx_get", lambda path, params: {"error": "TDX unavailable", "hint": "retry later"})
    failure = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", "2026-10-01"))
    assert failure == {"error": "TDX unavailable", "hint": "retry later"}


def test_tool_is_registered_without_network():
    out = json.loads(run_tool("hsr_trip_planner", {
        "origin": "Taipei", "destination": "Tainan", "date": "bad",
    }))
    assert "YYYY-MM-DD" in out["error"]
