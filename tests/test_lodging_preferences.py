"""Preference ranking, reference-rate boundaries, and existing registration/UI contracts."""

import json

import pytest

from tools import lodging, lodging_preferences, run_tool


def stay(name, price=None, ceiling=None, certified=False, town="萬華區", classes=(3,), **extra):
    return {"HotelID": name, "HotelName": name, "HotelLicenseNumber": f"臺北旅館{name}",
            "HotelClasses": list(classes), "ServiceStatus": 1,
            "PostalAddress": {"City": "臺北市", "Town": town, "StreetAddress": "測試路"},
            "LowestPrice": price, "CeilingPrice": ceiling, "TaiwanHost": int(certified),
            "PositionLat": 25.04, "PositionLon": 121.51, "Telephones": [{"Tel": "02-12345678"}],
            **extra}


CHEAP = stay("簡約旅館", 1200, 2800)
CERTIFIED = stay("好客旅館", 1800, 3500, certified=True)
UNKNOWN = stay("未標價旅館", 0, certified=True)
BNB = stay("好客民宿", 1000, 2500, certified=True, classes=(4,))
OTHER_AREA = stay("中山旅館", 800, 2000, town="中山區")


@pytest.fixture
def registry(monkeypatch):
    calls, rows = [], [CERTIFIED, UNKNOWN, CHEAP, BNB, OTHER_AREA]
    def fake(path, params):
        calls.append((path, params))
        return rows
    monkeypatch.setattr(lodging, "tdx_get", fake)
    return calls, rows


def search(**kwargs):
    return json.loads(lodging.legal_stay_check("Taipei", **kwargs))


def test_budget_ranks_pool_before_limit_and_certification(registry):
    calls, rows = registry
    rows[:] = [CERTIFIED, UNKNOWN, CHEAP]
    result = search(price_preference="budget", limit=1)
    assert result["stays"][0]["name"] == CHEAP["HotelName"]
    assert result["comparison"]["recommended_name"] == CHEAP["HotelName"]
    assert result["comparison"]["matching_candidate_count"] == 3
    assert calls[0][1]["$top"] == 500 and len(calls) == 1
    assert "LowestPrice" not in calls[0][1]["$filter"]  # No cap invented for cheap alone.


def test_default_retains_certification_preference(registry):
    _, rows = registry
    rows[:] = [CHEAP, UNKNOWN, CERTIFIED]
    result = search(limit=3)
    assert result["comparison"]["recommended_name"] == CERTIFIED["HotelName"]
    assert result["stays"][1]["name"] == UNKNOWN["HotelName"]


def test_numeric_cap_keeps_unknown_prices_but_excludes_known_higher_starts(registry):
    calls, rows = registry
    rows[:] = [CERTIFIED, UNKNOWN, CHEAP]
    result = search(max_price_twd=1500, limit=3)
    assert [s["name"] for s in result["stays"]] == [CHEAP["HotelName"], UNKNOWN["HotelName"]]
    assert "LowestPrice le 1500" in calls[0][1]["$filter"]
    assert "LowestPrice eq null" in calls[0][1]["$filter"]
    assert result["stays"][0]["preference_match"]["budget"] == "reference_start_within_cap"
    assert result["stays"][1]["preference_match"]["budget"] == "unknown"


def test_district_and_type_constraints_rank_only_qualifying_stays(registry):
    calls, _ = registry
    result = search(district="萬華區", type="hotel", price_preference="budget")
    assert [s["name"] for s in result["stays"]] == [CHEAP["HotelName"], CERTIFIED["HotelName"], UNKNOWN["HotelName"]]
    assert "PostalAddress/Town eq '萬華區'" in calls[0][1]["$filter"]
    assert "HotelClasses/any(c: c eq 3)" in calls[0][1]["$filter"]
    assert all(s["district"] == "萬華區" for s in result["stays"])


def test_reference_price_difference_and_certification_tradeoff(registry):
    _, rows = registry
    rows[:] = [CERTIFIED, CHEAP]
    result = search(price_preference="budget")
    alt = result["comparison"]["alternatives"][0]
    assert alt["name"] == CERTIFIED["HotelName"]
    assert "Reported starting rate NT$600 higher" in alt["trade_offs"]
    assert "Marked Taiwan Host certified" in alt["trade_offs"]
    assert result["stays"][0]["price_range_twd"]["maximum"] == 2800
    assert result["stays"][0]["price_range_twd"]["booking_price"] is None


def test_budget_start_with_high_ceiling_does_not_claim_entire_range_meets_cap(registry):
    _, rows = registry
    rows[:] = [CHEAP]
    result = search(max_price_twd=1500)
    assert result["stays"][0]["reference_price_twd"] == "1200-2800"
    assert "Starting reference rate" in result["comparison"]["reason"]
    assert "not prices or availability" in result["comparison"]["price_note"]


def test_all_missing_rates_still_provide_suggestions_and_pick(registry):
    _, rows = registry
    rows[:] = [stay("無標價甲旅館", certified=True), stay("無標價乙旅館", price=0)]
    result = search(price_preference="budget", max_price_twd=1500)
    assert len(result["stays"]) == 2
    assert result["comparison"]["recommended_name"] == "無標價甲旅館"
    assert all(s["reference_price_twd"] is None for s in result["stays"])
    assert all(s["preference_match"]["budget"] == "unknown" for s in result["stays"])
    assert "free" not in result["comparison"]["reason"].lower()


def test_closed_duplicates_and_other_city_do_not_fill_answer(registry):
    _, rows = registry
    rows[:] = [dict(CERTIFIED, ServiceStatus=3), dict(CHEAP), dict(CHEAP),
               dict(BNB, PostalAddress={"City": "臺南市", "Town": "中西區"})]
    result = search()
    assert [s["name"] for s in result["stays"]] == [CHEAP["HotelName"]]
    assert result["comparison"]["fetched_candidate_count"] == 4
    assert result["comparison"]["matching_candidate_count"] == 1


def test_empty_constrained_search_never_silently_broadens(registry):
    calls, rows = registry
    rows[:] = [OTHER_AREA, CERTIFIED]
    result = search(district="萬華區", max_price_twd=1000)
    assert result["stays"] == [] and result["comparison"]["recommended_name"] is None
    assert result["preferences"]["district"] == "萬華區" and result["preferences"]["max_price_twd"] == 1000
    assert result["hint"] and len(calls) == 1


def test_pool_metadata_reports_truncation_without_claiming_citywide_best(registry):
    _, rows = registry
    rows[:] = [stay(f"旅館{i}", price=1000 + i) for i in range(500)]
    result = search(price_preference="budget", limit=2)
    assert len(result["stays"]) == 2
    assert result["comparison"]["candidate_pool_may_be_capped"] is True
    assert result["comparison"]["matching_candidate_count"] == 500
    assert len(result["comparison"]["alternatives"]) == 1


def test_name_check_ignores_list_preferences_and_keeps_registered_result(registry):
    calls, rows = registry
    rows[:] = [dict(CERTIFIED, ServiceStatus=3)]
    result = search(name=CERTIFIED["HotelName"], district="中山區", max_price_twd=1000, price_preference="budget")
    assert result["mode"] == "check" and result["is_registered"] is True
    assert result["matches"][0]["license_number"] == CERTIFIED["HotelLicenseNumber"]
    assert "PostalAddress/Town" not in calls[0][1]["$filter"]
    assert "LowestPrice" not in calls[0][1]["$filter"]


def test_existing_map_and_ui_fields_remain(registry):
    _, rows = registry
    rows[:] = [CHEAP]
    data = search()["stays"][0]
    assert data["name"] and data["license_number"] and data["license_type"] and data["address"]
    assert data["reference_price_twd"] == "1200-2800"
    assert data["lat"] == 25.04 and data["lon"] == 121.51


@pytest.mark.parametrize("value", [None, 0, -1, True, "not a rate", float("nan"), float("inf")])
def test_invalid_starting_rates_are_unknown_not_free(value):
    row = stay("測試旅館", price=value)
    assert lodging_preferences.price_range(row)["minimum"] is None
    assert lodging_preferences.fit(row, None, 1500)["budget"] == "unknown"


def test_reversed_price_range_and_false_certificate_normalize_safely():
    row = stay("測試旅館", price="2400", ceiling=1200, TaiwanHost="0")
    summary = lodging._summarize(row)
    assert summary["reference_price_twd"] == 2400
    assert summary["price_range_twd"]["maximum"] is None
    assert summary["taiwan_host_certified"] is False


@pytest.mark.parametrize("kwargs", [{"max_price_twd": 0}, {"max_price_twd": -1},
                                   {"max_price_twd": True}, {"max_price_twd": 1500.5},
                                   {"price_preference": "luxury"}, {"district": ""},
                                   {"district": 123}, {"type": "villa"}])
def test_bad_preferences_fail_before_http(registry, kwargs):
    calls, _ = registry
    result = search(**kwargs)
    assert result["error"] and result["hint"] and calls == []


def test_network_error_is_passed_through(monkeypatch):
    monkeypatch.setattr(lodging, "tdx_get", lambda *args: {"error": "TDX rate limited", "hint": "later"})
    result = json.loads(run_tool("legal_stay_check", {"city": "Taipei", "price_preference": "budget"}))
    assert result == {"error": "TDX rate limited", "hint": "later"}


@pytest.mark.parametrize("message,expected", [
    ("Recommend two and explain which fits better.", 2),
    ("Hotels only, under NT$1,500. Please recommend 3 options.", 3),
    ("Show me the top five stays.", 5), ("請推薦兩家旅館", 2),
    ("推薦兩家。改成推薦三家。", 3), ("不要推薦兩家", None),
    ("推薦兩個人的房型", None), ("推薦兩間雙人房", None),
    ("Recommend budget stays for 2 guests, 3 nights, under NT$1500.", None),
    ("Give me two nights in Taipei.", None), ("Recommend three-star hotels.", None),
    ("Recommend two-night stays.", None), ("Recommend two or three options.", None),
    ("Show me 2.5 stars or better.", None),
])
def test_requested_counts_do_not_confuse_guests_nights_or_prices(message, expected):
    assert lodging_preferences.requested_limit(message) == expected
