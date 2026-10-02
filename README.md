# Taiwan Like a Local

A chat agent that acts like a local friend for foreigners traveling in Taiwan. It looks things
up in official Taiwanese open data instead of guessing: whether a B&B is legally registered,
where locals eat, what to see, whether a typhoon is coming, and how far your budget goes in TWD. Every tool call is shown in the chat,
and a trip board (map, stay check, budget) is drawn from the tool results.

## Sample queries

1. `I found a cheap B&B in Hualien called "你來花蓮民宿". Is it legal? Can you suggest some registered ones?`
   → `legal_stay_check` twice: confirms license 花蓮縣民宿2170號, then lists registered alternatives on the map.
2. `I have $1,500 USD for a week. How much is that in TWD, and find me registered B&Bs in Tainan under 3,000 TWD a night.`
   → `twd_exchange` (with a 30-day comparison), then `legal_stay_check` with a price cap.
3. `What should I eat in Tainan? I want beef soup for breakfast and a night market in the evening.`
   → `find_local_food` for 牛肉湯 and for night markets, including which days Tainan's rotating night markets open.
4. `I'm taking the train from Taipei to Tainan on Oct 8, 2026. Will the holiday make travel busy?`
   → `crowd_risk_check` highlights the day before the break as the stronger network-wide travel signal; `hsr_trip_planner` lists trains. The risk is not a live seat count.

5. `Recommend me mountain trails in Taipei.` then `Which one is best for sunset? Are there any temples near it?`
   → `find_attractions` for trails, then again with a district for nearby temples. Only the places named in the answer are pinned.
6. `I'm going to Hualien this Saturday. Any typhoon or rain I should worry about?`
   → `typhoon_backup_plan` checks the CWA forecast and typhoon warnings; on a bad day it adds indoor backups on the map.

Follow-up to test memory: after query 2, ask `Is the second one you listed registered? Double check it.`

## Tools

| Tool | What it does | Data source |
|---|---|---|
| `legal_stay_check` ⭐ | Checks if a hotel/B&B is registered, or lists registered stays (Taiwan Host certified first, optional price cap) | Tourism Administration lodging register via [TDX](https://tdx.transportdata.tw/) |
| `find_local_food` | Restaurants by dish (English keywords are translated to Chinese) and night markets | Tourism Administration [daily open data](https://data.gov.tw/dataset/7779) (TDX as fallback), plus local night-market schedules |
| `twd_exchange` | Converts to/from TWD and compares with the 30-day average | [fawazahmed0/exchange-api](https://github.com/fawazahmed0/exchange-api) daily rates |
| `hsr_trip_planner` | Up to three THSR or TRA trains with published adult one-way fares (no live seat availability) | TDX rail timetables and fares |
| `crowd_risk_check` ⭐ | Official days off and travel-pressure estimates for trips up to 30 days | [Government office calendar](https://data.gov.tw/dataset/14718) and [historical TRA station entries](https://data.gov.tw/dataset/8792) |
| `find_attractions` | Sights by city, keyword (English translated to Chinese), and district. Returns candidates plus every other listing by district so the model can pick famous places; the map pins only the places the answer names. Name lookups tolerate different wording (士林夜市 finds 士林觀光夜市) and report closed places | Tourism Administration [daily open data](https://data.gov.tw/dataset/7777) (TDX as fallback) |
| `typhoon_backup_plan` ⭐ | Forecast (weather, rain chance, temperatures) for a date within about a week plus active typhoon warnings; on a typhoon warning or 70%+ rain, up to 3 indoor backups. Further dates get a seasonal note | [CWA open data](https://opendata.cwa.gov.tw/) (`F-D0047-091`, `W-C0034-001`), backups via TDX |

⭐ = original tool. Every tool returns `{"error", "hint"}` on failure so the model knows what to do next.

Attractions and restaurants come from the Tourism Administration's daily open-data files, downloaded in the background at startup and refreshed daily ([tools/tourism_data.py](tools/tourism_data.py)). They hold every listing, with no TDX quota and no 500-row cap per query; until they load, or if the download fails, the tools query TDX. Hotels stay on TDX: their file is too large for a 512 MiB instance. Data is used under the [Open Government Data License, version 1.0](https://data.gov.tw/license).

## Guardrails

The agent runs on the [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/guardrails/) with Gemini through its LiteLLM adapter (beta). Guardrails use the SDK's interfaces, in [guardrails.py](guardrails.py):

| Layer | Check | When it fails |
|---|---|---|
| Input (blocking, before the model) | Message over 2,000 characters; a Gemini classifier (safety filter off, so it can read what it labels) flags prompt injection, harmful requests, or requests outside Taiwan travel | Tripwire: fixed reply, no main model or TDX call, message kept out of history. A classifier error allows the message |
| Tool input | Any string argument over 200 characters | Rejected: the model gets an error and hint instead of a tool run |
| Tool output | Result text that looks like instructions (e.g. "ignore previous instructions") | Rejected: the model gets an error and hint; the result is hidden from the trip board |
| Output | Answer repeats a system-prompt sentence, or names a Chinese lodging (in parentheses) that no tool result or user message contains | Tripwire: fixed reply |
| Output cleanup (after the run) | Links to a host that is not official (`.gov.tw`, `taiwan.net.tw`, `thsrc.com.tw`, `transportdata.tw`) and not in a tool result or user message | The link is removed (Markdown links keep their text) and the rest of the answer is shown; history keeps the cleaned answer |
| Model | Gemini safety settings block medium-or-higher harassment, hate, sexual, and dangerous content | Fixed reply |
| Agent | At most 8 model turns; model errors are logged, not shown | Fixed reply |

Each tool still validates its own arguments. Sessions keep the last 20 user turns, with at most 200 sessions in memory. SDK tracing is off, so chats are not sent to OpenAI. Not covered: rate limiting, PII, lodging names written only in English (the register lists Chinese names only).

## Run locally

1. A GCP project with billing and the Vertex AI API enabled, then `gcloud auth application-default login`.
2. A free [TDX](https://tdx.transportdata.tw/) account and a free [CWA open data](https://opendata.cwa.gov.tw/) API key (授權碼). Copy `.env.example` to `.env` and fill in the TDX client ID and secret and `CWA_API_KEY`.
3. `uv run app.py`, then open http://localhost:8000
4. Tests (network mocked): `uv run pytest`

## Crowd-risk evidence

`crowd_risk_check` compares day types with 2026 TRA station-entry counts through September 1. For each historical date, the [calibration script](scripts/calibrate_crowd_risk.py) divides total entries by the median on ordinary days of the same weekday within 56 days. A pattern needs at least five sampled days and a median ratio of 1.2 or higher for a high rating. The pre-break days meet that threshold; first and last days of long breaks do not. The [compact calibration](tools/data/crowd_calibration.json) is bundled, so normal lookups only download the annual calendar. Run `uv run python scripts/calibrate_crowd_risk.py` to refresh the calibration from the official files. Station entries are a network-wide proxy, not train occupancy, HSR demand, or a route-specific forecast.

## Deploy

Cloud Run with continuous deploy from GitHub. Set `TDX_CLIENT_ID`, `TDX_CLIENT_SECRET`, and
`CWA_API_KEY` as environment variables on the service. Keep max instances at 1: sessions are stored in memory.

## Layout

```
app.py              routes, session store, Agents SDK agent and tools, sight pins
guardrails.py       input, output, and tool guardrails
prompts/system.txt  system prompt (with {today} filled in on each turn)
tools/__init__.py   tool registry (TOOLS + run_tool)
tools/tdx_client.py TDX token, caching, rate-limit handling, city names
tools/tourism_data.py daily open-data files for attractions and restaurants, searched in memory
tools/lodging.py    legal_stay_check
tools/food.py       find_local_food
tools/exchange.py   twd_exchange
tools/transport.py  hsr_trip_planner (THSR and TRA)
tools/holidays.py   crowd_risk_check (official calendar, estimated travel pressure)
tools/attractions.py find_attractions (and pins for the places an answer recommends)
tools/weather.py    typhoon_backup_plan (CWA forecast and typhoon warnings)
static/             frontend (chat, tool cards, Leaflet map, trip board with train options)
tests/              tool, harness, and guardrail tests
```
