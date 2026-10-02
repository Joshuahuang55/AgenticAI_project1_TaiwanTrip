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


def test_download_reads_the_zip_and_sorts_by_id(monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        rows = {"UpdateTime": "today", "Attractions": [_place(2, "安平古堡"), _place(1, "赤崁樓")]}
        z.writestr("AttractionList.json", "﻿" + json.dumps(rows, ensure_ascii=False))  # the files carry a BOM
        z.writestr("AttractionServiceTimeList.json", json.dumps({"AttractionServiceTimes": []}))
        z.writestr("AttractionFeeList.json", json.dumps({"AttractionFees": []}))

    class Resp:
        content = buf.getvalue()

        def raise_for_status(self):
            pass
    monkeypatch.setattr(tourism_data._http, "get", lambda url, timeout: Resp())
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
    _load("attractions", [_place(i, f"景點{i}") for i in range(attractions.MAX_ROWS + 45)] + [_place(999, "赤崁樓")],
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
    _load("restaurants", [{"RestaurantID": "R1", "RestaurantName": "阿堂鹹粥", "Description": "虱目魚粥",
                           "PostalAddress": {"City": "臺南市", "Town": "中西區"}}])
    _load("attractions", [_place(1, "花園夜市"), _place(2, "已收攤夜市", status=0)], hours=[], fees=[])
    out = json.loads(food.find_local_food("Tainan", keyword="虱目魚"))
    assert [r["name"] for r in out["results"]] == ["阿堂鹹粥"] and out["source"] == tourism_data.SOURCE
    out = json.loads(food.find_local_food("Tainan", keyword="night market"))
    assert [r["name"] for r in out["results"]] == ["花園夜市"]  # closed markets are left out
    assert tdx_calls == []
