"""The daily open-data files and the tools that read them. Network is mocked."""

import io
import json
import time
import zipfile

import pytest

from tools import attractions, food, tourism_data


def _place(i, name, city="臺南市", town="中西區", status=1, desc=""):
    return {"AttractionID": f"A{i:04d}", "AttractionName": name, "Description": desc, "ServiceStatus": status,
            "PostalAddress": {"City": city, "Town": town, "StreetAddress": "1號"}, "PositionLat": 23.0, "PositionLon": 120.2}


def _load(dataset, rows, **parts):
    tourism_data._data[dataset] = {"loaded_at": time.time(), "parts": {"rows": rows, **parts}}


@pytest.fixture(autouse=True)
def fresh_session(monkeypatch):
    monkeypatch.setattr(attractions, "_seen_by_session", {})
    attractions.use_session("test")


@pytest.fixture
def tdx_calls(monkeypatch):
    calls = []

    def fake(path, params):
        calls.append((path, params))
        return []
    monkeypatch.setattr(attractions, "tdx_get", fake)
    monkeypatch.setattr(food, "tdx_get", fake)
    return calls


def test_listings_filter_like_the_tdx_queries():
    _load("attractions", [_place(1, "赤崁樓"), _place(2, "安平古堡", town="安平區", desc="荷蘭古堡"),
                          _place(3, "臺北孔廟", city="臺北市")])
    assert [r["AttractionName"] for r in tourism_data.listings("attractions", "臺南市")] == ["赤崁樓", "安平古堡"]
    assert [r["AttractionName"] for r in tourism_data.listings("attractions", "臺南市", town="安平區")] == ["安平古堡"]
    assert [r["AttractionName"] for r in tourism_data.listings("attractions", "臺南市", words=("古堡",))] == ["安平古堡"]
    assert tourism_data.listings("attractions", "臺南市", words=("荷蘭",), name_only=True) == []


def test_listings_is_none_until_loaded_and_starts_one_download(monkeypatch):
    started = []
    monkeypatch.setattr(tourism_data, "_start_refresh", started.append)
    assert tourism_data.listings("attractions", "臺南市") is None
    assert tourism_data.listings("attractions", "臺南市") is None
    assert started == ["attractions"]  # the second call waits for the first download


def test_warm_starts_every_download(monkeypatch):
    started = []
    monkeypatch.setattr(tourism_data, "_start_refresh", started.append)
    tourism_data.warm()
    assert started == list(tourism_data.DATASETS)


def test_a_day_old_copy_is_still_served_while_it_refreshes(monkeypatch):
    started = []
    monkeypatch.setattr(tourism_data, "_start_refresh", started.append)
    _load("attractions", [_place(1, "赤崁樓")])
    tourism_data._data["attractions"]["loaded_at"] -= tourism_data.REFRESH_SECONDS + 1
    assert len(tourism_data.listings("attractions", "臺南市")) == 1 and started == ["attractions"]


def _attraction_zip(*places) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        rows = {"UpdateTime": "today", "Attractions": list(places)}
        z.writestr("AttractionList.json", "\ufeff" + json.dumps(rows, ensure_ascii=False))  # the files carry a BOM
        z.writestr("AttractionServiceTimeList.json", json.dumps({"AttractionServiceTimes": []}))
        z.writestr("AttractionFeeList.json", json.dumps({"AttractionFees": []}))
    return buf.getvalue()


def _serve(monkeypatch, content: bytes, calls: list | None = None):
    class Resp:
        def raise_for_status(self):
            pass
    Resp.content = content
    def get(url, timeout):
        if calls is not None:
            calls.append(url)
        return Resp()
    monkeypatch.setattr(tourism_data._http, "get", get)


def test_download_reads_the_zip_and_sorts_by_id(monkeypatch):
    _serve(monkeypatch, _attraction_zip(_place(2, "安平古堡"), _place(1, "赤崁樓")))
    assert tourism_data.refresh("attractions")
    assert [r["AttractionName"] for r in tourism_data._data["attractions"]["parts"]["rows"]] == ["赤崁樓", "安平古堡"]


def test_a_failed_download_keeps_the_previous_copy(monkeypatch):
    _load("attractions", [_place(1, "赤崁樓")])

    def down(url, timeout):
        raise tourism_data.requests.ConnectionError("offline")
    monkeypatch.setattr(tourism_data._http, "get", down)
    assert not tourism_data.refresh("attractions")
    assert len(tourism_data.listings("attractions", "臺南市")) == 1


def test_search_uses_the_daily_file_without_tdx_or_the_500_cap(tdx_calls):
    _load("attractions", [_place(i, f"景點{i:04d}") for i in range(attractions.MAX_ROWS + 45)] + [_place(999, "赤崁樓")],
          hours=[], fees=[])
    out = json.loads(attractions.find_attractions("Tainan", names=["赤崁樓"]))
    assert [r["name"] for r in out["results"]] == ["赤崁樓"] and out["source"] == tourism_data.SOURCE
    out = json.loads(attractions.find_attractions("Tainan"))
    assert sum(len(v) for v in out["more_candidates"].values()) + len(out["results"]) == attractions.MAX_ROWS + 46
    assert tdx_calls == []


def test_name_lookup_reports_closed_places_from_the_daily_file(tdx_calls):
    _load("attractions", [_place(1, "太魯閣國家公園", city="花蓮縣", status=3)], hours=[], fees=[])
    out = json.loads(attractions.find_attractions("Hualien", names=["太魯閣國家公園"]))
    assert out["closed"] == {"太魯閣國家公園": ["太魯閣國家公園 (temporarily closed)"]} and tdx_calls == []


def test_hours_come_from_the_daily_file(tdx_calls):
    hours = [{"AttractionID": "A0001", "ServiceTimes": [{"ServiceDays": ["Monday"], "StartTime": "09:00:00",
                                                          "EndTime": "17:00:00"}]}]
    _load("attractions", [_place(1, "赤崁樓")], hours=hours, fees=[])
    out = json.loads(attractions.find_attractions("Tainan", names=["赤崁樓"]))
    assert out["results"][0]["open_time"] == "Mon 09:00-17:00" and tdx_calls == []


def test_food_reads_restaurants_and_night_markets_from_the_daily_files(tdx_calls):
    _load("restaurants", [{"RestaurantID": "R0", "RestaurantName": "已歇業粥店", "Description": "虱目魚粥",
                           "ServiceStatus": 0, "PostalAddress": {"City": "臺南市", "Town": "中西區"}},
                          {"RestaurantID": "R1", "RestaurantName": "阿堂鹹粥", "Description": "虱目魚粥",
                           "PostalAddress": {"City": "臺南市", "Town": "中西區"}}])
    _load("attractions", [_place(1, "花園夜市"), _place(2, "已收攤夜市", status=0)], hours=[], fees=[])
    out = json.loads(food.find_local_food("Tainan", keyword="虱目魚"))
    assert [r["name"] for r in out["results"]] == ["阿堂鹹粥"] and out["source"] == tourism_data.SOURCE
    out = json.loads(food.find_local_food("Tainan", keyword="night market"))
    assert [r["name"] for r in out["results"]] == ["花園夜市"]  # closed markets are left out
    assert tdx_calls == []


def _osm(i, name, lat=25.05, lon=121.52, en=None, cuisine=""):
    return food._osm_row({"id": f"osm:n{i}", "n": name, "lat": lat, "lon": lon, "city": "臺北市", "en": en, "c": cuisine})


def test_osm_fills_cities_the_register_lacks(tdx_calls, monkeypatch):
    _load("restaurants", [])
    monkeypatch.setattr(food, "OSM_BY_COUNTY", {"臺北市": [
        _osm(1, "林東芳牛肉麵"), _osm(2, "某咖啡", cuisine="coffee_shop"), _osm(3, "鼎泰豐", en="Din Tai Fung")]})
    out = json.loads(food.find_local_food("Taipei", keyword="beef noodles"))
    assert [r["name"] for r in out["results"]] == ["林東芳牛肉麵"]
    assert "OpenStreetMap" in out["source"] and "not the official register" in out["results"][0]["note"]
    assert [r["name"] for r in json.loads(food.find_local_food("Taipei", keyword="coffee"))["results"]] == ["某咖啡"]
    assert json.loads(food.find_local_food("Taipei", keyword="din tai fung"))["results"][0]["name_en"] == "Din Tai Fung"
    assert json.loads(food.find_local_food("Taipei", keyword="beef noodles", district="大安區"))["results"] == []


def test_an_osm_twin_of_an_official_restaurant_is_merged(tdx_calls, monkeypatch):
    official = {"RestaurantID": "R1", "RestaurantName": "文章牛肉湯", "Description": "溫體牛", "ServiceStatus": 1,
                "PositionLat": 22.99, "PositionLon": 120.19, "PostalAddress": {"City": "臺南市", "Town": "安平區"}}
    _load("restaurants", [official])
    twin = dict(_osm(1, "文章牛肉湯安平總店", lat=22.9905, lon=120.1901, en="Wen Zhang Beef Soup"), PostalAddress={"City": "臺南市"})
    far = dict(_osm(2, "文章牛肉湯", lat=23.05, lon=120.25), PostalAddress={"City": "臺南市"})  # another branch, 9 km away
    monkeypatch.setattr(food, "OSM_BY_COUNTY", {"臺南市": [twin, far]})
    out = json.loads(food.find_local_food("Tainan", keyword="牛肉"))
    assert [r["name"] for r in out["results"]] == ["文章牛肉湯"]  # the far branch has the same name: one is shown
    assert out["results"][0]["name_en"] == "Wen Zhang Beef Soup" and "note" not in out["results"][0]


def test_food_ranks_by_awards_and_adds_local_gems(tdx_calls, monkeypatch):
    _load("restaurants", [])
    places = [_osm(i, f"店{i}") for i in range(1, 8)]
    monkeypatch.setattr(food, "OSM_BY_COUNTY", {"臺北市": places})
    monkeypatch.setattr(food, "FOOD_FAME", {
        "osm:n3": {"fame": 0.9, "local": 0.0, "awards": ["Michelin Guide Taiwan: 1 Star"], "name_en": "Shop Three"},
        "osm:n5": {"fame": 0.8, "local": 0.8, "awards": ["500碗 2024-2026"]},
        "osm:n6": {"fame": 0.6, "local": 0.6, "awards": ["500盤 2025"]},
        "osm:n7": {"fame": 0.2, "local": 0.0}})
    out = json.loads(food.find_local_food("Taipei", limit=3))
    assert [r["name"] for r in out["results"]] == ["店3", "店5", "店6"]  # too few slots for gems
    assert out["results"][0]["awards"] == ["Michelin Guide Taiwan: 1 Star"] and out["results"][0]["name_en"] == "Shop Three"
    assert "research and education use only" in out["source"]
    out = json.loads(food.find_local_food("Taipei", limit=5))
    assert [r["name"] for r in out["results"]] == ["店3", "店5", "店6", "店7", "店1"]  # gems 店5, 店6 already picked
    out = json.loads(food.find_local_food("Taipei", limit=5, style="local"))
    assert [r["name"] for r in out["results"]][:2] == ["店5", "店6"]


def test_food_must_see_reserves_slots_for_local_gems(tdx_calls, monkeypatch):
    _load("restaurants", [])
    monkeypatch.setattr(food, "OSM_BY_COUNTY", {"臺北市": [_osm(i, f"店{i}") for i in range(1, 8)]})
    monkeypatch.setattr(food, "FOOD_FAME", {f"osm:n{i}": {"fame": 1 - i / 10, "local": 0.0} for i in range(1, 6)}
                        | {"osm:n7": {"fame": 0.1, "local": 0.7}})
    out = json.loads(food.find_local_food("Taipei", limit=5))
    assert [r["name"] for r in out["results"]] == ["店1", "店2", "店3", "店4", "店7"]
    assert out["results"][-1]["local_gem"] is True


def test_food_xiaolongbao_searches_the_dish_not_every_dumpling_shop(tdx_calls, monkeypatch):
    _load("restaurants", [])
    monkeypatch.setattr(food, "OSM_BY_COUNTY", {"臺北市": [
        _osm(1, "水餃店", cuisine="dumpling"), _osm(2, "明月湯包小籠包", cuisine="dumpling")]})
    out = json.loads(food.find_local_food("Taipei", keyword="xiaolongbao"))
    assert [r["name"] for r in out["results"]] == ["明月湯包小籠包"]


def test_food_unknown_style_is_an_error():
    assert "hint" in json.loads(food.find_local_food("Taipei", style="cheap"))


def test_food_holds_few_fine_dining_places_unless_asked_and_drops_closed(tdx_calls, monkeypatch):
    _load("restaurants", [])
    monkeypatch.setattr(food, "OSM_BY_COUNTY", {"臺北市": [_osm(i, f"店{i}") for i in range(1, 8)]})
    star = {"fame": 0.9, "local": 0.0, "fine_dining": True, "price": "$$$$"}
    monkeypatch.setattr(food, "FOOD_FAME", {"osm:n1": star, "osm:n2": star, "osm:n3": star,
                                            "osm:n4": {"fame": 0.7, "local": 0.0}, "osm:n5": {"fame": 0.6, "local": 0.0},
                                            "osm:n6": {"fame": 1.0, "local": 0.0, "closed": True}})
    out = json.loads(food.find_local_food("Taipei", limit=3))
    assert [r["name"] for r in out["results"]] == ["店1", "店4", "店5"] and out["results"][0]["price"] == "$$$$"
    out = json.loads(food.find_local_food("Taipei", keyword="fine dining", limit=3))
    assert "店6" not in [r["name"] for r in out["results"]]


def test_food_more_candidates_are_the_next_award_winners(tdx_calls, monkeypatch):
    _load("restaurants", [])
    monkeypatch.setattr(food, "OSM_BY_COUNTY", {"臺北市": [_osm(i, f"店{i:02d}") for i in range(1, 50)]})
    monkeypatch.setattr(food, "FOOD_FAME", {f"osm:n{i}": {"fame": 1 - i / 100, "local": 0.0, "awards": ["500碗 2026"]}
                                            for i in range(1, 46)})
    out = json.loads(food.find_local_food("Taipei"))
    assert len(out["more_candidates"]) == food.MORE_CANDIDATES
    assert out["more_candidates"][0] == "店11 (500碗 2026)" and all("(" in c for c in out["more_candidates"])


def test_food_names_look_up_places_loosely_and_report_missing_ones(tdx_calls, monkeypatch):
    _load("restaurants", [])
    monkeypatch.setattr(food, "OSM_BY_COUNTY", {"臺北市": [_osm(1, "阿宗麵線 西門店"), _osm(2, "某咖啡")]})
    monkeypatch.setattr(food, "FOOD_FAME", {"osm:n1": {"fame": 0.5, "local": 0.0, "awards": ["500碗 2024"]}})
    out = json.loads(food.find_local_food("Taipei", names=["阿宗麵線", "明耀小籠包"]))
    assert [r["name"] for r in out["results"]] == ["阿宗麵線 西門店"] and "lat" not in out["results"][0]
    assert out["results"][0]["map_url"].endswith("query=25.05,121.52")
    assert out["not_found"] == ["明耀小籠包"] and tdx_calls == []


def test_food_osm_places_carry_address_district_and_a_map_link(tdx_calls, monkeypatch):
    _load("restaurants", [])
    row = lambda i, name, **extra: food._osm_row({"id": f"osm:n{i}", "n": name, "lat": 25.03, "lon": 121.54,
                                                  "city": "臺北市", **extra})
    monkeypatch.setattr(food, "OSM_BY_COUNTY", {"臺北市": [
        row(1, "永康牛肉麵", t="大安區", a="金山南路二段31巷17號"), row(2, "林東芳牛肉麵", t="中山區"),
        row(3, "某牛肉麵", t="大安區", a="臺北市大安區某路1號")]})
    out = json.loads(food.find_local_food("Taipei", keyword="beef noodles", district="大安區"))
    by_name = {r["name"]: r for r in out["results"]}
    assert set(by_name) == {"永康牛肉麵", "某牛肉麵"}  # district searches include OSM places now
    assert by_name["永康牛肉麵"]["address"] == "臺北市大安區金山南路二段31巷17號"
    assert by_name["某牛肉麵"]["address"] == "臺北市大安區某路1號"  # a full address is not doubled
    assert by_name["永康牛肉麵"]["map_url"] == "https://www.google.com/maps/search/?api=1&query=25.03,121.54"


def test_food_address_drops_postal_codes_and_repeated_counties():
    a = lambda street, town=None: food._address({"City": "南投縣", "Town": town, "StreetAddress": street})
    assert a("540南投縣南投市光華路98號", "南投市") == "南投縣南投市光華路98號"
    assert a("埔里鎮中山路四段", "埔里鎮") == "南投縣埔里鎮中山路四段"
    assert food._address({"City": "臺北市", "Town": "大安區", "StreetAddress": "台北市大安區某路1號"}) == "臺北市大安區某路1號"
    assert a("No. 5, Some Road, Nantou") == "No. 5, Some Road, Nantou"


def test_a_saved_file_spares_the_download_after_a_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(tourism_data, "CACHE_DIR", tmp_path)
    _serve(monkeypatch, _attraction_zip(_place(1, "赤崁樓")))
    assert tourism_data.refresh("attractions") and (tmp_path / "Attraction-json.zip").exists()
    tourism_data._data.clear()  # a restart: memory is empty, the file is not
    started = []
    monkeypatch.setattr(tourism_data, "_start_refresh", started.append)
    assert [r["AttractionName"] for r in tourism_data.listings("attractions", "臺南市")] == ["赤崁樓"]
    assert started == []  # less than a day old: no new download


def test_a_day_old_saved_file_is_used_while_a_new_one_downloads(monkeypatch, tmp_path):
    import os
    monkeypatch.setattr(tourism_data, "CACHE_DIR", tmp_path)
    (tmp_path / "Attraction-json.zip").write_bytes(_attraction_zip(_place(1, "赤崁樓")))
    old = time.time() - tourism_data.REFRESH_SECONDS - 60
    os.utime(tmp_path / "Attraction-json.zip", (old, old))
    started = []
    monkeypatch.setattr(tourism_data, "_start_refresh", started.append)
    assert len(tourism_data.listings("attractions", "臺南市")) == 1 and started == ["attractions"]


def test_a_broken_saved_file_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(tourism_data, "CACHE_DIR", tmp_path)
    (tmp_path / "Attraction-json.zip").write_bytes(b"not a zip")
    assert tourism_data.listings("attractions", "臺南市") is None


def test_food_pins_only_the_places_the_answer_names(tdx_calls, monkeypatch):
    _load("restaurants", [])
    monkeypatch.setattr(food, "OSM_BY_COUNTY", {"臺北市": [_osm(1, "鼎泰豐"), _osm(2, "鼎泰豐信義店", lat=25.03),
                                                         _osm(3, "林東芳牛肉麵"), _osm(4, "某麵店")]})
    out = json.loads(food.find_local_food("Taipei"))
    assert all("lat" not in r for r in out["results"])  # candidates are not pinned
    pins = food.pins_from_answer("Try 林東芳牛肉麵 first, then 鼎泰豐信義店 (鼎泰豐信義店).")
    assert [(p["name"], p["lat"]) for p in pins] == [("林東芳牛肉麵", 25.05), ("鼎泰豐信義店", 25.03)]
