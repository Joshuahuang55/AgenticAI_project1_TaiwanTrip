# Taiwan Like a Local

A chat agent that acts like a local friend for foreigners traveling in Taiwan. It looks things
up in official Taiwanese open data instead of guessing: whether a B&B is legally registered,
where locals eat, and how far your budget goes in TWD. Every tool call is shown in the chat,
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

Follow-up to test memory: after query 2, ask `Is the second one you listed registered? Double check it.`

## Tools

| Tool | What it does | Data source |
|---|---|---|
| `legal_stay_check` ⭐ | Checks if a hotel/B&B is registered, or lists registered stays (Taiwan Host certified first, optional price cap) | Tourism Administration lodging register via [TDX](https://tdx.transportdata.tw/) |
| `find_local_food` | Restaurants by dish (English keywords are translated to Chinese) and night markets | Tourism Administration via TDX, plus local night-market schedules |
| `twd_exchange` | Converts to/from TWD and compares with the 30-day average | [fawazahmed0/exchange-api](https://github.com/fawazahmed0/exchange-api) daily rates |
| `hsr_trip_planner` | Up to three THSR or TRA trains with published adult one-way fares (no live seat availability) | TDX rail timetables and fares |
| `crowd_risk_check` ⭐ | Official days off and travel-pressure estimates for trips up to 30 days | [Government office calendar](https://data.gov.tw/dataset/14718) and [historical TRA station entries](https://data.gov.tw/dataset/8792) |
| `get_weather` | Starter placeholder, current weather | Open-Meteo |

⭐ = original tool. Every tool returns `{"error", "hint"}` on failure so the model knows what to do next.

## Run locally

1. A GCP project with billing and the Vertex AI API enabled, then `gcloud auth application-default login`.
2. A free [TDX](https://tdx.transportdata.tw/) account. Copy `.env.example` to `.env` and fill in the client ID and secret.
3. `uv run app.py`, then open http://localhost:8000
4. Tests (network mocked): `uv run pytest`

## Crowd-risk evidence

`crowd_risk_check` compares day types with 2026 TRA station-entry counts through September 1. For each historical date, the [calibration script](scripts/calibrate_crowd_risk.py) divides total entries by the median on ordinary days of the same weekday within 56 days. A pattern needs at least five sampled days and a median ratio of 1.2 or higher for a high rating. The pre-break days meet that threshold; first and last days of long breaks do not. The [compact calibration](tools/data/crowd_calibration.json) is bundled, so normal lookups only download the annual calendar. Run `uv run python scripts/calibrate_crowd_risk.py` to refresh the calibration from the official files. Station entries are a network-wide proxy, not train occupancy, HSR demand, or a route-specific forecast.

## Deploy

Cloud Run with continuous deploy from GitHub. Set `TDX_CLIENT_ID` and `TDX_CLIENT_SECRET` as
environment variables on the service. Keep max instances at 1: sessions are stored in memory.

## Layout

```
app.py              routes, session store, agent loop
prompts/system.txt  system prompt (with {today} filled in at session start)
tools/__init__.py   tool registry (TOOLS + run_tool)
tools/tdx_client.py TDX token, caching, rate-limit handling, city names
tools/lodging.py    legal_stay_check
tools/food.py       find_local_food
tools/exchange.py   twd_exchange
tools/transport.py  hsr_trip_planner (THSR and TRA)
tools/holidays.py   crowd_risk_check (official calendar, estimated travel pressure)
static/             frontend (chat, tool cards, Leaflet map, trip board with train options)
tests/              tool tests
```
