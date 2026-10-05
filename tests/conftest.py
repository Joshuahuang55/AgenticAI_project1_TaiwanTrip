"""Shared fixtures."""

import pytest

from tools import attractions, call_budget, food, tdx_client, tourism_data


@pytest.fixture(autouse=True)
def no_official_files(monkeypatch):
    """Tools fall back to (mocked) TDX: no test downloads the daily open-data files.
    A test that needs them sets tourism_data._data itself."""
    monkeypatch.setattr(tourism_data, "_data", {})
    monkeypatch.setattr(tourism_data, "_failed_at", {})
    monkeypatch.setattr(tourism_data, "_loading", set())
    monkeypatch.setattr(tourism_data, "_start_refresh", lambda dataset: None)
    monkeypatch.setattr(tourism_data, "CACHE_DIR", None)


@pytest.fixture(autouse=True)
def no_extra_sights(monkeypatch):
    """Tests see only the listings they set up, not the bundled Wikidata extras or real fame scores."""
    monkeypatch.setattr(attractions, "EXTRA", [])
    monkeypatch.setattr(attractions, "FAME", {})
    monkeypatch.setattr(attractions, "LOCAL_FAME", {})
    monkeypatch.setattr(attractions, "LOCAL_FAVORITES", {})
    monkeypatch.setattr(food, "OSM_BY_COUNTY", {})
    monkeypatch.setattr(food, "FOOD_FAME", {})


@pytest.fixture(autouse=True)
def no_live_tdx(monkeypatch):
    """An unmocked tdx_get answers 'credentials not configured' instead of calling TDX."""
    monkeypatch.setattr(tdx_client, "_get_token", lambda: None)


@pytest.fixture(autouse=True)
def roomy_tool_budget():
    """Tests run many tool calls a minute; tests of the budget itself lower the limit."""
    limit = call_budget.LIMIT
    call_budget.reset(10_000)
    yield
    call_budget.reset(limit)
