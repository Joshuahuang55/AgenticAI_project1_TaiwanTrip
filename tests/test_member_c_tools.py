"""Member C's tools: legal_stay_check, find_local_food, twd_exchange. Network is mocked."""

import json

import pytest

from tools import exchange, food, lodging, run_tool, tdx_client

HOTEL = {
    "HotelName": "你來花蓮民宿", "HotelLicenseNumber": "花蓮縣民宿2170號", "HotelClasses": [4],
    "PostalAddress": {"City": "花蓮縣", "Town": "花蓮市", "StreetAddress": "民樂三街14號"},
    "Telephones": [{"Tel": "0933799499"}], "LowestPrice": 2400, "CeilingPrice": 4800,
    "TaiwanHost": 1, "PositionLat": 23.99, "PositionLon": 121.63,
}
RESTAURANT = {
    "RestaurantName": "助仔牛肉湯", "Description": "台南牛肉湯", "ServiceTimeInfo": "04:30-14:00",
    "PostalAddress": {"City": "臺南市", "Town": "安平區", "StreetAddress": "安平路624號"},
    "Telephones": [], "PositionLat": 22.99, "PositionLon": 120.17,
}


@pytest.fixture
def fake_tdx(monkeypatch):
    """Replace tdx_get with a fake; returns the list of $filter strings it was called with."""
    calls, responses = [], []

    def fake(path, params):
        calls.append(params.get("$filter", ""))
        return responses.pop(0) if responses else []

    monkeypatch.setattr(lodging, "tdx_get", fake)
    monkeypatch.setattr(food, "tdx_get", fake)
    return calls, responses


def test_resolve_city_variants():
    assert tdx_client.resolve_city("Tainan") == ("臺南市", None)
    assert tdx_client.resolve_city("tainan city") == ("臺南市", None)
    assert tdx_client.resolve_city("台南") == ("臺南市", None)
    assert tdx_client.resolve_city("Jiufen") == ("新北市", "瑞芳區")
    assert tdx_client.resolve_city("Atlantis") is None


# --- legal_stay_check ---

def test_stay_registered(fake_tdx):
    calls, responses = fake_tdx
    responses.append([HOTEL])
    out = json.loads(lodging.legal_stay_check("Hualien", name="你來花蓮民宿"))
    assert out["is_registered"] is True
    assert out["matches"][0]["license_number"] == "花蓮縣民宿2170號"
    assert "contains(HotelName,'你來花蓮')" in calls[0]  # "民宿" stripped before matching


def test_stay_not_found_searches_all_taiwan_then_advises(fake_tdx):
    calls, _ = fake_tdx
    out = json.loads(lodging.legal_stay_check("Hualien", name="Sunny Homestay"))
    assert out["is_registered"] is False
    assert "license number" in out["advice"]
    assert len(calls) == 2 and "PostalAddress/City" not in calls[1]


def test_stay_unknown_city_lists_choices(fake_tdx):
    out = json.loads(lodging.legal_stay_check("Atlantis"))
    assert "error" in out and "Tainan" in out["valid_cities"]


def test_stay_list_filters_type_and_price(fake_tdx):
    calls, responses = fake_tdx
    responses.append([HOTEL])
    out = json.loads(lodging.legal_stay_check("Hualien", type="bnb", max_price_twd=3000))
    assert out["stays"][0]["taiwan_host_certified"] is True
    assert "HotelClasses/any(c: c eq 4)" in calls[0] and "LowestPrice le 3000" in calls[0]


def test_stay_passes_tdx_errors_through(fake_tdx):
    _, responses = fake_tdx
    responses.append({"error": "TDX rate limit reached", "hint": "wait"})
    assert json.loads(lodging.legal_stay_check("Hualien", type="hotel"))["hint"] == "wait"


# --- find_local_food ---

def test_food_translates_keyword(fake_tdx):
    calls, responses = fake_tdx
    responses.append([RESTAURANT, RESTAURANT])  # duplicate rows get merged
    out = json.loads(food.find_local_food("Tainan", keyword="beef soup"))
    assert out["searched_as"] == "牛肉湯" and len(out["results"]) == 1
    assert "牛肉湯" in calls[0]


def test_food_night_market_adds_local_tips(fake_tdx):
    out = json.loads(food.find_local_food("Tainan", keyword="night market"))
    assert out["kind"] == "night_market"
    assert any("花園夜市" in m["name"] for m in out["local_tips"])


def test_food_no_results_hints_chinese_keyword(fake_tdx):
    out = json.loads(food.find_local_food("Taipei", keyword="unicorn stew"))
    assert out["results"] == [] and "Traditional Chinese" in out["hint"]


# --- twd_exchange ---

@pytest.fixture
def fake_rates(monkeypatch):
    def fake(cur, date):
        if cur == "zzz":
            return {"date": "2026-09-28", "rates": {"usd": 1}}
        return {"date": "2026-09-28", "rates": {"twd": 32.0 if date == "latest" else 31.0}}
    monkeypatch.setattr(exchange, "_fetch", fake)


def test_exchange_to_twd(fake_rates):
    out = json.loads(exchange.twd_exchange(1500, "USD"))
    assert out["converted_amount"] == 48000
    assert out["avg_30d"] == pytest.approx(31.2) and out["diff_percent"] > 0


def test_exchange_from_twd(fake_rates):
    out = json.loads(exchange.twd_exchange(3200, "USD", "from_twd"))
    assert out["converted_amount"] == 100 and out["converted_currency"] == "USD"


def test_exchange_errors(fake_rates, monkeypatch):
    assert "hint" in json.loads(exchange.twd_exchange(100, "ZZZ"))
    assert "hint" in json.loads(exchange.twd_exchange(-5))
    assert "hint" in json.loads(exchange.twd_exchange(100, direction="sideways"))
    monkeypatch.setattr(exchange, "_fetch", lambda cur, date: None)
    assert "rate.bot.com.tw" in json.loads(exchange.twd_exchange(100))["hint"]


def test_run_tool_survives_bad_calls():
    assert "Unknown tool" in run_tool("teleport", {})
    assert "Bad arguments" in run_tool("twd_exchange", {"money": 5})
