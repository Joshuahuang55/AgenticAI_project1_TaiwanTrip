"""Matching and scoring in scripts/build_fame.py. No network."""

import pytest

from scripts import build_fame as bf


def _row(i, name, lat=23.0, lon=120.2, classes=(4,), city="臺南市"):
    return {"AttractionID": f"A{i}", "AttractionName": name, "PositionLat": lat, "PositionLon": lon,
            "AttractionClasses": list(classes), "PostalAddress": {"City": city}}


def _item(qid, label, lat=23.0, lon=120.2):
    return {"qid": qid, "labels": {label}, "lat": lat, "lon": lon, "sitelinks": 3, "zhwiki": label}


def test_name_forms_cover_series_and_bracketed_names():
    assert "象山親山步道" in bf.name_forms("南港山系_象山親山步道")
    assert "臺南孔子廟" in bf.name_forms("孔廟文化園區「臺南孔子廟」")


@pytest.mark.parametrize("listing, label, matches", [
    ("臺灣祀典武廟", "祀典武廟", True),
    ("祀典大天后宮(明寧靖王府邸)", "大天后宮", True),
    ("安平古堡", "赤崁樓", False),
    ("多良火車站", "多良車站", True),   # in order, as find_attractions matches names
    ("赤嵌樓", "赤崁樓", True),         # variant characters
])
def test_names_must_be_similar(listing, label, matches):
    assert (bf.best_item(_row(1, listing), [_item("Q1", label)]) is not None) == matches


def test_items_must_be_nearby_unless_the_place_is_large():
    far = _item("Q1", "象山步道", lat=23.01)  # about 1.1 km north
    assert bf.best_item(_row(1, "象山步道"), [far]) is None
    assert bf.best_item(_row(1, "象山步道", classes=(13,)), [far])["qid"] == "Q1"  # a trail: 3 km allowed


def test_villages_and_townships_never_match():
    assert bf.best_item(_row(1, "案山里˙平安寶塔"), [_item("Q1", "案山里")]) is None
    assert bf.best_item(_row(1, "大埔鄉北極殿"), [_item("Q1", "大埔鄉")]) is None
    assert bf.best_item(_row(1, "三仙台"), [_item("Q1", "三仙台風景區")])["qid"] == "Q1"  # scenic areas stay


def test_a_trail_matched_to_its_mountain_is_flagged():
    row = _row(1, "鳶山登山步道", classes=(13,))
    assert bf.via_mountain(row, bf.best_item(row, [_item("Q1", "鳶山")]))
    assert bf.best_item(_row(3, "南港山系_象山親山步道", classes=(13,)), [_item("Q3", "象山")])["qid"] == "Q3"
    assert bf.best_item(_row(4, "象山金剛寺", classes=(4,)), [_item("Q3", "象山")]) is None  # not a trail
    trail = _row(2, "聖母登山步道", classes=(13,))
    assert not bf.via_mountain(trail, bf.best_item(trail, [_item("Q2", "聖母登山步道")]))


def test_trails_via_their_mountain_score_by_english_views_only():
    rows = {"臺北市": [_row(1, "象山親山步道", city="臺北市"), _row(2, "天上山步道", city="臺北市"),
                      _row(3, "台北101", city="臺北市"), _row(4, "小公園", city="臺北市")]}
    signals = {"A1": {"via_mountain": True, "en_views": 11000},
               "A2": {"via_mountain": True, "en_views": 400},  # a few en views: no score
               "A3": {"views": 90000, "sitelinks": 4.0, "length": 50000}}
    scores = bf.fame_scores(rows, signals)
    assert scores["A1"] == 0.94 and "A2" not in scores and scores["A3"] == 0.875


def test_a_trail_ignores_its_mountain_range_and_non_mountain_names():
    xiangshan = bf.best_item(_row(1, "南港山系_象山親山步道", classes=(13,)), [_item("Q1", "南港山"), _item("Q2", "象山")])
    assert xiangshan["qid"] == "Q2"  # the trail's mountain, not the range's
    assert bf.best_item(_row(2, "七星山系_天母古道親山步道", classes=(13,)), [_item("Q3", "天母")]) is None  # a neighborhood


def test_the_most_similar_item_wins():
    items = [_item("Q1", "大稻埕"), _item("Q2", "大稻埕戲苑")]
    assert bf.best_item(_row(1, "大稻埕戲苑"), items)["qid"] == "Q2"


def test_percentiles_count_ties_half():
    assert bf.percentiles([0, 0, 10, 20]) == [0.25, 0.25, 0.625, 0.875]


def test_fame_is_relative_to_the_county():
    rows = {"臺北市": [_row(1, "台北101", city="臺北市"), _row(2, "小公園", city="臺北市")],
            "臺東縣": [_row(3, "三仙台", city="臺東縣"), _row(4, "小步道", city="臺東縣")]}
    signals = {"A1": {"views": 90000, "sitelinks": 4.0, "length": 50000},
               "A3": {"views": 900, "sitelinks": 1.5, "length": 3000}}  # far fewer views, top of its county
    scores = bf.fame_scores(rows, signals)
    assert scores == {"A1": 0.75, "A3": 0.75} and "A2" not in scores


def test_stations_and_campuses_get_half_fame():
    rows = {"臺北市": [_row(1, "台北101", city="臺北市"), _row(2, "板橋車站", city="臺北市"), _row(3, "小公園", city="臺北市")]}
    same = {"views": 90000, "sitelinks": 4.0, "length": 50000}
    scores = bf.fame_scores(rows, {"A1": same, "A2": same})
    assert abs(scores["A2"] - scores["A1"] / 2) <= 0.001


def test_english_fame_is_a_log_scale_between_min_and_full():
    assert bf.en_fame(0) == 0 and bf.en_fame(bf.EN_FAME_MIN) == 0
    assert bf.en_fame(bf.EN_FAME_FULL) == 1 and bf.en_fame(10 * bf.EN_FAME_FULL) == 1
    assert 0.4 < bf.en_fame(2000) < 0.6


def test_local_fame_is_high_for_places_known_only_at_home():
    fame = {"world": 0.95, "home": 0.9, "unknown": 0.1}
    en = {"world": 0.9, "unknown": 0.0}
    local = bf.local_scores(fame, en)
    assert local["home"] == 0.9 and local["world"] < 0.1 and local["unknown"] == 0.1
    assert max(local, key=local.get) == "home"


def _wd(qid, title, lat=23.0, lon=120.2, closed=False):
    return {"qid": qid, "labels": {title}, "lat": lat, "lon": lon, "sitelinks": 2, "zhwiki": title, "enwiki": None,
            "closed": closed}


@pytest.mark.parametrize("title, keep", [
    ("花園夜市", True), ("漁光島", True), ("琵琶湖 (台灣)", True), ("聖母登山步道", True),
    ("駁二藝術特區", True), ("龍虎塔", True), ("鵝鑾鼻", True), ("臺北市立動物園", True),
    ("七星山", False),         # a bare peak
    ("臺南車站", False), ("成功大學", False), ("案山里", False), ("中正路", False),
])
def test_only_sights_become_extras(title, keep):
    assert bf.is_extra_sight(_wd("Q1", title)) == keep


def test_an_extra_matching_a_closed_listing_is_left_out():
    open_listing = dict(_row(1, "太魯閣遊客中心", lat=24.158, lon=121.62, city="花蓮縣"), PostalAddress={"City": "花蓮縣", "Town": "秀林鄉"})
    closed = dict(_row(2, "砂卡礑步道", lat=24.162, lon=121.615, city="花蓮縣"), ServiceStatus=3)
    extras = bf.extra_rows([_wd("Q1", "砂卡礑步道", lat=24.1625, lon=121.6152), _wd("Q2", "七星潭", lat=24.10, lon=121.63)],
                           [open_listing], [closed])
    assert [r["AttractionName"] for r in extras] == ["七星潭"]


def test_extras_take_the_nearest_listings_county_and_skip_duplicates():
    listing = dict(_row(1, "士林觀光夜市", lat=25.088, lon=121.524, city="臺北市"), PostalAddress={"City": "臺北市", "Town": "士林區"})
    far = dict(_row(2, "陽明山", lat=25.16, lon=121.55, city="臺北市"), PostalAddress={"City": "臺北市", "Town": "北投區"})
    extras = bf.extra_rows([_wd("Q1", "大龍峒保安宮", lat=25.073, lon=121.515), _wd("Q2", "士林夜市", lat=25.088, lon=121.525),
                            _wd("Q3", "某夜市", lat=26.5, lon=122.5), _wd("Q4", "停業夜市", closed=True)], [listing, far])
    assert [(r["AttractionName"], r["PostalAddress"]) for r in extras] == [("大龍峒保安宮", {"City": "臺北市", "Town": "士林區"})]
    assert extras[0]["AttractionID"] == "wikidata:Q1" and extras[0]["Source"] == "Wikidata"
