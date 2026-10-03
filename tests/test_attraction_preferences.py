"""Competing sightseeing choices, outing budgets, and source/pin contracts; no network."""

import json

import pytest

from tools import attraction_preferences as preferences
from tools import attractions, tourism_data


def place(aid, name, classes, lat=25.04, lon=121.51):
    return {"AttractionID": aid, "AttractionName": name, "AttractionClasses": classes,
            "ServiceStatus": 1, "PositionLat": lat, "PositionLon": lon,
            "PostalAddress": {"City": "臺北市", "Town": "中正區", "StreetAddress": "測試路"}}


MUSEUM = place("museum", "歷史博物館", [3, 25])
GALLERY = place("gallery", "城市美術館", [5, 25], lon=121.515)
PARK = place("park", "城市公園", [19], lat=25.12, lon=121.60)
TEMPLE = place("temple", "古蹟龍山寺", [3, 4], lon=121.516)
TRAIL = place("trail", "森林登山步道", [13, 16], lat=25.15, lon=121.61)


@pytest.fixture
def listings(monkeypatch):
    rows = [PARK, TRAIL, MUSEUM, GALLERY, TEMPLE]
    monkeypatch.setattr(tourism_data, "listings", lambda *args, **kwargs: rows)
    monkeypatch.setattr(attractions, "_lookup", lambda *args: {})
    monkeypatch.setattr(attractions, "FAME", {"park": 1, "trail": .95, "temple": .8, "museum": .3, "gallery": .2})
    monkeypatch.setattr(attractions, "_seen_by_session", {})
    attractions.use_session("preferences-test")
    return rows


def search(**kwargs):
    return json.loads(attractions.find_attractions("Taipei", **kwargs))


def test_interests_beat_fame_and_local_gem_injection(listings, monkeypatch):
    monkeypatch.setattr(attractions, "LOCAL_FAME", {"park": 1, "trail": 1})
    result = search(interests=["history"], limit=5)
    assert [r["name"] for r in result["results"][:2]] == [TEMPLE["AttractionName"], MUSEUM["AttractionName"]]
    assert result["comparison"]["recommended_name"] == TEMPLE["AttractionName"]
    assert not any(r.get("local_gem") for r in result["results"])


def test_indoor_history_pick_and_useful_art_alternative(listings):
    result = search(interests=["history", "art"], setting="indoor", limit=3)
    assert [r["name"] for r in result["results"][:2]] == [MUSEUM["AttractionName"], GALLERY["AttractionName"]]
    comparison = result["comparison"]
    assert comparison["recommended_name"] == MUSEUM["AttractionName"]
    assert "history" in comparison["reason"] and "indoor" in comparison["reason"]
    assert "Adds art" in comparison["alternatives"][0]["trade_offs"]
    assert comparison["alternatives"][0]["equally_suitable"] is True
    assert "Equally matches" in comparison["alternatives"][0]["trade_offs"][0]
    assert comparison["alternatives"][1]["equally_suitable"] is False


def test_short_outing_prefers_fitting_visit_not_famous_long_trail(listings):
    result = search(interests=["nature", "hiking"], available_minutes=80)
    assert result["comparison"]["recommended_name"] == PARK["AttractionName"]
    plan = result["comparison"]["suggested_visit"]
    assert plan["names"] == [PARK["AttractionName"]]
    assert plan["estimated_minutes"] <= 80
    trail = next(r for r in result["results"] if r["name"] == TRAIL["AttractionName"])
    assert trail["preference_match"]["fits_typical_visit"] is False


def test_nearby_outing_budget_includes_transfer_and_separates_alternatives(listings):
    result = search(interests=["culture"], available_minutes=180)
    plan = result["comparison"]["suggested_visit"]
    assert plan["names"] == [TEMPLE["AttractionName"], MUSEUM["AttractionName"]]
    assert plan["estimated_minutes"] == 45 + 90 + 20
    assert plan["transfer_allowance_minutes"] == 20
    assert PARK["AttractionName"] not in plan["names"]
    assert GALLERY["AttractionName"] not in plan["names"]  # Third stop would exceed budget.
    group = next(g for g in result["comparison"]["nearby_groups"] if TEMPLE["AttractionName"] in g["names"])
    assert MUSEUM["AttractionName"] in group["names"] and PARK["AttractionName"] not in group["names"]
    assert group["max_straight_line_km"] <= 2


def test_indoor_outing_does_not_add_mixed_temple(listings):
    result = search(interests=["history", "art"], setting="indoor", available_minutes=240)
    assert result["comparison"]["suggested_visit"]["names"] == [MUSEUM["AttractionName"], GALLERY["AttractionName"]]


def test_five_hour_indoor_art_and_nature_outing_includes_city_transfer(listings):
    nature = dict(place("natural", "自然科學博物館", [25], lat=25.05, lon=121.51), Description="自然史與動植物展覽")
    art = place("art", "市立美術館", [5, 25], lat=25.10, lon=121.53)
    listings[:] = [art, nature]
    result = search(interests=["art", "nature"], setting="indoor", available_minutes=300)
    plan = result["comparison"]["suggested_visit"]
    assert len(plan["names"]) == 2
    assert 280 <= plan["estimated_minutes"] <= 300
    assert plan["estimated_minutes"] + plan["remaining_minutes"] == 300
    assert plan["transfer_allowance_minutes"] == 35
    assert plan["break_minutes"] == 30
    assert result["comparison"]["nearby_groups"] == []  # A city transfer is not a nearby claim.
    timeline = plan["timeline"]
    assert {s["kind"] for s in timeline} == {"visit", "transfer", "break"}
    assert all(a["end_minute"] == b["start_minute"] for a, b in zip(timeline, timeline[1:]))
    assert timeline[-1]["end_minute"] == plan["estimated_minutes"]
    assert timeline[0]["relative_time"].startswith("0:00–")
    assert timeline[-1]["relative_time"].endswith("5:00")
    assert all(60 <= s["estimated_minutes"] <= 120 for s in timeline if s["kind"] == "visit")


def test_mixed_creative_park_is_optional_indoor_art_stop_not_indoor_nature(listings):
    creative = place("creative", "華山文化創意產業園區", [25], lon=121.54)
    listings[:] = [GALLERY, creative]
    result = search(interests=["art", "nature"], setting="indoor", available_minutes=300)
    plan = result["comparison"]["suggested_visit"]
    assert len(plan["names"]) == 2
    creative_stop = next(s for s in plan["timeline"] if s.get("name") == creative["AttractionName"])
    assert creative_stop["setting"] == "mixed" and "outdoor grounds optional" in creative_stop["focus"]
    assert "nature" not in preferences.profile(creative)["interests"]


def test_memorial_and_garden_are_not_reclassified_as_indoor_art_and_nature():
    memorial = place("memorial", "國立中正紀念堂", [3, 25])
    info = preferences.profile(memorial)
    assert info["setting"] == "mixed"
    assert "art" not in info["interests"] and "nature" not in info["interests"]
    generic_museum = dict(MUSEUM, Description="博物館附近有公園和花園")
    assert "nature" not in preferences.profile(generic_museum)["interests"]


def test_no_coordinates_use_transfer_estimate_without_nearby_claim(listings):
    listings[:] = [MUSEUM, dict(GALLERY, PositionLat=None)]
    result = search(interests=["culture"], available_minutes=300)
    plan = result["comparison"]["suggested_visit"]
    assert len(plan["names"]) == 2 and plan["estimated_minutes"] <= 300
    transfer = next(s for s in plan["timeline"] if s["kind"] == "transfer")
    assert transfer["straight_line_km"] is None and transfer["status"] == "estimated"
    assert result["comparison"]["nearby_groups"] == []


def test_unavailable_hours_and_fees_do_not_block_pick_or_invent_free_entry(listings):
    result = search(interests=["museums"], setting="indoor")
    pick = result["results"][0]
    assert result["comparison"]["recommended_name"] == MUSEUM["AttractionName"]
    assert pick["open_time"] is None and pick["ticket_info"] is None
    assert pick["planning"]["status"] == "estimated"
    assert pick["planning"]["visit_minutes"] == {"min": 60, "max": 120, "typical": 90}
    assert "do not prevent recommendations" in result["note"]


def test_names_lookups_keep_preferences_and_use_seen_cache(listings, monkeypatch):
    search()
    monkeypatch.setattr(attractions, "tdx_get", lambda *args: pytest.fail("Unexpected live/fallback request"))
    result = search(names=[PARK["AttractionName"], MUSEUM["AttractionName"]],
                    interests=["history"], setting="indoor", available_minutes=120)
    assert result["results"][0]["name"] == MUSEUM["AttractionName"]
    assert result["preferences"]["interests"] == ["history"]
    assert result["comparison"]["suggested_visit"]["names"] == [MUSEUM["AttractionName"]]


def test_only_answered_names_get_search_map_pins(listings):
    result = search(interests=["history"], setting="indoor")
    assert all("lat" not in r and "lon" not in r for r in result["results"])
    pins = attractions.pins_from_answer(f"Choose the History Museum ({MUSEUM['AttractionName']}).")
    assert [p["name"] for p in pins] == [MUSEUM["AttractionName"]]


def test_no_typical_visit_fits_still_returns_useful_alternatives(listings):
    result = search(interests=["history"], available_minutes=15)
    assert result["results"] and result["comparison"]["recommended_name"]
    assert result["comparison"]["suggested_visit"]["names"] == []
    assert "more than your available time" in result["comparison"]["reason"]


def test_unknown_setting_and_interest_remain_eligible(listings):
    unknown = place("unknown", "城市景點", [])
    listings[:] = [unknown]
    result = search(interests=["art"], setting="indoor", available_minutes=120)
    assert result["results"][0]["preference_match"]["setting_fit"] == "unknown"
    assert result["comparison"]["recommended_name"] == unknown["AttractionName"]


def test_closed_famous_match_is_not_recommended(listings):
    listings[:] = [dict(MUSEUM, ServiceStatus=3), GALLERY]
    result = search(interests=["museums"], setting="indoor")
    assert result["comparison"]["recommended_name"] == GALLERY["AttractionName"]


def test_museum_name_overrides_scenic_category_and_palace_is_not_temple():
    profile = preferences.profile(place("palace", "國立故宮博物院", [25, 8]))
    assert profile["setting"] == "indoor" and "temples" not in profile["interests"]
    assert "nature" not in profile["interests"]


def test_nearby_groups_do_not_chain_or_guess_from_district():
    a = place("a", "甲館", [], lon=121.50)
    b = place("b", "乙館", [], lon=121.515)
    c = place("c", "丙館", [], lon=121.53)
    missing = dict(a, AttractionName="缺座標", PositionLat=None)
    groups = preferences.nearby_groups([a, b, c, missing])
    assert groups[0]["names"] == ["甲館", "乙館"]
    assert "丙館" not in groups[0]["names"] and "缺座標" not in groups[0]["names"]


@pytest.mark.parametrize("coordinate", [None, "25.04", float("nan"), float("inf"), 91, True])
def test_invalid_coordinates_never_claim_nearby(coordinate):
    assert preferences.distance_km(dict(MUSEUM, PositionLat=coordinate), GALLERY) is None


@pytest.mark.parametrize("kwargs", [{"interests": "history"}, {"interests": ["unknown"]},
                                   {"setting": "both"}, {"available_minutes": 0},
                                   {"available_minutes": 721}, {"available_minutes": True},
                                   {"available_minutes": 90.5}])
def test_bad_preferences_return_hint_before_any_request(monkeypatch, kwargs):
    monkeypatch.setattr(attractions, "search_rows", lambda *args: pytest.fail("Should validate first"))
    monkeypatch.setattr(attractions, "_details", lambda *args: pytest.fail("Should validate first"))
    result = search(**kwargs)
    assert result["error"] and result["hint"]
