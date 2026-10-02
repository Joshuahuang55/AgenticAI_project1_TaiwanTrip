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


def test_spread_takes_one_per_series_or_district_first(fake_tdx):
    _, data = fake_tdx
    trail = lambda i, name, town: dict(PORT, AttractionID=f"T{i}", AttractionName=name,
                                       PostalAddress={"City": "臺北市", "Town": town, "StreetAddress": str(i)})
    data[attractions.ATTRACTION_PATH] = [
        trail(1, "大屯山系_忠義山親山步道", "北投區"), trail(2, "大屯山系_中正山步道", "北投區"),
        trail(3, "南港山系_象山親山步道", "信義區"), trail(4, "碧湖步道", "內湖區"), trail(5, "大湖公園步道", "內湖區"),
    ]
    out = json.loads(attractions.find_attractions("Taipei", keyword="trail", limit=4))
    assert [r["name"] for r in out["results"]] == [
        "大屯山系_忠義山親山步道", "南港山系_象山親山步道", "碧湖步道", "大屯山系_中正山步道"]
    assert out["more_candidates"] == {"內湖區": ["大湖公園步道"]}


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
    assert "contains(AttractionName,'南鯤鯓代天府')" in calls[0][1]["$filter"]


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
