"""Shared TDX (Transport Data eXchange) client: token, caching, rate limits, city names.

Every TDX-backed tool goes through `tdx_get`. It never raises: on failure it returns
{"error": ..., "hint": ...} so tools can hand that straight back to the model.
"""

import os
import time
import threading
from functools import wraps

import requests
from tools.freshness import observe

TOKEN_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
API_BASE = "https://tdx.transportdata.tw/api"
TIMEOUT = 10
CACHE_TTL = 6 * 60 * 60  # Avoid repeated requests to TDX's rate-limited APIs.

_token = {"value": None, "expires_at": 0.0}
_cache: dict[tuple, tuple[float, list | dict]] = {}
_request_lock = threading.RLock()


def serialized(function):
    @wraps(function)
    def locked(*args, **kwargs):
        with _request_lock:
            return function(*args, **kwargs)
    return locked


def cached_result(path, cached, *, stale=False):
    observe("TDX " + path, cached[0], CACHE_TTL, stale=stale)
    return cached[1]

# English (and common spellings) -> the county/city name TDX uses in PostalAddress/City.
CITIES = {
    "taipei": "臺北市", "new taipei": "新北市", "taoyuan": "桃園市", "taichung": "臺中市",
    "tainan": "臺南市", "kaohsiung": "高雄市", "keelung": "基隆市", "hsinchu": "新竹市",
    "hsinchu county": "新竹縣", "miaoli": "苗栗縣", "changhua": "彰化縣", "nantou": "南投縣",
    "yunlin": "雲林縣", "chiayi": "嘉義市", "chiayi county": "嘉義縣", "pingtung": "屏東縣",
    "yilan": "宜蘭縣", "hualien": "花蓮縣", "taitung": "臺東縣", "penghu": "澎湖縣",
    "kinmen": "金門縣", "matsu": "連江縣", "lienchiang": "連江縣",
}

# Famous destinations travelers name instead of the county: -> (county, township).
PLACES = {
    "jiufen": ("新北市", "瑞芳區"), "tamsui": ("新北市", "淡水區"), "danshui": ("新北市", "淡水區"),
    "kenting": ("屏東縣", "恆春鎮"), "sun moon lake": ("南投縣", "魚池鄉"),
    "alishan": ("嘉義縣", "阿里山鄉"), "taroko": ("花蓮縣", "秀林鄉"),
}


def resolve_city(city: str) -> tuple[str, str | None] | None:
    """Map 'Tainan', 'tainan city', '台南', or 'Jiufen' to (TDX county name, township or None)."""
    key = city.strip().lower().replace("-", " ")
    for suffix in (" city", " county"):
        if key.endswith(suffix) and key not in CITIES:
            key = key[: -len(suffix)]
    if key in PLACES:
        return PLACES[key]
    if key in CITIES:
        return CITIES[key], None
    # Chinese input: accept 台/臺 and a missing 市/縣.
    zh = city.strip().replace("台", "臺")
    for name in set(CITIES.values()):
        if zh in (name, name[:-1]):
            return name, None
    return None


def city_choices() -> list[str]:
    return sorted({k.title() for k in CITIES} | {k.title() for k in PLACES})


def odata_quote(text: str) -> str:
    """Escape a value for use inside an OData string literal."""
    return text.replace("'", "''")


@serialized
def _get_token() -> str | None:
    if _token["value"] and time.time() < _token["expires_at"] - 60:
        return _token["value"]
    client_id, secret = os.environ.get("TDX_CLIENT_ID"), os.environ.get("TDX_CLIENT_SECRET")
    if not client_id or not secret:
        return None
    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    _token["value"] = body["access_token"]
    _token["expires_at"] = time.time() + body.get("expires_in", 3600)
    return _token["value"]


@serialized
def tdx_get(path: str, params: dict) -> list | dict:
    """GET a TDX endpoint (path relative to /api, e.g. 'tourism/service/odata/V2/Tourism/Hotel').

    Returns the list of records, or {"error", "hint"}. Successful responses are cached,
    and a stale cached copy is served if TDX is rate-limiting or down.
    """
    key = (path, tuple(sorted(params.items())))
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached_result(path, cached)

    try:
        token = _get_token()
        if token is None:
            return {
                "error": "TDX credentials are not configured on the server.",
                "hint": "Tell the user this data source is temporarily unavailable. Do not invent results.",
            }
        for attempt in range(2):
            resp = requests.get(
                f"{API_BASE}/{path}",
                params={**params, "$format": "JSON"},
                headers={"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"},
                timeout=TIMEOUT,
            )
            if resp.status_code != 429:
                break
            wait = max(0, int(resp.headers.get("ratelimit-reset", "60")))
            if attempt == 0 and wait <= 8:
                time.sleep(wait + 1)
                continue
            if cached:
                return cached_result(path, cached, stale=True)
            return {
                "error": f"TDX rate limit reached (free tier). Resets in about {wait} seconds.",
                "hint": "Answer with what you already know, and tell the user they can ask again in a minute.",
            }
        resp.raise_for_status()
        body = resp.json()
    except (requests.RequestException, ValueError, KeyError) as e:
        if cached:
            return cached_result(path, cached, stale=True)
        return {
            "error": f"TDX request failed: {type(e).__name__}",
            "hint": "The TDX data service is unreachable. Tell the user and suggest trying again shortly.",
        }

    records = body.get("value", body) if isinstance(body, dict) else body
    _cache[key] = (time.time(), records)
    observe("TDX " + path, _cache[key][0], CACHE_TTL)
    return records
