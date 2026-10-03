"""The Tourism Administration's daily open-data files, held in memory and searched locally.

They carry the same V2.1 records TDX serves (https://data.gov.tw/dataset/7777 and 7779), as one
download a day: no TDX quota and no 500-row cap. Hotels stay on TDX: their file needs about
190 MB to parse, too much for a 512 MiB Cloud Run instance.

listings() returns None until a file is loaded or when its download failed; callers then
query TDX instead, so the app works either way.

Each download is also saved to CACHE_DIR (data/daily/), so a restart within a day reads the saved file
(under a second) instead of downloading again (up to 90 seconds, with TDX answering meanwhile). Cloud
Run starts every instance from the image, so there a cold start still downloads.
"""

import io
import json
import os
import threading
import time
import zipfile
from pathlib import Path

import requests

from tools.gov_tls import gov_session

BASE_URL = "https://media.taiwan.net.tw/XMLReleaseAll_public/v2.0/Zh_tw/"
# dataset -> (zip file, {part: JSON file in the zip})
DATASETS = {
    "attractions": ("Attraction-json.zip", {"rows": "AttractionList.json",
                                            "hours": "AttractionServiceTimeList.json",
                                            "fees": "AttractionFeeList.json"}),
    "restaurants": ("Restaurant-json.zip", {"rows": "RestaurantList.json"}),
}
NAME_FIELD = {"attractions": "AttractionName", "restaurants": "RestaurantName"}
ID_FIELD = {"attractions": "AttractionID", "restaurants": "RestaurantID"}
SOURCE = "Taiwan Tourism Administration open data (data.gov.tw)"
REFRESH_SECONDS = 24 * 60 * 60  # the files are republished daily
RETRY_SECONDS = 15 * 60  # after a failed download
TIMEOUT = 180  # the server is slow: a 1-3 MB file takes 10-90 seconds
_http = gov_session(BASE_URL)  # its certificate fails Python 3.13+'s strict checks
# In the project (git-ignored); TOURISM_CACHE_DIR moves it.
CACHE_DIR: Path | None = Path(os.environ.get("TOURISM_CACHE_DIR") or Path(__file__).resolve().parents[1] / "data" / "daily")

_data: dict[str, dict] = {}  # dataset -> {"loaded_at": time, "parts": {part: list}}
_failed_at: dict[str, float] = {}
_loading: set[str] = set()
_lock = threading.Lock()


def _download(dataset: str) -> dict[str, list]:
    filename = DATASETS[dataset][0]
    resp = _http.get(BASE_URL + filename, timeout=TIMEOUT)
    resp.raise_for_status()
    parts = _parse(dataset, resp.content)  # only a file that parses is saved
    _save(filename, resp.content)
    return parts


def _parse(dataset: str, content: bytes) -> dict[str, list]:
    parts = DATASETS[dataset][1]
    out = {}
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        for part, member in parts.items():
            body = json.loads(z.read(member).decode("utf-8-sig"))
            rows = next((v for v in body.values() if isinstance(v, list)), None) if isinstance(body, dict) else body
            if not isinstance(rows, list):
                raise ValueError(f"{member} has no record list")
            out[part] = rows
    # File order is not guaranteed; TDX returns ID order, which ties in ranking keep.
    out["rows"].sort(key=lambda r: r.get(ID_FIELD[dataset]) or "")
    return out


def _save(filename: str, content: bytes) -> None:
    """Write the zip to CACHE_DIR (via a temporary file, so a crash never leaves half a file)."""
    if CACHE_DIR is None:
        return
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_DIR / (filename + ".part")
        tmp.write_bytes(content)
        tmp.replace(CACHE_DIR / filename)
    except OSError:
        pass  # a read-only disk only costs the cache


def _load_saved(dataset: str) -> dict | None:
    """The saved copy as a _data entry, dated by when it was downloaded, or None if missing or unreadable."""
    path = CACHE_DIR / DATASETS[dataset][0] if CACHE_DIR else None
    if path is None or not path.exists():
        return None
    try:
        return {"loaded_at": path.stat().st_mtime, "parts": _parse(dataset, path.read_bytes())}
    except (OSError, zipfile.BadZipFile, KeyError, ValueError):
        return None


def refresh(dataset: str) -> bool:
    """Download a dataset now. On failure the previous copy, if any, stays in use."""
    try:
        parts = _download(dataset)
    except (requests.RequestException, zipfile.BadZipFile, KeyError, ValueError):
        with _lock:
            _failed_at[dataset] = time.time()
            _loading.discard(dataset)
        return False
    with _lock:
        _data[dataset] = {"loaded_at": time.time(), "parts": parts}
        _loading.discard(dataset)
    return True


def _parts(dataset: str) -> dict[str, list] | None:
    """The loaded files, or None. A missing or day-old copy is refreshed in the background."""
    now = time.time()
    with _lock:
        if dataset not in _data and dataset not in _loading and (saved := _load_saved(dataset)):
            _data[dataset] = saved
        entry = _data.get(dataset)
        due = entry is None or now - entry["loaded_at"] > REFRESH_SECONDS
        start = due and dataset not in _loading and now - _failed_at.get(dataset, 0) > RETRY_SECONDS
        if start:
            _loading.add(dataset)
    if start:
        _start_refresh(dataset)
    return entry["parts"] if entry else None


def _start_refresh(dataset: str) -> None:
    threading.Thread(target=refresh, args=(dataset,), daemon=True).start()


def warm() -> None:
    """Start downloading every dataset in the background; the tools use TDX until they arrive."""
    for dataset in DATASETS:
        _parts(dataset)


def listings(dataset: str, county: str, town: str | None = None, words=(), name_only: bool = False) -> list | None:
    """Records in a county (and district) whose name, or description unless name_only, contains any
    of `words`: the local version of the tools' TDX filters. None when the file is not loaded."""
    parts = _parts(dataset)
    if parts is None:
        return None
    name_field = NAME_FIELD[dataset]
    out = []
    for r in parts["rows"]:
        addr = r.get("PostalAddress") or {}
        if addr.get("City") != county or (town and addr.get("Town") != town):
            continue
        text = (r.get(name_field) or "") if name_only else f"{r.get(name_field) or ''} {r.get('Description') or ''}"
        if not words or any(w in text for w in words):
            out.append(r)
    return out


def extra(dataset: str, part: str) -> list | None:
    """Another file of a loaded dataset (e.g. attraction hours or fees), or None."""
    parts = _parts(dataset)
    return parts.get(part) if parts else None
