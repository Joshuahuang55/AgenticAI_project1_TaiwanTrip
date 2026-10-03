"""Candidate choice, label parsing and the review step in scripts/label_local_favorites.py. No network."""

import json

import pytest

from scripts import label_local_favorites as ll


def _row(aid, name, city):
    return {"AttractionID": aid, "AttractionName": name, "PostalAddress": {"City": city}}


def test_candidates_are_the_top_local_fame_per_county():
    rows = [_row("A", "漁光島", "臺南市"), _row("B", "小廟", "臺南市"), _row("C", "無名", "臺南市"),
            _row("D", "某湖", "宜蘭縣")]
    local = {"A": 0.9, "B": 0.4, "D": 0.7}
    picked = ll.candidates(local, rows, per_county=1)
    assert [(c["county"], c["name"]) for c in picked] == [("宜蘭縣", "某湖"), ("臺南市", "漁光島")]


def test_parse_labels_keeps_known_labels_and_defaults_to_minor():
    text = 'Sure: {"漁光島": "local_favorite", "小廟": "great"}'
    assert ll.parse_labels(text, ["漁光島", "小廟", "沒提到"]) == {
        "漁光島": "local_favorite", "小廟": "minor", "沒提到": "minor"}
    assert ll.parse_labels("no json", ["x"]) == {"x": "minor"}


def test_apply_keeps_only_reviewed_local_favorites(tmp_path):
    review = tmp_path / "review.csv"
    review.write_text("id,county,name,local,label\nA,臺南市,漁光島,0.9,local_favorite\nB,臺南市,小廟,0.4,minor\n",
                      encoding="utf-8-sig")
    out = tmp_path / "local.json"
    assert ll.apply(review, out) == {"A": "local_favorite"}
    assert json.loads(out.read_text(encoding="utf-8"))["labels"] == {"A": "local_favorite"}


def test_apply_rejects_typos_in_the_review(tmp_path):
    review = tmp_path / "review.csv"
    review.write_text("id,county,name,local,label\nA,臺南市,漁光島,0.9,local favorite\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        ll.apply(review, tmp_path / "local.json")
