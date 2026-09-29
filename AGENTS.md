# Repository Guidelines

## Project Structure & Module Organization

`app.py` defines the FastAPI routes, in-memory chat sessions, and LiteLLM agent loop. `tools/` contains the tool registry (`__init__.py`), TDX client, lodging and food lookups, currency conversion, and starter weather tool. `static/` holds the HTML, CSS, and JavaScript frontend; `tests/` holds pytest tests for the data tools. See `README.md` for sample conversations and tool behavior.

## Build, Test, and Development Commands

- `uv run app.py`: start the app locally at `http://localhost:8000`.
- `uv run pytest`: run the test suite; external data requests are mocked.
- `uv sync --group dev`: install project and test dependencies from `uv.lock`.

Before running the app, copy `.env.example` to `.env`, set the TDX client credentials, and configure Google Cloud application default credentials as described in `README.md`. `Procfile` starts the same Python app for deployment. There is no separate frontend build step.

## Coding Style & Naming Conventions

Use four-space indentation and `snake_case` for Python functions and modules. Keep tool implementations in `tools/` and register callable tools through `tools/__init__.py`. Match the existing tool contract: return JSON strings, and include `error` and `hint` on recoverable failures. Keep frontend changes in `static/` and follow its existing plain JavaScript and CSS style. No formatter or linter is configured in `pyproject.toml`; keep changes consistent with nearby code.

## Testing Guidelines

Use pytest. Name test files `test_*.py` and test functions `test_*`. Add focused tests for tool results, errors, and argument handling when changing a tool. Mock TDX and exchange-rate calls, as `tests/test_member_c_tools.py` does, so tests do not depend on live services. Run `uv run pytest` before opening a pull request. No coverage threshold is configured.

TDX gives basic members 3 points/month across data services (roughly 3,000 calls); visitor mode allows 20 basic-service calls/day per source IP ([TDX quotas](https://tdx.transportdata.tw/)). Point costs and request-frequency limits vary by service and plan ([rail API example](https://tdx.transportdata.tw/api-service/swagger/basic/5fa88b0c-120b-43f1-b188-c379ddb2593d)). Make at most five live TDX HTTP requests per task, counting token requests and retries. Disable automatic retries; stop on HTTP 429 and report any unverified behavior.

## Commit & Pull Request Guidelines

Recent commits use short, descriptive subjects such as `Add member C tools and new trip-board frontend`; use an imperative verb for new changes. In pull requests, describe the behavior changed, list test results, and link any relevant issue. Include screenshots when changing `static/` UI. Keep secrets out of commits: `.env` is local configuration, while `.env.example` documents required variable names.
