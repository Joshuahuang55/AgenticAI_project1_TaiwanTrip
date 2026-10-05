"""English names for Chinese-only listings."""

import pytest

from tools import lodging
from tools.english_labels import english_name


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
