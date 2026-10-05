# Repository Guidelines

## Project Structure & Module Organization

`app.py` contains FastAPI routes and the OpenAI Agents SDK runtime (Gemini via LiteLLM). `guardrails.py` defines checks and `ChatState`; `trip_context.py` extracts session preferences. `agent_hooks.py` collects planning facts/diagnostics; `planning_context.py` stores compact evidence and named selections. `prompts/` holds agent and extraction instructions. `tools/agent_tools.py` defines typed SDK wrappers; `tools/` also contains the Python registry, clients, and tourism loader. `tools/data/` holds bundled ranking/calibration data. `scripts/` builds datasets from sources in `data/`. `static/` is the frontend; `tests/` contains pytest tests.

## Build, Test, and Development Commands

- `uv run app.py`: start the app locally at `http://localhost:8000`.
- `uv run pytest`: run mocked tool, SDK, guardrail, and dataset tests.
- `uv sync --locked --group dev`: install dependencies without changing `uv.lock`.
- `uv run python scripts/calibrate_crowd_risk.py`: refresh crowd calibration.

Copy `.env.example` to `.env`; configure TDX, CWA, and Google Cloud credentials per `README.md` (details in `docs/DESIGN.md`). `Procfile` defines deployment; the frontend needs no build.
`.python-version` selects Python 3.13 (the newest Google Cloud buildpacks support; 3.11 fails to deploy); `uv sync` creates `.venv` without manual activation.
Keep `pyproject.toml` and `uv.lock` aligned, and use Node.js 22+ for the frontend checks.
Fresh environment installation requires no API credentials; live use needs each contributor's
own `.env` and Google application default credentials. Never copy or commit credentials or `.venv`.

## Agent & Session Contracts

This is everyday travel planning, not a verification service. Prioritize useful suggestions and reasonable estimates; keep model instructions short. Routine advice needs at most one relevant practical note, not a checklist of uncertainty.

Use general conversation policy rather than accumulating recipes for individual example questions. Match the requested answer type and scope; tool rankings and proposed timelines support the agent's judgment. Defaults are not user requirements. Scripted tests verify contracts, not recommendation quality, and users should not need special phrasing to get useful behavior.

Allow general transport guidance beyond tool coverage, labeling cost/time estimates. Attribute sources only to relevant retrieved results; do not imply an official lookup for model knowledge or invent exact last departures/current fares.

The main Gemini agent uses medium thinking via `ModelSettings.reasoning`; keep the input classifier and preference extractor at minimal thinking rather than inheriting the main agent's effort. Preserve Vertex location and each agent's safety settings when cloning settings.

Preserve `/chat` fields `response`, `session_id`, and `tool_calls` (`name`, `args`, `result`), plus `map_pins` with tool `kind`, and `tool_quota`. `tools/call_budget.py` limits tool calls per rolling minute across all sessions (`/quota`; HTTP 429 from `/chat` when empty); `tests/conftest.py` lifts it for tests. The trip-notes panel shows English: tools add `holiday_name_en`/`weather_en` via `tools/english_labels.py`, and the frontend drops remaining Chinese glosses. SDK tracing stays disabled. Sessions retain 20 user turns, at most 200 sessions, in memory; keep Cloud Run at one instance/process. Preserve tool-call/reply pairs and clear both attraction and food caches on session removal. Map pins resolve reply references to original listing names, independent of the answer's language.

Trip preferences outlive trimmed history within the same session. Keep user evidence, distinguish unknown from no preference, and roll back rejected/failed turns. Serialize session requests; clearing cancels active work and eviction skips busy states. Idle sessions expire after six hours; turns have a 180-second deadline. Restore browser history/board on refresh and signal expired server memory.

Main-agent planning context retains city-tagged evidence across destinations within its existing size budget, prioritizing the active city without hiding the accommodation base or other legs. Helper reply excerpts retain beginning and end so follow-up questions survive long answers.

Connect meal follow-ups to hotel/sightseeing areas from the conversation and returned districts. Keep provisional planning choices separate from user preferences; vague agreement does not select every alternative. Pair hotels with their actual locations, and describe district searches without inventing walking distances.

Itineraries cover the requested duration, using day/date sections for multi-day trips and a feasible timeline for requested short-outing plans. Suggestions need no timeline merely because a time budget is known. Adapt stops/meals to pace, interests, transfers, and arrival/departure days; no fixed daily quota. Explain low-cost choices when requested, and adapt to limited results rather than inventing stops. `tools/planning_hints.py` adds conditional SDK-result suggestions without executing tools or saving preferences. Reuse matching prior weather and avoid repeat same-turn attempts. Model behavior needs a separate live check.

SDK hooks collect accepted results for the next dynamic prompt, not by mutating an already-built prompt in `on_llm_start`. Keep bounded planning evidence, provisional proposals, and named user selections separate; reuse the existing extractor for selections rather than adding a model call. Roll back planning updates on rejected turns, including output guardrails after `on_agent_end`. On provider failures/timeouts/round limits retain accepted lookups, but roll back choices/preferences/proposals. Distinguish lookup criteria and keep stable candidate IDs plus bounded selected/proposed evidence. Diagnostics are bounded, omit chat/argument/result text from logs, and never force tool calls or change `/chat` fields. Use `tests/test_agent_hooks.py` for lifecycle/session checks.

The main agent uses `TravelReply` in `agent_reply.py`. Keep its schema small; validate proposed place/source IDs against accepted session records, and render citations from returned source metadata. Unknown IDs do not block a useful answer. Remove leaked internal IDs from displayed text and labels, preserving names, ordinary links, and map references; history/proposals retain cleaned text. Guardrails/helpers read message text; history retains cleaned structured replies. Clear reply metadata on rejected/failed turns. Preserve recommended versus alternative roles without treating proposals as user selections; test English-only pins and reused evidence in `tests/test_agent_reply.py`.

## Coding Style & Naming Conventions

Use four spaces and Python `snake_case`. Register plain domain functions in `tools/__init__.py` and typed `@function_tool` wrappers in `tools/agent_tools.py`; generate schemas from annotations and Google-style `Args` docstrings, not handwritten `SCHEMA` dictionaries. Keep wrapper signatures/defaults aligned with domain functions, prompt, README, and `docs/DESIGN.md`. Use `Literal` for choices and `Annotated`/`Field` for numeric bounds. Gemini wrappers keep `strict_mode=False` for optional arguments; SDK validation errors still need recorded JSON replies. Preserve worker-thread execution, session restoration, and tool guardrails in the shared adapter. Return `error` and `hint` for recoverable failures. Follow nearby frontend style; no formatter/linter is configured.

Food comparisons in `tools/food_preferences.py` prioritize dietary reports and indications before price/awards, keeping unknown facts distinct from reported matches. Preserve evidence/provenance; relative bands never verify exact meal budgets. Present choices and practical comparisons first; keep status labels internal and consolidate relevant gaps into one short note. Pass criteria through name lookups; exclude conflicts and, with explicit `confirmed_only`, unknown matches. District matches do not establish landmark proximity.

Rail ranks the whole matching timetable before limiting options. Default to earliest arrival; reserve earliest departure for an explicit user priority. Keep computed trade-offs, overnight dates, and schedules when fares fail. `arrive_by` is on the departure date; missing fares never count as free. Test with mocked responses in `tests/test_transport.py`.

Lodging list mode ranks a cached pool of up to 500 registered stays before limiting results. Use district/type constraints and reported starting rates; qualitative budgets never invent numeric caps. Missing rates remain eligible and never count as free; numeric caps exclude known higher starts, not unknown prices. Reference prices do not verify every room/date. Apply recognized explicit result counts in the SDK wrapper; do not confuse nights/guests/prices with counts. Preserve check-mode matching, licensed status versus quality, and existing `stays` UI/map fields. District matches never establish MRT walking distance.

Attraction preferences in `tools/attraction_preferences.py` rank before fame/local scores and survive name lookups. Settings and visit durations are category estimates, separate from listed hours/prices. Nearby groups use coordinates, never district names alone; straight-line distances are not walking routes. Time-budgeted outings return an ordered timeline with estimated city transfers, breaks, and remaining time; keep alternatives separate from stops. Stop counts depend on time and suitable candidates, with no fixed three-stop cap. Requested itineraries account for unused time explicitly, without stretching visits just to fill it. Reconcile clock windows with duration, mark derived values, and restore outing arguments only for matching city/date. Indoor nature means relevant exhibits, not nearby gardens; mixed art venues remain useful when their outdoor portions are optional. Preserve reference-based map pins and broad-search diversity.

Weather comparisons use overlapping CWA intervals, aligning elements by timestamps and including preceding overnight periods. Preserve daily summaries but base outing advice on the selected window; missing probabilities/coverage are unknown, never zero. Rain thresholds are planning heuristics, not warning levels or rainfall intensity. Weather performs no attraction lookup; the agent decides whether to call `find_attractions` separately, reusing district/interests and the time budget. Record actual SDK calls separately in `/chat.tool_calls`; never fabricate attraction entries for hidden helper work. Current warnings only govern today's outing; postpone sightseeing during an active warning. Preserve `forecast` and `is_bad_weather`; legacy `backup_spots` stays empty and map pins use the attraction search. Save sightseeing clock windows separately from train times; test weather-only and combined SDK runs with mocked data.

## Testing Guidelines

Use `test_*.py` and `test_*` functions. `tests/conftest.py` disables daily downloads, real ranking data, and live TDX authentication. Use `tests/test_app.py`'s scripted model for SDK/guardrail checks, `tests/test_agent_tools.py` for generated schemas and validation, `tests/test_trip_context.py` for sessions, `tests/test_food_preferences.py` for food comparisons, and `tests/test_tourism_data.py` for loaders/ranking. Mock HTTP and model calls. After prompt edits, inspect real `/chat` behavior separately. Run pytest before PRs; no coverage threshold exists. CI also runs the dependency-free Node frontend checks (`node --test tests/frontend_state.test.cjs`); mocked TDX token/cache/retry checks live in `tests/test_tdx_client.py`.

Scope board cards by route/date/location, including errors and empty results. Deduplicate/reconcile proposed map pins and show English names only (`english_name` romanizes Chinese-only listings); the OpenFreeMap vector basemap uses English or romanized labels and leaves others unlabeled, over a label-free Esri Light Gray raster that also serves as the fallback.
Keep optional map assets asynchronous with a bounded loading deadline. Formatting/storage/map
failures must not block chat, and every send path must release the pending state in `finally`.

Check mode must distinguish uniquely matched lodging from similar-name candidates. Exchange trends use weekly samples over four weeks, not a daily average; absent history leaves a usable conversion and null trend. Clients collect `data_freshness` for tool responses. Weather fallback ages are bounded (forecasts two hours, warnings 15 minutes); never reuse expired session weather as current.

## Data Maintenance

Tourism files refresh in the background daily, using saved data in ignored `data/daily/` and TDX fallback. Hotels/rail use TDX. Run ranking builders (`build_fame.py`, `build_osm_food.py`, `build_food_fame.py` in `scripts/`) only for intentional refreshes; some invoke paid models. Review generated names/labels and preserve source/license metadata, including the education-use restrictions in README and `docs/DESIGN.md`. Keep README short (what graders need); put detail in `docs/DESIGN.md`.

## API Limits & Live-Request Rules

TDX visitors have 20 basic-service calls/IP/day; basic members receive 3 points/month (roughly 3,000 calls). Endpoint costs/frequency vary by plan ([quotas](https://tdx.transportdata.tw/), [rail API](https://tdx.transportdata.tw/api-service/swagger/basic/5fa88b0c-120b-43f1-b188-c379ddb2593d)).

Make at most five live TDX HTTP requests/task, including tokens/retries. Reuse `tdx_get`'s six-hour cache. Stop manual probing on HTTP 429; the client may retry once when reset is within eight seconds. Report unverified behavior.

## Commit & Pull Request Guidelines

Use short imperative subjects, e.g. `Rank food by awards`. PRs describe changes, tests, linked issues, and screenshots for UI edits. Never commit `.env`; document configuration in `.env.example`.
