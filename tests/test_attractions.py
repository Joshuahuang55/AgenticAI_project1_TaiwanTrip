"""Member B's find_attractions tool. Network is mocked."""

import json

import pytest

from tools import attractions

TEMPLE = {
    "AttractionID": "A1", "AttractionName": "南鯤鯓代天府", "Description": "全臺規模最大的王爺信仰中心廟宇",
    "AttractionClasses": [1, 3, 4], "ServiceStatus": 1, "ServiceTimeInfo": "", "FeeInfo": "",
    "PostalAddress": {"City": "臺南市", "Town": "北門區", "StreetAddress": "鯤江976號"},
    "Telephones": [{"Tel": "(06)7863711"}], "PositionLat": 23.28, "PositionLon": 120.14,
}
PORT = {
    "AttractionID": "A2", "AttractionName": "將軍漁港", "Description": "台南沿海最大的漁港",
    "AttractionClasses": [12], "ServiceStatus": 1,
    "PostalAddress": {"City": "臺南市", "Town": "將軍區", "StreetAddress": "平沙里156號"},
    "Telephones": [], "PositionLat": 23.21, "PositionLon": 120.08,
}
SHRINE = dict(PORT, AttractionID="A3", AttractionName="東隆宮文化中心", AttractionClasses=[1],
              PostalAddress={"City": "臺南市", "Town": "北門區", "StreetAddress": "三寮灣127-3號"})
CLOSED_TEMPLE = dict(TEMPLE, AttractionID="A4", AttractionName="已歇業廟", ServiceStatus=0)
HOURS = {"AttractionID": "A1", "ServiceTimes": [
    {"ServiceDays": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
     "StartTime": "08:00:00", "EndTime": "17:00:00"},
    {"ServiceDays": ["Saturday", "Sunday", "PublicHolidays"], "StartTime": "07:00:00", "EndTime": "18:00:00"},
]}
FEES = {"AttractionID": "A1", "Fees": [{"Name": "全票", "Price": 100}, {"Name": "半票", "Price": 50}]}


@pytest.fixture(autouse=True)
def fresh_session(monkeypatch):
    """Each test starts in its own empty chat session."""
    monkeypatch.setattr(attractions, "_seen_by_session", {})
    attractions.use_session("test")


@pytest.fixture
def fake_tdx(monkeypatch):
    """Replace tdx_get with a fake that answers by endpoint; returns (calls, data)."""
    calls = []
    data = {attractions.ATTRACTION_PATH: [], attractions.SERVICE_TIME_PATH: [], attractions.FEE_PATH: []}

    def fake(path, params):
        calls.append((path, params))
        return data[path]

    monkeypatch.setattr(attractions, "tdx_get", fake)
    return calls, data


def test_english_keyword_becomes_chinese_filter(fake_tdx):
    calls, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [TEMPLE, SHRINE]
    out = json.loads(attractions.find_attractions("Tainan", keyword="temples", limit=2))
    assert [r["name"] for r in out["results"]] == ["東隆宮文化中心", "南鯤鯓代天府"]  # name match first
    assert out["searched_as"] == "廟, 宮, 寺"
    path, params = calls[0]
    assert path == attractions.ATTRACTION_PATH and params["$top"] == attractions.RANK_POOL
    assert params["$filter"].startswith("PostalAddress/City eq '臺南市' and (contains(AttractionName,'廟')")
    assert "contains(Description,'寺')" in params["$filter"]


def test_chinese_keyword_is_used_as_is(fake_tdx):
    calls, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [PORT]
    out = json.loads(attractions.find_attractions("Tainan", keyword="漁港"))
    assert out["searched_as"] is None
    assert "contains(AttractionName,'漁港')" in calls[0][1]["$filter"]


def test_hours_fees_and_categories_are_merged(fake_tdx):
    calls, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [TEMPLE]
    data[attractions.SERVICE_TIME_PATH] = [HOURS]
    data[attractions.FEE_PATH] = [FEES]
    r = json.loads(attractions.find_attractions("Tainan"))["results"][0]
    assert r["open_time"] == "Mon-Fri 08:00-17:00; Sat-Sun, public holidays 07:00-18:00"
    assert r["ticket_info"] == "全票 NT$100; 半票 NT$50"
    assert r["categories"] == ["Culture", "Heritage site", "Temple/religious site"]
    assert r["address"] == "臺南市北門區鯤江976號"
    assert "lat" not in r  # search results are candidates: no map pin until picked by name
    # Hours and fees: whole (small) endpoints, fetched without a filter so the cache serves every search.
    assert [(p, "$filter" in q) for p, q in calls[1:]] == [
        (attractions.SERVICE_TIME_PATH, False), (attractions.FEE_PATH, False)]


def test_missing_or_failed_hours_and_fees_become_null(fake_tdx):
    _, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [TEMPLE]
    data[attractions.FEE_PATH] = {"error": "TDX rate limit reached", "hint": "later"}
    out = json.loads(attractions.find_attractions("Tainan"))
    assert out["results"][0]["open_time"] is None
    assert out["results"][0]["ticket_info"] is None
    assert "only source for hours and prices" in out["note"]


def test_closed_places_are_skipped(fake_tdx):
    _, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [CLOSED_TEMPLE, TEMPLE]
    out = json.loads(attractions.find_attractions("Tainan", keyword="temple"))
    assert [r["name"] for r in out["results"]] == ["南鯤鯓代天府"]


def test_district_and_limit(fake_tdx):
    calls, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [TEMPLE, PORT, SHRINE]
    attractions.find_attractions("台南", district="北門區")
    assert calls[0][1]["$filter"] == "PostalAddress/City eq '臺南市' and PostalAddress/Town eq '北門區'"
    out = json.loads(attractions.find_attractions("Tainan", limit=99))
    assert len(out["results"]) == 3  # limit clamped to 10, not an error
    out = json.loads(attractions.find_attractions("Tainan", limit=0))
    assert len(out["results"]) == 1


def test_unknown_english_keyword_hints_chinese(fake_tdx):
    calls, _ = fake_tdx  # TDX finds nothing for the English word
    out = json.loads(attractions.find_attractions("Tainan", keyword="skatepark"))
    assert "contains(AttractionName,'skatepark')" in calls[0][1]["$filter"]
    assert out["results"] == []
    assert "Traditional Chinese" in out["hint"]


def test_unknown_city_lists_choices():
    out = json.loads(attractions.find_attractions("Atlantis"))
    assert "Unknown city" in out["error"] and "Tainan" in out["valid_cities"]


def test_tdx_error_is_passed_through(fake_tdx):
    _, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = {"error": "TDX request failed: HTTPError", "hint": "try later"}
    out = json.loads(attractions.find_attractions("Tainan"))
    assert out["error"].startswith("TDX request failed")


def test_days_text():
    assert attractions._days_text(list(attractions.WEEK)) == "Daily"
    assert attractions._days_text(["Monday", "Wednesday", "TyphoonDay"]) == "Mon, Wed, typhoon days"


def test_ticket_info_free():
    assert attractions._ticket_info([{"Name": "免費", "Price": 0}]) == "Free"
    assert attractions._ticket_info([{"Name": "全票", "Price": 200}, {"Name": "免費", "Price": 0}]) == (
        "全票 NT$200; free for eligible visitors")
    assert attractions._ticket_info([]) is None


def test_phrase_merges_known_words(fake_tdx):
    calls, _ = fake_tdx
    out = json.loads(attractions.find_attractions("Taipei", keyword="Mountain Trails"))
    assert out["results"] == [] and "Traditional Chinese" not in out["hint"]
    assert "contains(AttractionName,'登山')" in calls[0][1]["$filter"]
    assert "contains(AttractionName,'步道')" in calls[0][1]["$filter"]


def test_rank_prefers_name_match_then_documented(fake_tdx):
    _, data = fake_tdx
    mention = dict(PORT, AttractionID="B1", AttractionName="河濱公園", Description="可連接步道")
    bare = dict(PORT, AttractionID="B2", AttractionName="象山步道", Description="短")
    rich = dict(bare, AttractionID="B3", AttractionName="虎山步道", Description="長" * 80, Images=[{"URL": "x"}])
    data[attractions.ATTRACTION_PATH] = [mention, bare, rich]
    out = json.loads(attractions.find_attractions("Taipei", keyword="trail"))
    assert [r["name"] for r in out["results"]] == ["虎山步道", "象山步道", "河濱公園"]


def test_spread_takes_one_per_series_first(fake_tdx):
    _, data = fake_tdx
    trail = lambda i, name, town: dict(PORT, AttractionID=f"T{i}", AttractionName=name,
                                       PostalAddress={"City": "臺北市", "Town": town, "StreetAddress": str(i)})
    data[attractions.ATTRACTION_PATH] = [
        trail(1, "大屯山系_忠義山親山步道", "北投區"), trail(2, "大屯山系_中正山步道", "北投區"),
        trail(3, "南港山系_象山親山步道", "信義區"), trail(4, "碧湖步道", "內湖區"), trail(5, "大湖公園步道", "內湖區"),
    ]
    out = json.loads(attractions.find_attractions("Taipei", keyword="trail", limit=4))
    assert [r["name"] for r in out["results"]] == [
        "大屯山系_忠義山親山步道", "南港山系_象山親山步道", "碧湖步道", "大湖公園步道"]  # one district twice
    assert out["more_candidates"] == {"北投區": ["大屯山系_中正山步道"]}  # the series' second trail waits


def test_fame_ranks_well_known_places_first(fake_tdx, monkeypatch):
    _, data = fake_tdx
    temple = lambda i, name: dict(TEMPLE, AttractionID=f"F{i}", AttractionName=name)
    data[attractions.ATTRACTION_PATH] = [temple(1, "西港慶安宮"), temple(2, "臺灣祀典武廟"), temple(3, "大天后宮")]
    monkeypatch.setattr(attractions, "FAME", {"F2": 0.97, "F3": 0.99})
    out = json.loads(attractions.find_attractions("Tainan", keyword="temple"))
    assert [r["name"] for r in out["results"]] == ["大天后宮", "臺灣祀典武廟", "西港慶安宮"]


def test_nature_searches_by_kind_not_by_the_word_nature(fake_tdx, monkeypatch):
    _, data = fake_tdx
    place = lambda i, name, classes: dict(PORT, AttractionID=f"N{i}", AttractionName=name, AttractionClasses=classes)
    data[attractions.ATTRACTION_PATH] = [
        place(1, "蜜蜂生態教育館", [2]), place(2, "三仙台風景區", [2, 8, 12]), place(3, "清水斷崖", [12]),
        place(4, "某大學", [18]), place(5, "八仙洞", [3, 8, 11]), place(6, "綠島人權紀念園區", [8])]
    monkeypatch.setattr(attractions, "FAME", {"N5": 0.97, "N2": 0.95})
    out = json.loads(attractions.find_attractions("Taitung", keyword="nature"))
    assert [r["name"] for r in out["results"]] == ["八仙洞", "三仙台風景區", "清水斷崖"]  # by fame; no eco centre, campus
    assert out["searched_as"].startswith("natural scenery")


def test_coast_is_searched_with_chinese_words(fake_tdx):
    calls, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [dict(PORT, AttractionName="外澳海灘")]
    out = json.loads(attractions.find_attractions("Yilan", keyword="coast"))
    assert [r["name"] for r in out["results"]] == ["外澳海灘"] and "contains(AttractionName,'海岸')" in calls[0][1]["$filter"]


def test_local_style_ranks_reviewed_favorites_then_local_fame(fake_tdx, monkeypatch):
    _, data = fake_tdx
    temple = lambda i, name: dict(TEMPLE, AttractionID=f"L{i}", AttractionName=name)
    data[attractions.ATTRACTION_PATH] = [temple(1, "龍山寺"), temple(2, "小巷廟"), temple(3, "老街廟")]
    monkeypatch.setattr(attractions, "FAME", {"L1": 0.99, "L2": 0.5, "L3": 0.6})
    monkeypatch.setattr(attractions, "LOCAL_FAME", {"L1": 0.05, "L2": 0.5, "L3": 0.6})
    monkeypatch.setattr(attractions, "LOCAL_FAVORITES", {"L2": "local_favorite"})
    must = json.loads(attractions.find_attractions("Taipei", keyword="temple"))
    assert [r["name"] for r in must["results"]] == ["龍山寺", "老街廟", "小巷廟"] and must["style"] == "must_see"
    local = json.loads(attractions.find_attractions("Taipei", keyword="temple", style="local"))
    assert [r["name"] for r in local["results"]] == ["小巷廟", "老街廟", "龍山寺"]
    assert local["results"][0]["local_favorite"] is True and "local_favorite" not in local["results"][1]


def test_must_see_results_end_with_local_gems(fake_tdx, monkeypatch):
    _, data = fake_tdx
    temple = lambda i, name: dict(TEMPLE, AttractionID=f"M{i}", AttractionName=f"{name}廟")
    data[attractions.ATTRACTION_PATH] = [temple(i, f"名{i}") for i in range(1, 8)] + [temple(8, "巷"), temple(9, "海")]
    monkeypatch.setattr(attractions, "FAME", {f"M{i}": 1 - i / 10 for i in range(1, 8)})
    monkeypatch.setattr(attractions, "LOCAL_FAME", {"M9": 0.9, "M7": 0.2})
    monkeypatch.setattr(attractions, "LOCAL_FAVORITES", {"M8": "local_favorite"})
    out = json.loads(attractions.find_attractions("Taipei", keyword="temple", limit=5))
    names = [r["name"] for r in out["results"]]
    assert names == ["名1廟", "名2廟", "名3廟", "巷廟", "海廟"]  # 3 best known, then 2 gems
    assert [r.get("local_gem", False) for r in out["results"]] == [False, False, False, True, True]
    small = json.loads(attractions.find_attractions("Taipei", keyword="temple", limit=3))
    assert not any(r.get("local_gem") for r in small["results"])  # too few slots to spare


def test_local_picks_hold_at_most_a_third_temples_unless_temples_were_asked():
    rows = [{"AttractionName": n} for n in ("甲廟", "乙宮", "丙寺", "某湖", "某街", "某島")]
    assert [r["AttractionName"] for r in attractions._few_temples(rows, 3, ())] == [
        "甲廟", "某湖", "某街", "某島", "乙宮", "丙寺"]
    assert attractions._few_temples(rows, 3, ("廟",)) == rows


def test_dedupe_drops_a_listing_named_inside_another():
    row = lambda name, town="安平區": {"AttractionName": name, "PostalAddress": {"Town": town}}
    rows = [row("安平古堡"), row("臺灣城殘蹟(安平古堡內牆)"), row("台南林百貨", "中西區"), row("臺南林百貨", "中西區"),
            row("天后宮", "中西區"), row("鹿港天后宮", "鹿港鎮")]
    assert [r["AttractionName"] for r in attractions._dedupe(rows)] == ["安平古堡", "台南林百貨", "天后宮", "鹿港天后宮"]


def test_unknown_style_is_an_error_with_a_hint():
    out = json.loads(attractions.find_attractions("Taipei", style="cheap"))
    assert "error" in out and "local" in out["hint"]


def test_a_name_match_still_beats_fame(fake_tdx, monkeypatch):
    _, data = fake_tdx
    famous_mention = dict(PORT, AttractionID="G1", AttractionName="安平古堡", Description="旁邊有廟")
    temple = dict(TEMPLE, AttractionID="G2", AttractionName="小廟")
    data[attractions.ATTRACTION_PATH] = [famous_mention, temple]
    monkeypatch.setattr(attractions, "FAME", {"G1": 1.0})
    out = json.loads(attractions.find_attractions("Tainan", keyword="temple"))
    assert [r["name"] for r in out["results"]] == ["小廟", "安平古堡"]


def test_names_expands_picks_from_recent_search_without_tdx(fake_tdx):
    calls, data = fake_tdx
    xiangshan = dict(PORT, AttractionID="X1", AttractionName="南港山系_象山親山步道",
                     PostalAddress={"City": "臺北市", "Town": "信義區", "StreetAddress": "信義路五段150巷"})
    data[attractions.ATTRACTION_PATH] = [xiangshan]
    attractions.find_attractions("Taipei", keyword="trail")
    n = len(calls)
    out = json.loads(attractions.find_attractions("Taipei", names=["象山親山步道"]))
    assert [r["name"] for r in out["results"]] == ["南港山系_象山親山步道"]
    assert out["results"][0]["lat"] == 23.21  # picks carry coordinates for the map pin
    assert out["not_found"] == []
    assert all(path != attractions.ATTRACTION_PATH for path, _ in calls[n:])  # served from memory


def test_names_falls_back_to_one_tdx_query(fake_tdx, monkeypatch):
    calls, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [TEMPLE]
    out = json.loads(attractions.find_attractions("Tainan", names=["南鯤鯓代天府", "不存在的地方"]))
    assert [r["name"] for r in out["results"]] == ["南鯤鯓代天府"]
    assert out["not_found"] == ["不存在的地方"]
    assert calls[0][1]["$filter"] == "PostalAddress/City eq '臺南市'"  # the whole city first
    assert "contains(AttractionName,'不存在的地方')" in calls[1][1]["$filter"]  # then only the misses
    assert "南鯤鯓代天府" not in calls[1][1]["$filter"]


NIGHT_MARKET = dict(PORT, AttractionID="T1", AttractionName="士林觀光夜市",
                    PostalAddress={"City": "臺北市", "Town": "士林區", "StreetAddress": "基河路101號"})
LONGSHAN = dict(PORT, AttractionID="T2", AttractionName="艋舺龍山寺",
                PostalAddress={"City": "臺北市", "Town": "萬華區", "StreetAddress": "廣州街211號"})
LONGSHAN_MALL = dict(LONGSHAN, AttractionID="T3", AttractionName="龍山寺地下街")
CONFUCIUS = dict(PORT, AttractionID="T4", AttractionName="臺北孔廟",
                 PostalAddress={"City": "臺北市", "Town": "大同區", "StreetAddress": "大龍街275號"})


@pytest.mark.parametrize("asked, official", [
    ("士林夜市", "士林觀光夜市"),  # extra characters in the listing
    ("台北市孔廟", "臺北孔廟"),    # 台/臺, and an extra character in the name
])
def test_names_match_listings_with_different_wording(fake_tdx, asked, official):
    _, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [NIGHT_MARKET, CONFUCIUS]
    out = json.loads(attractions.find_attractions("Taipei", names=[asked]))
    assert [r["name"] for r in out["results"]] == [official]
    assert out["possible_matches"] == {asked: [official]} and out["not_found"] == []


def test_names_with_several_listings_return_all_for_the_model_to_pick(fake_tdx):
    _, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [LONGSHAN_MALL, LONGSHAN]
    out = json.loads(attractions.find_attractions("Taipei", names=["龍山寺"]))
    assert sorted(out["possible_matches"]["龍山寺"]) == ["艋舺龍山寺", "龍山寺地下街"]
    assert "the one the user means" in out["hint"]


def test_generic_names_say_how_many_listings_match(fake_tdx):
    _, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [dict(TEMPLE, AttractionID=f"M{i}", AttractionName=f"第{i}天后宮") for i in range(5)]
    out = json.loads(attractions.find_attractions("Tainan", names=["天后宮"]))
    assert len(out["possible_matches"]["天后宮"]) == attractions.MAX_NAME_MATCHES
    assert out["too_many_matches"] == {"天后宮": 5} and "more specific name" in out["hint"]


def test_names_past_ten_are_reported_not_dropped(fake_tdx):
    _, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [TEMPLE]
    names = [f"地方{i}" for i in range(12)]
    out = json.loads(attractions.find_attractions("Tainan", names=names))
    assert out["not_found"] == names[:10] and out["not_checked"] == names[10:]
    assert "call again with the names in not_checked" in out["hint"]


def test_names_of_closed_places_are_reported_as_closed(fake_tdx):
    _, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [dict(TEMPLE, AttractionName="太魯閣國家公園", ServiceStatus=3)]
    out = json.loads(attractions.find_attractions("Hualien", names=["太魯閣國家公園"]))
    assert out["results"] == [] and out["not_found"] == []
    assert out["closed"] == {"太魯閣國家公園": ["太魯閣國家公園 (temporarily closed)"]}
    assert "do not recommend them" in out["hint"]


def test_exact_name_wins_over_partial_matches(fake_tdx):
    _, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [LONGSHAN_MALL, LONGSHAN]
    out = json.loads(attractions.find_attractions("Taipei", names=["艋舺龍山寺"]))
    assert [r["name"] for r in out["results"]] == ["艋舺龍山寺"] and "possible_matches" not in out


def test_names_lookup_without_a_search_still_pins_the_picks(fake_tdx):
    _, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [TEMPLE]
    attractions.find_attractions("Tainan", names=["南鯤鯓代天府"])
    assert [r["name"] for r in attractions.pins_from_answer("Visit 南鯤鯓代天府.")] == ["南鯤鯓代天府"]


def test_names_lookup_skips_closed_places(fake_tdx):
    _, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [dict(TEMPLE, ServiceStatus=0)]
    out = json.loads(attractions.find_attractions("Tainan", names=["南鯤鯓代天府"]))
    assert out["results"] == [] and out["closed"] == {"南鯤鯓代天府": ["南鯤鯓代天府 (permanently closed)"]}


def test_palace_museum_is_not_a_temple(fake_tdx):
    _, data = fake_tdx
    palace = dict(PORT, AttractionID="P1", AttractionName="國立故宮博物院", Description="故宮收藏")
    shrine = dict(PORT, AttractionID="P2", AttractionName="行天宮", Description="關聖帝君")
    data[attractions.ATTRACTION_PATH] = [palace, shrine]
    out = json.loads(attractions.find_attractions("Taipei", keyword="temple"))
    assert [r["name"] for r in out["results"]] == ["行天宮"] and out["more_candidates"] == {}


def test_pins_only_places_named_in_the_answer(fake_tdx, monkeypatch):
    _, data = fake_tdx
    xiangshan = dict(PORT, AttractionID="X1", AttractionName="南港山系_象山親山步道", PositionLat=25.03)
    data[attractions.ATTRACTION_PATH] = [xiangshan, TEMPLE, SHRINE]
    out = json.loads(attractions.find_attractions("Tainan"))
    assert all("lat" not in r for r in out["results"])  # candidates are never pinned
    answer = "Go to Elephant Mountain (象山親山步道) and 南鯤鯓代天府."
    pins = attractions.pins_from_answer(answer)
    assert [r["name"] for r in pins] == ["南港山系_象山親山步道", "南鯤鯓代天府"]
    assert pins[0]["lat"] == 25.03
    assert attractions.pins_from_answer("No Chinese names here.") == []


def test_name_matches_come_before_description_mentions(fake_tdx):
    _, data = fake_tdx
    bike = dict(PORT, AttractionID="K1", AttractionName="八里左岸自行車道", Description="可遠眺關渡宮")
    temple = dict(PORT, AttractionID="K2", AttractionName="關渡宮", Description="媽祖廟",
                  PostalAddress={"City": "臺北市", "Town": "北投區", "StreetAddress": "知行路360號"})
    data[attractions.ATTRACTION_PATH] = [bike, temple]
    out = json.loads(attractions.find_attractions("Taipei", keyword="temple"))
    assert [r["name"] for r in out["results"]] == ["關渡宮", "八里左岸自行車道"]


def test_answer_pins_ignore_names_inside_longer_names(monkeypatch):
    area = dict(PORT, AttractionID="Q1", AttractionName="擎天崗", PositionLat=25.16)
    canal = dict(PORT, AttractionID="Q2", AttractionName="擎天崗系_坪頂古圳步道", PositionLat=25.13)
    attractions._seen().update({r["AttractionName"]: r for r in (area, canal)})
    pins = attractions.pins_from_answer("Walk the canal trail (擎天崗系_坪頂古圳步道).")
    assert [r["name"] for r in pins] == ["擎天崗系_坪頂古圳步道"]


def test_answer_pins_match_shortened_name_in_parentheses(monkeypatch):
    confucius = dict(PORT, AttractionID="C1", AttractionName="孔廟文化園區「臺南孔子廟」", PositionLat=22.99)
    attractions._seen().update({confucius["AttractionName"]: confucius})
    pins = attractions.pins_from_answer("Confucius Temple (臺南孔子廟) is a must.")
    assert pins[0]["lat"] == 22.99


def test_answer_pins_split_alternative_names(monkeypatch):
    street = dict(PORT, AttractionID="S1", AttractionName="安平老街(延平老街)", PositionLat=23.0)
    attractions._seen().update({street["AttractionName"]: street})
    pins = attractions.pins_from_answer("Anping Old Street (安平老街 / 延平老街) is Taiwan's oldest street.")
    assert [r["name"] for r in pins] == ["安平老街(延平老街)"]


def test_sessions_do_not_share_recent_searches():
    temple = dict(PORT, AttractionID="T9", AttractionName="赤崁樓", PositionLat=23.0)
    attractions.use_session("traveler-a")
    attractions._seen().update({temple["AttractionName"]: temple})
    assert [r["name"] for r in attractions.pins_from_answer("See 赤崁樓.")] == ["赤崁樓"]
    attractions.use_session("traveler-b")
    assert attractions.pins_from_answer("See 赤崁樓.") == []
    attractions.forget_session("traveler-a")
    attractions.use_session("traveler-a")
    assert attractions.pins_from_answer("See 赤崁樓.") == []


GARDEN = {"AttractionID": "wikidata:Q1", "AttractionName": "花園夜市", "PositionLat": 23.01, "PositionLon": 120.2,
          "AttractionClasses": [], "ServiceStatus": 1, "Description": "", "Source": "Wikidata",
          "PostalAddress": {"City": "臺南市", "Town": "北區"}}


def test_search_adds_wikidata_sights_the_register_lacks(fake_tdx, monkeypatch):
    _, data = fake_tdx
    data[attractions.ATTRACTION_PATH] = [dict(TEMPLE, AttractionName="大東夜市")]
    monkeypatch.setattr(attractions, "EXTRA", [GARDEN])
    monkeypatch.setattr(attractions, "FAME", {"wikidata:Q1": 0.9})
    out = json.loads(attractions.find_attractions("Tainan", keyword="夜市"))
    assert [r["name"] for r in out["results"]] == ["花園夜市", "大東夜市"]
    assert "Wikidata" in out["source"] and "Not in the official register" in out["results"][0]["note"]
    assert json.loads(attractions.find_attractions("Tainan", keyword="夜市", district="中西區"))["results"][0]["name"] == "大東夜市"


def test_name_lookup_falls_back_to_wikidata_sights(fake_tdx, monkeypatch):
    monkeypatch.setattr(attractions, "EXTRA", [GARDEN])
    out = json.loads(attractions.find_attractions("Tainan", names=["花園夜市"]))
    assert [r["name"] for r in out["results"]] == ["花園夜市"] and out["results"][0]["lat"] == 23.01
    assert out["source"].endswith("plus Wikidata")
    assert [r["name"] for r in attractions.pins_from_answer("Go to Garden Night Market (花園夜市).")] == ["花園夜市"]
