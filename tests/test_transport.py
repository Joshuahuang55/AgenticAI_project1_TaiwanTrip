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


@pytest.mark.parametrize("kwargs,expected", [({}, "0619"), ({"preference": "earliest_departure"}, "0813")])
def test_default_recommends_earlier_arrival_instead_of_first_departure(thsr_tdx, monkeypatch, kwargs, expected):
    original = transport.tdx_get

    def reported_options(path, params):
        if "/DailyTimetable/" in path:
            return [thsr_train("0813", "09:11", "11:11"),
                    thsr_train("0619", "09:21", "11:06"),
                    thsr_train("0621", "09:46", "11:32")]
        return original(path, params)

    monkeypatch.setattr(transport, "tdx_get", reported_options)
    out = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", "2026-10-12",
                                              depart_after="09:00", depart_before="12:00", **kwargs))
    assert out["comparison"]["recommended_train_no"] == expected
    assert out["trains"][0]["train_no"] == expected
    if not kwargs:
        assert out["preference"] == "earliest_arrival"
        assert out["comparison"]["alternatives"][0]["duration_difference_min"] == 15
        assert out["comparison"]["alternatives"][0]["arrival_difference_min"] == 5
        assert out["comparison"]["alternatives"][0]["fare_difference_twd"] == 0


def test_same_arrival_time_favors_shorter_journey(thsr_tdx, monkeypatch):
    original = transport.tdx_get

    def same_arrival(path, params):
        return [thsr_train("0801", "09:00", "11:00"),
                thsr_train("0803", "09:30", "11:00")] if "/DailyTimetable/" in path else original(path, params)

    monkeypatch.setattr(transport, "tdx_get", same_arrival)
    out = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", "2026-10-12"))
    assert out["comparison"]["recommended_train_no"] == "0803"


def test_fastest_uses_the_full_timetable_before_limiting(thsr_tdx):
    out = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", "2026-10-01", preference="fastest", limit=1))
    assert out["trains"][0]["train_no"] == "0811"
    assert out["comparison"]["matching_train_count"] == 5
    assert out["comparison"]["recommended_train_no"] == "0811"
    assert out["comparison"]["returned_train_count"] == 1
    assert out["comparison"]["same_fare"] is True
    assert len(thsr_tdx) == 3  # One station, timetable, and fare request; no per-train queries.


def test_requested_count_and_same_fare_comparison(thsr_tdx):
    out = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", "2026-10-01", limit=5))
    assert len(out["trains"]) == 5
    assert all(a["fare_difference_twd"] == 0 and "same fare" in a["trade_off"]
               for a in out["comparison"]["alternatives"])


def tra_train(number, departure, arrival, fare_type):
    return {"TrainInfo": {"TrainNo": number, "Direction": 0, "TrainTypeCode": fare_type,
                          "TrainTypeName": {"En": "Express"}},
            "StopTimes": [{"StationID": "1000", "DepartureTime": departure},
                          {"StationID": "4220", "ArrivalTime": arrival}]}


@pytest.fixture
def tra_tdx(monkeypatch):
    calls = []

    def fake(path, params):
        calls.append(path)
        if path.endswith("/Station"):
            return {"Stations": [
                {"StationID": "1000", "StationName": {"En": "Taipei", "Zh_tw": "臺北"}},
                {"StationID": "4220", "StationName": {"En": "Tainan", "Zh_tw": "臺南"}},
            ]}
        if "/DailyTrainTimetable/" in path:
            return {"TrainTimetables": [tra_train("104", "10:00", "13:00", 6),
                                       tra_train("101", "08:00", "12:00", 3),
                                       tra_train("105", "11:00", "16:00", 13),
                                       tra_train("103", "09:00", "11:00", 1),
                                       tra_train("102", "08:30", "12:10", 11)]}
        if "/ODFare/" in path:
            return {"ODFares": [{"Direction": 0, "TrainType": kind, "Fares": [
                {"TicketType": 1, "FareClass": 1, "CabinClass": 1, "Price": price}]}
                for kind, price in ((3, 950), (11, 600), (1, 1200), (13, 500))]}
        raise AssertionError(path)

    monkeypatch.setattr(transport, "tdx_get", fake)
    return calls


def test_cheapest_finds_later_train_and_never_treats_missing_fares_as_free(tra_tdx):
    out = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", "2026-10-01", rail="TRA", preference="cheapest", limit=5))
    assert [t["train_no"] for t in out["trains"]] == ["105", "102", "101", "103", "104"]
    assert out["fare_status"] == "partial"
    assert out["trains"][-1]["fare_twd"] is None
    assert "priced matches" in out["comparison"]["reason"]
    assert len(tra_tdx) == 3


def test_earliest_arrival_can_choose_a_later_departure_and_explain_tradeoffs(tra_tdx):
    out = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", "2026-10-01", rail="TRA", preference="earliest_arrival"))
    assert out["trains"][0]["train_no"] == "103"
    alternative = out["comparison"]["alternatives"][0]
    assert alternative["train_no"] == "101"
    assert alternative["arrival_difference_min"] == 60
    assert alternative["duration_difference_min"] == 120
    assert alternative["fare_difference_twd"] == -250
    assert "120 min longer" in alternative["trade_off"] and "TWD 250 less" in alternative["trade_off"]


def test_departure_window_and_arrival_deadline_are_applied_before_ranking(tra_tdx):
    out = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", "2026-10-01", rail="TRA",
                    depart_after="09:00", depart_before="10:00", arrive_by="11:00", preference="cheapest"))
    assert [t["train_no"] for t in out["trains"]] == ["103"]
    assert out["comparison"]["matching_train_count"] == 1


@pytest.mark.parametrize("preference,expected", [("fastest", "0811"), ("cheapest", "0803")])
def test_fare_outage_preserves_schedules_and_uses_a_useful_fallback(thsr_tdx, monkeypatch, preference, expected):
    original = transport.tdx_get

    def no_fares(path, params):
        return {"error": "TDX fare service unavailable", "hint": "retry later"} if "/ODFare/" in path else original(path, params)

    monkeypatch.setattr(transport, "tdx_get", no_fares)
    out = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", "2026-10-01", preference=preference))
    assert "error" not in out and out["trains"][0]["train_no"] == expected
    assert out["fare_status"] == "unavailable" and out["fare_error"]["error"]
    assert all(t["fare_twd"] is None for t in out["trains"])
    if preference == "cheapest":
        assert out["comparison"]["effective_preference"] == "earliest_arrival"


def test_overnight_arrival_dates_and_deadlines(tra_tdx, monkeypatch):
    original = transport.tdx_get

    def overnight(path, params):
        return {"TrainTimetables": [tra_train("201", "23:20", "00:40", 3),
                                   tra_train("202", "22:00", "23:30", 3)]} if "/DailyTrainTimetable/" in path else original(path, params)

    monkeypatch.setattr(transport, "tdx_get", overnight)
    out = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", "2026-12-31", rail="TRA", preference="fastest"))
    assert out["trains"][0]["train_no"] == "201"
    assert out["trains"][0]["arrival_date"] == "2027-01-01"
    assert out["trains"][0]["duration_min"] == 80
    out = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", "2026-12-31", rail="TRA", preference="earliest_arrival"))
    assert out["trains"][0]["train_no"] == "202"
    out = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", "2026-12-31", rail="TRA", arrive_by="23:59"))
    assert [t["train_no"] for t in out["trains"]] == ["202"]


def test_time_window_without_matches_skips_fare_request(thsr_tdx):
    out = json.loads(transport.hsr_trip_planner("Taipei", "Tainan", "2026-10-01", depart_after="20:00", arrive_by="21:00"))
    assert out["trains"] == [] and out["depart_after"] == "20:00"
    assert len(thsr_tdx) == 2


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
    ({"date": "2026-10-01", "depart_before": "24:00"}, "HH:MM"),
    ({"date": "2026-10-01", "arrive_by": "noon"}, "HH:MM"),
    ({"date": "2026-10-01", "depart_after": "12:00", "depart_before": "09:00"}, "inverted"),
    ({"date": "2026-10-01", "preference": "luxury"}, "preference"),
    ({"date": "2026-10-01", "limit": 0}, "limit"),
    ({"date": "2026-10-01", "limit": 11}, "limit"),
    ({"date": "2026-10-01", "limit": True}, "limit"),
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
