"""Name matching and scoring in scripts/build_food_fame.py. No network."""

from scripts import build_food_fame as bf


def _place(pid, name, city="臺北市", lat=25.0, lon=121.5, en=None):
    p = {"id": pid, "n": name, "city": city, "lat": lat, "lon": lon}
    if en:
        p["en"] = en
    p["forms"] = bf.name_forms(name)
    return p


def test_name_forms_drop_branch_notes_and_split_scripts():
    assert bf.name_forms("橋頭臭豆腐（玉里店／礁溪店）") == {"橋頭臭豆腐"}
    assert bf.name_forms("晶華軒 Silks House") == {"晶華軒silkshouse", "晶華軒", "silkshouse"}
    assert "名家滷肉飯" in bf.name_forms("名家魯肉飯") and "臺南擔仔麵" in bf.name_forms("台南擔仔麵")


def test_names_match_needs_most_of_a_long_enough_name():
    assert bf.names_match(bf.name_forms("林家乾麵"), bf.name_forms("林家乾麵 中正店"))
    assert bf.names_match({"文章牛肉湯"}, {"文章牛肉湯安平"})
    assert not bf.names_match({"阿銘牛肉麵"}, {"牛肉麵"})
    assert not bf.names_match({"老麵店"}, {"麵店"})


def test_awards_match_in_their_cities_and_skip_nameless_stalls():
    rows = [{"name": "林家乾麵", "city": "台北", "bowls": "2", "year": "2023"},
            {"name": "林家乾麵", "city": "台北", "bowls": "2", "year": "2026"},
            {"name": "無名米粉湯", "city": "新北", "bowls": "1", "year": "2026"},
            {"name": "橋頭臭豆腐（玉里店／礁溪店）", "city": "宜蘭/花蓮", "bowls": "3", "year": "2025"}]
    awards = bf.award_totals(rows, "bowls")
    by_county = {"臺北市": [_place("a", "林家乾麵")], "新北市": [_place("b", "無名米粉湯", "新北市")],
                 "宜蘭縣": [_place("c", "橋頭臭豆腐礁溪店", "宜蘭縣")], "花蓮縣": [_place("d", "橋頭臭豆腐", "花蓮縣")]}
    found = bf.match_awards(awards, by_county)
    assert sorted(found) == ["a", "c", "d"]
    assert found["a"][0]["total"] == 4 and found["a"][0]["years"] == {2023, 2026}


def test_michelin_matches_by_chinese_name_nearby_or_the_countys_only_namesake():
    entries = [{"Name": "Lin Dong Fang", "Url": "u1", "Latitude": "25.0", "Longitude": "121.5", "Award": "Bib Gourmand",
                "Location": "Taipei, Taiwan Region"},
               {"Name": "logy", "Url": "u2", "Latitude": "25.07", "Longitude": "121.57", "Award": "2 Stars",
                "Location": "Taipei, Taiwan Region"}]
    places = [_place("a", "林東芳牛肉麵", lat=25.0005), _place("b", "別家", lat=25.0001), _place("c", "logy", lat=25.03)]
    found = bf.match_michelin(entries, places, {"u1": "林東芳牛肉麵"})
    assert {k: v["Url"] for k, v in found.items()} == {"a": "u1", "c": "u2"}


def test_local_score_is_lowered_by_michelin_and_for_chains():
    places = [_place("a", "小攤"), _place("b", "名店"), _place("c", "連鎖")]
    michelin = {"b": {"Award": "2 Stars", "Name": "Famous"}}
    bowls = {"a": [{"name": "小攤", "total": 6, "years": {2024, 2025}}], "c": [{"name": "連鎖", "total": 6, "years": {2025}}]}
    dishes = {"b": [{"name": "名店", "total": 12, "years": {2025}}]}
    out = bf.scores(places, michelin, dishes, bowls, chain_names={"連鎖"})
    assert out["a"]["local"] == 1.0 and out["b"]["local"] < 0.1 and out["c"]["local"] == 0.3
    assert out["b"]["awards"] == ["Michelin Guide Taiwan: 2 Stars", "500盤 2025"] and out["b"]["name_en"] == "Famous"


def test_renames_join_one_award_and_old_or_closed_awards_count_less():
    rows = [{"name": "牡丹 天ぷら", "city": "台北", "plates": "11", "year": "2022"},
            {"name": "牡丹．極上 天ぷら", "city": "", "plates": "5", "year": "2026"}]
    (award,) = bf.award_totals(rows, "plates")
    assert award["total"] == 16 and award["years"] == {2022, 2026} and award["city"] == "台北"
    assert bf.recency({2025}) == 1 and bf.recency({2022, 2024}) == 0.7 and bf.recency({2022}) == 0.5
    places = [_place("a", "RAW")]
    out = bf.scores(places, {}, {"a": [{"name": "RAW", "total": 15, "years": {2024}}]}, {}, set())
    assert out["a"] == {"fame": 0.0, "local": 0.0, "closed": True}


def test_fine_dining_is_a_star_a_high_price_or_500_dishes_alone():
    places = [_place(i, n) for i, n in (("a", "星"), ("b", "盤"), ("c", "盤碗"), ("d", "盤便宜"), ("e", "碗"))]
    michelin = {"a": {"Award": "1 Star", "Name": "A", "Price": "$$$$"}, "d": {"Award": "Bib Gourmand", "Name": "D", "Price": "$"}}
    dish = lambda n: [{"name": n, "total": 5, "years": {2026}}]
    out = bf.scores(places, michelin, {"b": dish("盤"), "c": dish("盤碗"), "d": dish("盤便宜")},
                    {"c": dish("盤碗"), "e": dish("碗")}, set())
    assert {k for k, v in out.items() if v.get("fine_dining")} == {"a", "b"}


def test_well_known_places_without_awards_get_fame_but_no_award():
    places = [_place("a", "阿宗麵線"), _place("b", "林家乾麵")]
    known = bf.match_known([{"county": "臺北市", "name": "阿宗麵線", "known_for": "大腸麵線"}],
                           {"臺北市": places})
    out = bf.scores(places, {}, {}, {"b": [{"name": "林家乾麵", "total": 6, "years": {2026}}]}, set(), known)
    assert out["a"] == {"fame": bf.KNOWN_FAME, "local": 0.0, "known_for": "大腸麵線"}
    assert "known_for" not in out["b"] and out["b"]["fame"] == 1.0
