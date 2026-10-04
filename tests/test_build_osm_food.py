"""OSM element conversion in scripts/build_osm_food.py. No network."""

from scripts import build_osm_food as bo


def test_place_keeps_the_useful_tags_with_short_keys():
    way = {"type": "way", "id": 7, "center": {"lat": 25.0331234, "lon": 121.5654321},
           "tags": {"amenity": "restaurant", "name": "鼎泰豐", "name:en": "Din Tai Fung", "cuisine": "dumpling",
                    "opening_hours": "Mo-Su 10:00-21:00", "phone": "x"}}
    assert bo.place(way, "臺北市") == {"id": "osm:w7", "k": "restaurant", "lat": 25.033123, "lon": 121.565432,
                                      "city": "臺北市", "n": "鼎泰豐", "en": "Din Tai Fung", "c": "dumpling",
                                      "h": "Mo-Su 10:00-21:00"}


def test_place_skips_unnamed_and_closed():
    node = {"type": "node", "id": 1, "lat": 25.0, "lon": 121.5, "tags": {"amenity": "cafe"}}
    assert bo.place(node, "臺北市") is None
    assert bo.place(dict(node, tags={"amenity": "cafe", "name": "x", "disused": "yes"}), "臺北市") is None


def test_address_comes_from_addr_tags():
    assert bo.address({"addr:street": "中正路", "addr:housenumber": "16"}) == "中正路16號"
    assert bo.address({"addr:district": "中西區", "addr:street": "民權路二段", "addr:housenumber": "71號"}) == "中西區民權路二段71號"
    assert bo.address({"addr:full": "臺南市中西區開山路118號"}) == "臺南市中西區開山路118號"
    assert bo.address({"addr:housenumber": "5"}) is None


def test_place_retains_dietary_evidence_and_reported_district():
    element = {"type": "node", "id": 1, "lat": 25.0, "lon": 121.5,
               "tags": {"name": "小店", "amenity": "restaurant", "diet:vegetarian": "yes",
                        "diet:vegan": "limited", "addr:district": "萬華區"}}
    result = bo.place(element, "臺北市")
    assert (result["dv"], result["dg"], result["t"], result["ts"]) == ("yes", "limited", "萬華區", "reported")


def test_district_estimation_preserves_reported_districts():
    places = [{"lat": 25.0, "lon": 121.5, "city": "臺北市", "t": "萬華區", "ts": "reported"},
              {"lat": 25.0, "lon": 121.5, "city": "臺北市"}]
    official = [{"PositionLat": 25.0, "PositionLon": 121.5,
                 "PostalAddress": {"City": "臺北市", "Town": "中正區"}}]
    assert bo.assign_towns(places, official) == 2
    assert (places[0]["t"], places[0]["ts"]) == ("萬華區", "reported")
    assert (places[1]["t"], places[1]["ts"]) == ("中正區", "estimated")
