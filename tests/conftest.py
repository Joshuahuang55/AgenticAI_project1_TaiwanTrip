"""Shared fixtures."""

import pytest

from tools import tdx_client, tourism_data


@pytest.fixture(autouse=True)
def no_official_files(monkeypatch):
    """Tools fall back to (mocked) TDX: no test downloads the daily open-data files.
    A test that needs them sets tourism_data._data itself."""
    monkeypatch.setattr(tourism_data, "_data", {})
    monkeypatch.setattr(tourism_data, "_failed_at", {})
    monkeypatch.setattr(tourism_data, "_loading", set())
    monkeypatch.setattr(tourism_data, "_start_refresh", lambda dataset: None)


@pytest.fixture(autouse=True)
def no_live_tdx(monkeypatch):
    """An unmocked tdx_get answers 'credentials not configured' instead of calling TDX."""
    monkeypatch.setattr(tdx_client, "_get_token", lambda: None)
