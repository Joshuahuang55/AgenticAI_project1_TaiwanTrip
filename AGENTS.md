# Repository Guidelines

## Project Structure & Module Organization

`app.py` contains FastAPI routes and the OpenAI Agents SDK runtime (Gemini via LiteLLM). `guardrails.py` defines checks and `ChatState`; `trip_context.py` extracts session preferences. `prompts/` holds agent and extraction instructions. `tools/` contains the registry, clients, and tourism loader; `tools/data/` holds bundled ranking/calibration data. `scripts/` builds datasets from sources in `data/`. `static/` is the frontend; `tests/` contains pytest tests.

## Build, Test, and Development Commands

- `uv run app.py`: start the app locally at `http://localhost:8000`.
- `uv run pytest`: run mocked tool, SDK, guardrail, and dataset tests.
- `uv sync --locked --group dev`: install dependencies without changing `uv.lock`.
- `uv run python scripts/calibrate_crowd_risk.py`: refresh crowd calibration.

Copy `.env.example` to `.env`; configure TDX, CWA, and Google Cloud credentials per `README.md`. `Procfile` defines deployment; the frontend needs no build.

## Agent & Session Contracts

This is everyday travel planning, not a verification service. Prioritize useful suggestions and reasonable estimates; keep model instructions short. Routine advice needs at most one relevant practical note, not a checklist of uncertainty.

Preserve `/chat` fields `response`, `session_id`, and `tool_calls` (`name`, `args`, `result`), plus `map_pins` with tool `kind`. SDK tracing stays disabled. Sessions retain 20 user turns, at most 200 sessions, in memory; keep Cloud Run at one instance/process. Preserve tool-call/reply pairs and clear both attraction and food caches on session removal. Exact Chinese names currently drive map pins.

Trip preferences outlive trimmed history within the same session. Keep user evidence, distinguish unknown from no preference, and roll back rejected/failed turns. Serialize session requests; clearing/eviction also removes preferences.

## Coding Style & Naming Conventions

Use four spaces and Python `snake_case`. Register tools in `tools/__init__.py`; align schemas, prompt, and README. Return JSON strings with `error` and `hint` for recoverable failures. Follow nearby frontend style; no formatter/linter is configured.

Food comparisons in `tools/food_preferences.py` prioritize dietary reports and indications before price/awards, keeping unknown facts distinct from reported matches. Preserve evidence/provenance; relative bands never verify exact meal budgets. Present choices and practical comparisons first; keep status labels internal and consolidate relevant gaps into one short note. Pass criteria through name lookups; exclude conflicts and, with explicit `confirmed_only`, unknown matches. District matches do not establish landmark proximity.

Rail ranks the whole matching timetable before limiting options. Default to earliest arrival; reserve earliest departure for an explicit user priority. Keep computed trade-offs, overnight dates, and schedules when fares fail. `arrive_by` is on the departure date; missing fares never count as free. Test with mocked responses in `tests/test_transport.py`.

Attraction preferences in `tools/attraction_preferences.py` rank before fame/local scores and survive name lookups. Settings and visit durations are category estimates, separate from listed hours/prices. Nearby groups use coordinates, never district names alone; straight-line distances are not walking routes. Time-budgeted outings return an ordered timeline with estimated city transfers, breaks, and remaining time; keep alternatives separate from stops. Save outing time/setting in session preferences and restore omitted attraction arguments. Indoor nature means relevant exhibits, not nearby gardens; mixed art venues remain useful when their outdoor portions are optional. Preserve exact-name map pins and broad-search diversity.

## Testing Guidelines

Use `test_*.py` and `test_*` functions. `tests/conftest.py` disables daily downloads, real ranking data, and live TDX authentication. Use `tests/test_app.py`'s scripted model for SDK/guardrail checks, `tests/test_trip_context.py` for sessions, `tests/test_food_preferences.py` for competing/unknown food facts, and `tests/test_tourism_data.py` for loaders/ranking. Mock HTTP and model calls. After prompt edits, inspect real `/chat` behavior separately. Run pytest before PRs; no coverage threshold exists.

## Data Maintenance

Tourism files refresh in the background daily, using saved data in ignored `data/daily/` and TDX fallback. Hotels/rail use TDX. Run ranking builders (`build_fame.py`, `build_osm_food.py`, `build_food_fame.py` in `scripts/`) only for intentional refreshes; some invoke paid models. Review generated names/labels and preserve source/license metadata, including README's education-use restrictions.

## API Limits & Live-Request Rules

TDX visitors have 20 basic-service calls/IP/day; basic members receive 3 points/month (roughly 3,000 calls). Endpoint costs/frequency vary by plan ([quotas](https://tdx.transportdata.tw/), [rail API](https://tdx.transportdata.tw/api-service/swagger/basic/5fa88b0c-120b-43f1-b188-c379ddb2593d)).

Make at most five live TDX HTTP requests/task, including tokens/retries. Reuse `tdx_get`'s six-hour cache. Stop manual probing on HTTP 429; the client may retry once when reset is within eight seconds. Report unverified behavior.

## Commit & Pull Request Guidelines

Use short imperative subjects, e.g. `Rank food by awards`. PRs describe changes, tests, linked issues, and screenshots for UI edits. Never commit `.env`; document configuration in `.env.example`.
