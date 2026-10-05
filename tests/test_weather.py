"""Member B's typhoon_backup_plan tool. CWA and TDX are mocked."""

import datetime as dt
import json
import ssl

import pytest

from tools import attractions, weather
from test_app import _call, _chat, _reply, _text, model  # scripted SDK model; no provider requests

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
    assert f["weather_en"] == "Sunny, at times partly cloudy" and f["periods"][1]["weather_en"] == "Partly cloudy"
    assert out["is_bad_weather"] is False and out["typhoon_alert"] is None and out["backup_spots"] == []
    assert cwa_calls[0] == (weather.WEEK_FORECAST, {"LocationName": "臺南市"})
    assert tdx_calls == []  # no TDX spend on a fine day


def test_heavy_rain_returns_strategy_without_searching_attractions(fake):
    _, _, tdx_calls = fake
    out = json.loads(weather.typhoon_backup_plan("台南", TOMORROW.isoformat()))
    assert out["forecast"]["rain_chance"] == 80 and out["is_bad_weather"] is True
    assert out["comparison"]["strategy"] == "indoor"
    assert out["backup_spots"] == [] and tdx_calls == []
    assert "backup_comparison" not in out and out["source"] == "Central Weather Administration (CWA)"


def test_active_typhoon_warning_for_the_city_is_bad(fake):
    _, data, _ = fake
    data[weather.TYPHOON_WARNING] = typhoon()
    out = json.loads(weather.typhoon_backup_plan("Tainan"))
    assert out["typhoon_alert"].startswith("海上陸上颱風警報: 中度颱風")
    assert out["is_bad_weather"] is True and out["backup_spots"] == []
    assert out["comparison"]["strategy"] == "postpone_outing"


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


def elements(data):
    return {e["ElementName"]: e for e in data[weather.WEEK_FORECAST]["Locations"][0]["Location"][0]["WeatherElement"]}


def test_evening_rain_does_not_cancel_a_morning_outing(fake):
    _, data, tdx_calls = fake
    elements(data)["12小時降雨機率"]["Time"][1]["ElementValue"][0]["ProbabilityOfPrecipitation"] = "90"
    out = json.loads(weather.typhoon_backup_plan("Tainan", start_time="09:00", end_time="13:00"))
    assert out["forecast"]["rain_chance"] == 90  # Daily summary remains available.
    assert out["comparison"]["rain_chance"] == 20
    assert out["comparison"]["strategy"] == "outdoor" and out["is_bad_weather"] is False
    assert out["backup_spots"] == [] and tdx_calls == []


def test_split_outing_keeps_both_forecast_periods(fake):
    _, data, _ = fake
    elements(data)["12小時降雨機率"]["Time"][1]["ElementValue"][0]["ProbabilityOfPrecipitation"] = "90"
    out = json.loads(weather.typhoon_backup_plan("Tainan", start_time="15:00", end_time="21:00"))
    plan = out["comparison"]
    assert plan["strategy"] == "split"
    assert [(p["outing_start"], p["outing_end"]) for p in plan["periods"]] == [("15:00", "18:00"), ("18:00", "21:00")]
    assert plan["lowest_rain_period"]["rain_chance"] == 20 and out["backup_spots"] == []


def test_weather_elements_align_by_timestamp_not_array_index(fake):
    _, data, _ = fake
    elements(data)["12小時降雨機率"]["Time"].reverse()
    out = json.loads(weather.typhoon_backup_plan("Tainan", start_time="09:00", end_time="12:00"))
    assert out["comparison"]["rain_chance"] == 20


def test_missing_interval_is_not_shifted_to_another_period(fake):
    _, data, _ = fake
    elements(data)["12小時降雨機率"]["Time"].pop(0)
    out = json.loads(weather.typhoon_backup_plan("Tainan", start_time="09:00", end_time="12:00"))
    assert out["comparison"]["rain_chance"] is None
    assert out["comparison"]["strategy"] == "flexible" and out["is_bad_weather"] is None


@pytest.mark.parametrize("value", ["", "-99", "101", None])
def test_invalid_rain_chance_never_becomes_zero(fake, value):
    _, data, _ = fake
    elements(data)["12小時降雨機率"]["Time"][0]["ElementValue"][0]["ProbabilityOfPrecipitation"] = value
    out = json.loads(weather.typhoon_backup_plan("Tainan", start_time="09:00", end_time="12:00"))
    assert out["is_bad_weather"] is None and out["comparison"]["rain_chance"] is None


def test_overnight_period_from_previous_day_covers_early_morning(fake):
    out = json.loads(weather.typhoon_backup_plan("Tainan", TOMORROW.isoformat(), start_time="01:00", end_time="04:00"))
    assert out["comparison"]["rain_chance"] == 10
    assert out["comparison"]["forecast_covers_window"] is True
    assert out["is_bad_weather"] is False


def test_partial_coverage_never_becomes_an_all_clear(fake):
    out = json.loads(weather.typhoon_backup_plan("Tainan", start_time="01:00", end_time="10:00"))
    assert out["comparison"]["forecast_covers_window"] is False
    assert out["comparison"]["strategy"] == "flexible" and out["is_bad_weather"] is None


def test_current_warning_is_not_a_future_date_forecast(fake):
    _, data, _ = fake
    data[weather.TYPHOON_WARNING] = typhoon()
    out = json.loads(weather.typhoon_backup_plan("Tainan", TOMORROW.isoformat()))
    assert out["current_typhoon_alert"] and out["typhoon_alert"] is None
    assert out["comparison"]["strategy"] == "indoor"  # Tomorrow's rain, not today's warning.
    assert out["comparison"]["warning_applies_to_outing"] is False
    assert "future" in out["comparison"]["warning_note"]


def test_warning_feed_failure_is_distinct_from_no_warning(fake):
    _, data, _ = fake
    data[weather.TYPHOON_WARNING] = {"error": "unavailable", "hint": "later"}
    out = json.loads(weather.typhoon_backup_plan("Tainan"))
    assert out["comparison"]["warning_status"] == "unavailable"
    assert out["forecast"] and out["comparison"]["strategy"] == "outdoor"


def test_missing_near_term_date_is_not_called_too_far_away(fake):
    _, data, _ = fake
    for element in elements(data).values():
        element["Time"] = element["Time"][:2]
    out = json.loads(weather.typhoon_backup_plan("Tainan", TOMORROW.isoformat(), start_time="09:00", end_time="12:00"))
    assert out["comparison"]["strategy"] == "forecast_unavailable"
    assert out["comparison"]["forecast_covers_window"] is False


def test_current_warning_still_returned_without_a_forecast(fake):
    _, data, tdx_calls = fake
    for element in elements(data).values():
        element["Time"] = []
    data[weather.TYPHOON_WARNING] = typhoon()
    out = json.loads(weather.typhoon_backup_plan("Tainan"))
    assert out["forecast"] is None and out["is_bad_weather"] is True
    assert out["comparison"]["strategy"] == "postpone_outing" and tdx_calls == []


def test_window_caps_larger_outing_budget(fake):
    out = json.loads(weather.typhoon_backup_plan("Tainan", TOMORROW.isoformat(), available_minutes=300,
                                               start_time="09:00", end_time="11:00"))
    assert out["comparison"]["available_minutes"] == 120


@pytest.mark.parametrize("kwargs", [{"start_time": "9am"}, {"end_time": "25:00"},
                                   {"start_time": "17:00", "end_time": "10:00"},
                                   {"start_time": "23:00", "available_minutes": 120},
                                   {"available_minutes": True}, {"available_minutes": 721}])
def test_invalid_preferences_fail_before_requests(fake, kwargs):
    calls, _, tdx_calls = fake
    assert "error" in json.loads(weather.typhoon_backup_plan("Tainan", **kwargs))
    assert calls == [] and tdx_calls == []


def test_sdk_weather_only_does_not_force_an_attraction_call(fake, model):
    _, _, tdx_calls = fake
    model([[_call("typhoon_backup_plan", json.dumps({"city": "Tainan", "date": TOMORROW.isoformat()}))],
           [_text("Rain chance is 80%; an indoor plan would be useful.")]])
    result = _chat("Check tomorrow's weather in Tainan, weather only.")
    assert [call["name"] for call in result.tool_calls] == ["typhoon_backup_plan"]
    assert tdx_calls == [] and result.map_pins == []


def test_sdk_agent_can_call_attractions_after_weather_and_both_are_visible(fake, model, monkeypatch):
    import app
    museum = dict(MUSEUM, AttractionID="H1", AttractionName="歷史博物館", Description="歷史收藏", AttractionClasses=[3])
    art = dict(MUSEUM, AttractionID="A1", AttractionName="臺南美術館", AttractionClasses=[5])
    nature = dict(MUSEUM, AttractionID="N1", AttractionName="自然史博物館", Description="自然科學與生物多樣性",
                  PositionLat=22.935)
    attraction_requests = []
    def get(path, params):
        attraction_requests.append((path, params))
        return [museum, art, nature] if path == attractions.ATTRACTION_PATH else []
    monkeypatch.setattr(attractions, "tdx_get", get)
    monkeypatch.setattr(attractions, "FAME", {"H1": 1.0})
    weather_args = {"city": "Tainan", "date": TOMORROW.isoformat(), "start_time": "10:00", "end_time": "15:00"}
    attraction_args = {"city": "Tainan", "district": "仁德區", "interests": ["art", "nature"], "setting": "indoor", "limit": 3}
    scripted = model([[_call("typhoon_backup_plan", json.dumps(weather_args))],
                      [_call("find_attractions", json.dumps(attraction_args), call_id="c1")],
                          _reply("With 80% rain, visit Tainan Art Museum and the Natural History Museum.",
                                 names=[("臺南美術館", "Tainan Art Museum"), ("自然史博物館", "Natural History Museum")])],
                     context_updates=[[{"field": "city", "operation": "set", "value": "Tainan", "evidence": "Tainan"},
                                       {"field": "available_minutes", "operation": "set", "value": "300", "evidence": "five hours"}]])
    result = _chat("Tainan tomorrow, five hours from 10am to 3pm, art and nature around Rende. Check weather and suggest an outing.")
    assert [call["name"] for call in result.tool_calls] == ["typhoon_backup_plan", "find_attractions"]
    weather_result, attraction_result = [json.loads(call["result"]) for call in result.tool_calls]
    assert weather_result["backup_spots"] == [] and weather_result["comparison"]["available_minutes"] == 300
    assert result.tool_calls[1]["args"]["available_minutes"] == 300
    plan = attraction_result["comparison"]["suggested_visit"]
    assert plan["names"] == [art["AttractionName"], nature["AttractionName"]]
    assert sum(step["estimated_minutes"] for step in plan["timeline"]) == plan["estimated_minutes"] <= 300
    assert "PostalAddress/Town eq '仁德區'" in attraction_requests[0][1]["$filter"]
    assert [p["name"] for p in result.map_pins] == plan["names"]
    assert all(p["kind"] == "find_attractions" for p in result.map_pins)
    # The second main model turn receives the weather result before choosing its attraction call.
    replies = [i for i in scripted.main_inputs[1] if i.get("type") == "function_call_output"]
    assert json.loads(replies[0]["output"])["comparison"]["strategy"] == "indoor"
    assert app.sessions[result.session_id].trip.preferences["available_minutes"].value == "300"
    assert "setting" not in app.sessions[result.session_id].trip.preferences
    assert "your indoor preference" not in attraction_result["comparison"]["reason"]


def test_attraction_failure_after_weather_keeps_weather_result_visible(fake, model, monkeypatch):
    monkeypatch.setattr(attractions, "tdx_get", lambda *a: {"error": "temporarily unavailable", "hint": "try later"})
    model([[_call("typhoon_backup_plan", json.dumps({"city": "Tainan", "date": TOMORROW.isoformat()}))],
           [_call("find_attractions", '{"city":"Tainan","setting":"indoor"}', call_id="c1")],
           [_text("Rain chance is 80%. I could not retrieve attraction options this time.")]])
    result = _chat("Check weather in Tainan tomorrow and suggest indoor options.")
    assert [call["name"] for call in result.tool_calls] == ["typhoon_backup_plan", "find_attractions"]
    assert json.loads(result.tool_calls[0]["result"])["forecast"]["rain_chance"] == 80
    assert json.loads(result.tool_calls[1]["result"])["error"] == "temporarily unavailable"
    assert result.map_pins == []
