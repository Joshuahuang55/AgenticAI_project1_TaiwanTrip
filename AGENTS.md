# Repository Guidelines

## Project Structure & Module Organization

`app.py` contains FastAPI routes and the LiteLLM agent loop; `prompts/system.txt` supplies the system prompt. `tools/` holds the registry, TDX client, and data tools, including `tools/data/crowd_calibration.json`. `scripts/calibrate_crowd_risk.py` refreshes that data. `static/` is the plain HTML, CSS, and JavaScript frontend; `tests/` contains pytest tests. See `README.md` for tool behavior.

## Build, Test, and Development Commands

- `uv run app.py`: start the app locally at `http://localhost:8000`.
- `uv run pytest`: run the test suite; external data requests are mocked.
- `uv sync --group dev`: install project and test dependencies from `uv.lock`.
- `uv run python scripts/calibrate_crowd_risk.py`: refresh the bundled crowd calibration from official data files.

Copy `.env.example` to `.env`, set TDX credentials, and configure Google Cloud application default credentials per `README.md`. `Procfile` defines the deployment command. The frontend has no build step.

## Coding Style & Naming Conventions

Use four-space indentation and `snake_case` for Python functions and modules. Add tools in `tools/` and register them in `tools/__init__.py`. Keep tool descriptions aligned with `prompts/system.txt`. Return JSON strings with `error` and `hint` for recoverable failures. Follow nearby JavaScript and CSS style in `static/`; no formatter or linter is configured.

## Testing Guidelines

Use pytest with `test_*.py` files and `test_*` functions. Cover results, errors, and argument handling. Mock network calls as in `tests/test_member_c_tools.py`, `tests/test_transport.py`, and `tests/test_holidays.py`. Pytest does not validate model behavior; after prompt edits, restart the app and inspect `/chat` tool calls. Run `uv run pytest` before a pull request; no coverage threshold is configured.

## API Limits & Live-Request Rules

TDX allows visitors 20 basic-service calls per source IP per day. Basic members receive 3 points/month across services (roughly 3,000 calls); endpoint costs and request-frequency limits vary by plan ([TDX quotas](https://tdx.transportdata.tw/), [rail API example](https://tdx.transportdata.tw/api-service/swagger/basic/5fa88b0c-120b-43f1-b188-c379ddb2593d)).

For development checks, make at most five live TDX HTTP requests per task, including tokens and retries. Mock tests and reuse `tdx_get`, which caches successes for six hours. Stop manual probing on HTTP 429; the client may retry once if the limit resets within eight seconds. Report unverified behavior.

## Commit & Pull Request Guidelines

Use short, imperative commit subjects, like `Add member C tools and new trip-board frontend`. Pull requests should describe changes, list test results, link relevant issues, and include screenshots for `static/` UI changes. Never commit `.env`; document variable names in `.env.example`.
