"""English names for Chinese-only listings."""

import pytest

from tools import lodging
from tools.english_labels import english_district, english_name


@pytest.mark.parametrize("name, english", [
    ("你來花蓮民宿", "Nilai Hualien B&B"),
    ("阿村牛肉湯（保安路）", "Acun Beef Soup (Baoan Rd.)"),
    ("台北凱撒大飯店", "Taipei Kaisa Hotel"),
    ("好樣VVG", "Haoyang VVG"),
    ("三和街33號民宿", "Sanhe St. No. 33 B&B"),
    ("Hotel Proverbs Taipei", "Hotel Proverbs Taipei"),
    ("", ""),
    (None, None),
])
def test_listing_names_become_latin(name, english):
    assert english_name(name) == english


def test_stays_without_an_english_name_get_a_romanized_one():
    stay = lodging._summarize({"HotelName": "你來花蓮民宿", "HotelNameEn": None})
    assert stay["name"] == "你來花蓮民宿" and stay["name_en"] == "Nilai Hualien B&B"
    assert lodging._summarize({"HotelName": "晶華酒店", "HotelNameEn": "Regent Taipei"})["name_en"] == "Regent Taipei"


@pytest.mark.parametrize("district, address, english", [
    ("北區", None, "North District"),
    ("中西區", None, "West Central District"),
    ("安平區", None, "Anping District"),
    ("礁溪鄉", None, "Jiaoxi Township"),
    (None, "臺南市北區公園南路98號", "North District"),
    (None, "41 Baoan Road, West Central", None),
])
def test_districts_become_english(district, address, english):
    assert english_district(district, address) == english


def test_run_tool_gives_every_listed_place_an_english_name_and_district(monkeypatch):
    import json
    import tools
    monkeypatch.setitem(tools.TOOL_MAP, "find_local_food", lambda **_: json.dumps({"results": [
        {"name": "西羅殿牛肉湯", "address": "臺南市北區公園南路98號"},
        {"name": "上好之牛肉湯", "name_en": "Shang Hao Chih Beef Soup", "facts": {"district": {"value": "北區"}}},
    ]}, ensure_ascii=False))
    results = json.loads(tools.run_tool("find_local_food", {"city": "Tainan"}))["results"]
    assert [(r["name_en"], r["district_en"]) for r in results] == [
        ("Xiluodian Beef Soup", "North District"), ("Shang Hao Chih Beef Soup", "North District")]


def test_run_tool_passes_non_json_results_through(monkeypatch):
    import tools
    monkeypatch.setitem(tools.TOOL_MAP, "twd_exchange", lambda **_: "plain text")
    assert tools.run_tool("twd_exchange", {}) == "plain text"
