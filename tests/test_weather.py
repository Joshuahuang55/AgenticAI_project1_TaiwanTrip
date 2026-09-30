"""Member B's typhoon_backup_plan tool. CWA and TDX are mocked."""

import datetime as dt
import json
import ssl

import pytest

from tools import attractions, weather

TODAY = dt.datetime.now(weather.TAIPEI).date()
TOMORROW = TODAY + dt.timedelta(days=1)


def period(day: dt.date, start: str, end_day: dt.date, end: str, **value) -> dict:
    return {"StartTime": f"{day}T{start}:00+08:00", "EndTime": f"{end_day}T{end}:00+08:00",
            "ElementValue": [value]}


def week_forecast(rain_today: str = "20") -> dict:
    """Two periods today, one tomorrow, shaped like F-D0047-091."""
    slots = [(TODAY, "06:00", TODAY, "18:00"), (TODAY, "18:00", TOMORROW, "06:00"),
             (TOMORROW, "06:00", TOMORROW, "18:00")]

    def element(name, field, values):
        return {"ElementName": name, "Time": [period(*s, **{field: v}) for s, v in zip(slots, values)]}

    return {"Locations": [{"Location": [{"LocationName": "臺南市", "WeatherElement": [
        element("天氣現象", "Weather", ["晴時多雲", "多雲", "陣雨"]),
        element("12小時降雨機率", "ProbabilityOfPrecipitation", [rain_today, "10", "80"]),
        element("最低溫度", "MinTemperature", ["27", "26", "25"]),
        element("最高溫度", "MaxTemperature", ["32", "29", "28"]),
        element("天氣預報綜合描述", "WeatherDescription", ["晴時多雲。降雨機率20%。", "x", "y"]),
    ]}]}]}


def typhoon(headline: str = "海上陸上颱風警報", urgency: str = "Immediate", hours: int = 3,
            areas: tuple = ("臺南市", "高雄市")) -> dict:
    expires = (dt.datetime.now(weather.TAIPEI) + dt.timedelta(hours=hours)).isoformat()
    return {"info": [{"headline": headline, "urgency": urgency, "expires": expires,
                      "area": [{"areaDesc": a} for a in areas],
                      "description": {"section": [{"title": "命名與位置", "value": "中度颱風 範例 位於臺南西南方"}]}}]}


MUSEUM = {
    "AttractionID": "M1", "AttractionName": "奇美博物館", "Description": "西洋藝術收藏", "ServiceStatus": 1,
    "PostalAddress": {"City": "臺南市", "Town": "仁德區", "StreetAddress": "文華路二段66號"},
    "Telephones": [], "PositionLat": 22.93, "PositionLon": 120.23,
}


@pytest.fixture
def fake(monkeypatch):
    """Mock CWA by dataset and TDX; returns (cwa_calls, cwa_data, tdx_calls)."""
    cwa_calls, tdx_calls = [], []
    data = {weather.WEEK_FORECAST: week_forecast(), weather.TYPHOON_WARNING: {"info": []}}

    def fake_cwa(dataset, params):
        cwa_calls.append((dataset, params))
        return data[dataset]

    def fake_tdx(path, params):
        tdx_calls.append(params)
        return [MUSEUM]

    monkeypatch.setattr(weather, "_cwa_get", fake_cwa)
    monkeypatch.setattr(attractions, "tdx_get", fake_tdx)
    return cwa_calls, data, tdx_calls


def test_fine_day_has_forecast_and_no_backups(fake):
    cwa_calls, _, tdx_calls = fake
    out = json.loads(weather.typhoon_backup_plan("Tainan", TODAY.isoformat()))
    f = out["forecast"]
    assert (f["weather"], f["rain_chance"], f["min_temp_c"], f["max_temp_c"]) == ("晴時多雲", 20, 26, 32)
    assert len(f["periods"]) == 2 and f["description"].startswith("晴時多雲")
    assert out["is_bad_weather"] is False and out["typhoon_alert"] is None and out["backup_spots"] == []
    assert cwa_calls[0] == (weather.WEEK_FORECAST, {"LocationName": "臺南市"})
    assert tdx_calls == []  # no TDX spend on a fine day


def test_heavy_rain_attaches_pinned_indoor_backups(fake):
    _, _, tdx_calls = fake
    out = json.loads(weather.typhoon_backup_plan("台南", TOMORROW.isoformat()))
    assert out["forecast"]["rain_chance"] == 80 and out["is_bad_weather"] is True
    spot = out["backup_spots"][0]
    assert spot["name"] == "奇美博物館" and spot["lat"] == 22.93  # frontend pins it
    assert "contains(AttractionName,'博物館')" in tdx_calls[0]["$filter"]


def test_active_typhoon_warning_for_the_city_is_bad(fake):
    _, data, _ = fake
    data[weather.TYPHOON_WARNING] = typhoon()
    out = json.loads(weather.typhoon_backup_plan("Tainan"))
    assert out["typhoon_alert"].startswith("海上陸上颱風警報: 中度颱風")
    assert out["is_bad_weather"] is True and out["backup_spots"]


@pytest.mark.parametrize("bulletin", [
    typhoon(headline="解除颱風警報"),
    typhoon(urgency="Past"),
    typhoon(hours=-1),
    typhoon(areas=("基隆市", "臺北市")),
])
def test_lifted_expired_or_elsewhere_warnings_are_ignored(fake, bulletin):
    _, data, _ = fake
    data[weather.TYPHOON_WARNING] = bulletin
    out = json.loads(weather.typhoon_backup_plan("Tainan"))
    assert out["typhoon_alert"] is None and out["is_bad_weather"] is False


def test_date_beyond_forecast_gets_seasonal_note(fake):
    out = json.loads(weather.typhoon_backup_plan("Tainan", "2027-08-15"))
    assert out["forecast"] is None and out["is_bad_weather"] is None
    assert "typhoon season" in out["seasonal_note"]


def test_bad_inputs():
    assert "valid_cities" in json.loads(weather.typhoon_backup_plan("Atlantis"))
    assert "YYYY-MM-DD" in json.loads(weather.typhoon_backup_plan("Tainan", "next friday"))["hint"]
    past = (TODAY - dt.timedelta(days=1)).isoformat()
    assert "past" in json.loads(weather.typhoon_backup_plan("Tainan", past))["error"]


def test_cwa_error_is_passed_through(fake):
    _, data, _ = fake
    data[weather.WEEK_FORECAST] = {"error": "CWA request failed: HTTPError", "hint": "try later"}
    out = json.loads(weather.typhoon_backup_plan("Tainan"))
    assert out["error"].startswith("CWA request failed")


def test_missing_key_is_a_recoverable_error(monkeypatch):
    monkeypatch.delenv("CWA_API_KEY", raising=False)
    monkeypatch.setattr(weather, "_cache", {})
    out = weather._cwa_get(weather.WEEK_FORECAST, {"LocationName": "臺南市"})
    assert "not configured" in out["error"] and "hint" in out


def test_cwa_tls_keeps_verification_but_not_strict_profile():
    ctx = weather._http.get_adapter(weather.CWA_BASE).poolmanager.connection_pool_kw["ssl_context"]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert not ctx.verify_flags & ssl.VERIFY_X509_STRICT
