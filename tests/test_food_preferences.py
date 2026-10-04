"""Preference comparisons with competing strengths, absent facts, and conflicting evidence."""

import json
import time

import pytest

from tools import attractions, food, food_preferences as fp, tourism_data


def row(i, name, town="萬華區", district_status="reported", cuisine=None, **tags):
    return food._osm_row({"id": f"osm:n{i}", "n": name, "lat": 25.03, "lon": 121.5,
                          "city": "臺北市", "t": town, "ts": district_status,
                          "c": cuisine, "dv": tags.get("vegetarian"), "dg": tags.get("vegan")})


@pytest.fixture
def candidates(monkeypatch):
    tourism_data._data["restaurants"] = {"loaded_at": time.time(), "parts": {"rows": []}}
    monkeypatch.setattr(food, "_seen_by_session", {})
    attractions.use_session("food-comparison-test")
    places = [row(1, "獲獎肉食店", vegetarian="no", vegan="no"),
              row(2, "平價素食店", vegetarian="yes"),
              row(3, "未確認素食店"),
              row(4, "純素店", town="大安區", district_status="estimated", vegan="only"),
              row(5, "標籤小店", vegan="limited")]
    monkeypatch.setattr(food, "OSM_BY_COUNTY", {"臺北市": places})
    monkeypatch.setattr(food, "FOOD_FAME", {
        "osm:n1": {"fame": 1, "awards": ["Michelin Guide Taiwan: 1 Star"], "price": "$$$$"},
        "osm:n2": {"fame": 0.1, "price": "$"},
        "osm:n3": {"fame": 0.9, "awards": ["500碗 2026"]},
        "osm:n4": {"fame": 0.5, "price": "$$"},
        "osm:n5": {"fame": 0.2, "price": "$"},
    })

    def forbid(*args, **kwargs):
        pytest.fail("Comparison checks must not make live TDX requests")

    monkeypatch.setattr(food, "tdx_get", forbid)
    return places


def search(**kwargs):
    return json.loads(food.find_local_food("Taipei", **kwargs))


def test_reported_preference_fit_beats_awards_and_unknown_facts(candidates):
    assert search(limit=1)["results"][0]["name"] == "獲獎肉食店"
    out = search(dietary="vegetarian", price_preference="budget", district="萬華區", limit=1)
    assert out["results"][0]["name"] == "平價素食店"
    assert out["results"][0]["comparison"]["fit"] == "reported_match"
    assert out["comparison_summary"]["reported_matches"] == 1
    assert any(r["name"] == "獲獎肉食店" for r in out["excluded"])
    assert "未確認素食店" not in [r["name"] for r in out["results"]]


def test_keyword_dietary_search_finds_tags_even_without_diet_in_the_name(candidates):
    out = search(keyword="vegan")
    assert out["criteria"]["dietary"] == "vegan"
    assert "標籤小店" in [r["name"] for r in out["results"]]
    assert "獲獎肉食店" not in [r["name"] for r in out["results"]]
    best = out["results"][0]
    assert best["facts"]["vegan"]["status"] == "reported"


def test_chinese_diet_keyword_does_not_drop_places_with_only_tag_evidence(candidates):
    out = search(keyword="素", dietary="vegetarian")
    assert "標籤小店" in [r["name"] for r in out["results"]]


def test_confirmed_only_excludes_uncertain_names_but_preserves_reported_matches(candidates):
    out = search(dietary="vegetarian", price_preference="budget", confirmed_only=True)
    assert {r["name"] for r in out["results"]} == {"平價素食店", "標籤小店"}
    assert out["comparison_summary"]["excluded_unconfirmed"] == 1
    assert all("name" not in r for r in out["excluded"] if r["kind"] == "unconfirmed")
    out = search(names=["未確認素食店"], dietary="vegetarian", confirmed_only=True)
    assert out["results"] == [] and out["comparison_summary"]["excluded_unconfirmed"] == 1


def test_exact_cap_with_confirmed_only_cannot_produce_confirmed_candidates(candidates):
    out = search(dietary="vegan", max_price_twd=300, confirmed_only=True)
    assert out["results"] == [] and out["comparison_summary"]["reported_matches"] == 0
    assert out["comparison_summary"]["excluded_unconfirmed"] > 0


def test_vegetarian_evidence_is_not_a_vegan_match(candidates):
    out = search(dietary="vegan", price_preference="budget")
    by_name = {r["name"]: r for r in out["results"]}
    assert by_name["平價素食店"]["facts"]["vegan"]["status"] == "unknown"
    assert by_name["平價素食店"]["comparison"]["fit"] == "needs_confirmation"
    assert out["results"][0]["name"] == "標籤小店"
    assert "limited" in out["results"][0]["comparison"]["checks"]["dietary"]["reason"]


def test_name_or_cuisine_is_only_an_indication(candidates):
    out = search(names=["未確認素食店"], dietary="vegetarian")
    result = out["results"][0]
    assert result["facts"]["vegetarian"]["status"] == "indication"
    assert result["comparison"]["fit"] == "needs_confirmation"
    assert out["comparison_summary"]["reported_matches"] == 0
    facts = fp.facts_for(row(6, "小館", cuisine="vegan"), {}, "OpenStreetMap", food.AWARD_SOURCE)
    assert facts["vegan"]["status"] == "indication"


def test_dietary_indication_beats_cheap_award_winner_without_dietary_evidence(candidates, monkeypatch):
    places = [row(10, "獲獎牛肉麵", district_status="estimated"),
              row(11, "素食自助餐", district_status="estimated"),
              row(12, "蔬食小館", district_status="estimated")]
    monkeypatch.setattr(food, "OSM_BY_COUNTY", {"臺北市": places})
    monkeypatch.setattr(food, "FOOD_FAME", {"osm:n10": {"fame": 1, "price": "$"}})
    out = search(district="萬華區", dietary="vegetarian", price_preference="budget", limit=2)
    assert {r["name"] for r in out["results"]} == {"素食自助餐", "蔬食小館"}
    assert out["criteria"]["confirmed_only"] is False
    assert out["criteria"]["max_price_twd"] is None
    assert all(r["comparison"]["fit"] == "needs_confirmation" for r in out["results"])
    assert all(r["comparison"]["checks"]["dietary"]["evidence_status"] == "indication"
               for r in out["results"])


def test_reported_dietary_options_beat_indications_with_more_price_evidence(candidates, monkeypatch):
    places = [row(10, "素食小館"), row(11, "標籤小館", vegetarian="limited")]
    monkeypatch.setattr(food, "OSM_BY_COUNTY", {"臺北市": places})
    monkeypatch.setattr(food, "FOOD_FAME", {"osm:n10": {"fame": 1, "price": "$"}})
    out = search(dietary="vegetarian", price_preference="budget", limit=1)
    assert out["results"][0]["name"] == "標籤小館"


def test_estimated_district_is_not_a_verified_area_or_proximity_match(candidates):
    result = search(district="大安區", dietary="vegan")["results"][0]
    assert result["facts"]["district"]["status"] == "estimated"
    assert result["comparison"]["checks"]["district"]["status"] == "unknown"
    assert result["comparison"]["fit"] == "needs_confirmation"


def test_price_band_never_verifies_an_exact_budget(candidates):
    out = search(dietary="vegetarian", price_preference="budget", max_price_twd=300)
    assert out["comparison_summary"]["reported_matches"] == 0
    assert all(r["comparison"]["checks"]["max_price_twd"]["status"] == "unknown" for r in out["results"])
    price = next(r for r in out["results"] if r["name"] == "平價素食店")["facts"]["price"]
    assert price["value"] == "$" and price["exact_twd"] is None


def test_exact_budget_prefers_lower_price_leads_without_claiming_a_match(candidates):
    out = search(max_price_twd=300)
    assert out["results"][0]["price"] == "$"
    assert out["results"][-1]["facts"]["price"]["status"] == "unknown"
    assert all(r["comparison"]["fit"] == "needs_confirmation" for r in out["results"])


def test_names_lookup_keeps_constraints_and_reports_conflicting_options(candidates):
    out = search(names=["獲獎肉食店", "平價素食店", "不存在店"], dietary="vegetarian", price_preference="budget")
    assert [r["name"] for r in out["results"]] == ["平價素食店"]
    assert [r["name"] for r in out["excluded"]] == ["獲獎肉食店"]
    assert out["not_found"] == ["不存在店"]
    assert out["results"][0]["facts"]["price"]["source"] == food.PRICE_SOURCE


def test_no_matches_is_not_a_lookup_failure_or_silent_relaxation(candidates):
    out = search(names=["獲獎肉食店"], dietary="vegan")
    assert out["results"] == [] and "error" not in out
    assert out["comparison_summary"]["excluded_conflicts"] == 1


def test_unknown_price_is_not_cheap_or_expensive(candidates):
    result = search(names=["未確認素食店"], price_preference="budget")["results"][0]
    assert result["facts"]["price"]["status"] == "unknown"
    assert result["comparison"]["checks"]["price_preference"]["status"] == "unknown"
    assert "price" in result["missing_fields"] and "price" not in result


def test_conflicting_dietary_tags_do_not_establish_either_diet():
    facts = fp.facts_for(row(1, "小店", vegetarian="no", vegan="yes"), {}, "OpenStreetMap", food.AWARD_SOURCE)
    for diet in fp.DIETARY:
        assert facts[diet]["status"] == "conflicting"
        assert facts[diet]["source"] == "OpenStreetMap"
        assert fp.compare(facts, dietary=diet)["fit"] == "needs_confirmation"


def test_unknown_tag_values_do_not_count_as_dietary_evidence():
    facts = fp.facts_for(row(1, "小店", vegan="maybe"), {}, "OpenStreetMap", food.AWARD_SOURCE)
    assert facts["vegan"]["status"] == "unknown"


def test_merged_record_preserves_the_source_of_borrowed_facts(candidates, monkeypatch):
    official = {"RestaurantID": "R1", "RestaurantName": "平價素食店", "PositionLat": 25.03, "PositionLon": 121.5,
                "PostalAddress": {"City": "臺北市", "Town": "萬華區"}, "ServiceTimeInfo": None}
    tourism_data._data["restaurants"]["parts"]["rows"] = [official]
    twin = dict(candidates[1], ServiceTimeInfo="Mo-Su 10:00-20:00")
    monkeypatch.setattr(food, "OSM_BY_COUNTY", {"臺北市": [twin]})
    result = search(dietary="vegetarian")["results"][0]
    assert result["source"] == tourism_data.SOURCE
    assert result["facts"]["vegetarian"]["source"] == "OpenStreetMap"
    assert result["facts"]["opening_hours"]["source"] == "OpenStreetMap"
    assert result["facts"]["district"]["status"] == "reported"


def test_missing_official_file_does_not_claim_exhaustive_names_coverage(candidates):
    tourism_data._data.clear()
    out = search(names=["平價素食店", "不存在店"])
    assert "unavailable" in out["coverage_note"] and "not exhaustive" not in out["coverage_note"]
    assert "does not exist" in out["coverage_note"]
    assert "Tourism Administration" not in out["results"][0]["source"]


@pytest.mark.parametrize("kwargs", [
    {"dietary": "gluten_free"}, {"price_preference": "luxury"}, {"max_price_twd": 0},
    {"max_price_twd": -1}, {"max_price_twd": float("nan")}, {"max_price_twd": float("inf")},
    {"max_price_twd": True}, {"max_price_twd": "300"},
    {"confirmed_only": "yes"},
])
def test_invalid_constraints_fail_before_requests(kwargs):
    out = search(**kwargs)
    assert "error" in out and "hint" in out
