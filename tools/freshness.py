"""Per-tool data ages collected without changing domain clients' return types."""

import datetime as dt
import time
from contextvars import ContextVar

_observations = ContextVar("data_freshness", default=None)


def observe(source, fetched_at, ttl, *, stale=None):
    age = max(0, time.time() - fetched_at)
    entry = {"source": source, "retrieved_at": dt.datetime.fromtimestamp(
        fetched_at, dt.timezone.utc).isoformat(), "age_seconds": round(age),
        "stale": age >= ttl if stale is None else stale}
    observations = _observations.get()
    if observations is not None:
        observations[source] = entry
    return entry


def begin():
    return _observations.set({})


def finish(token):
    entries = list((_observations.get() or {}).values())
    _observations.reset(token)
    return entries
