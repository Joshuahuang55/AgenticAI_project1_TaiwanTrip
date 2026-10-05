# Taiwan Like a Local

A travel agent for **foreigners planning a trip to Taiwan**. Ask it what you would ask a friend who
lives there. Instead of guessing, it checks official Taiwanese open data: whether a B&B is legally
registered, where locals eat, what to see, which train to take, whether a holiday will make travel
busy, whether rain or a typhoon is coming, and what your budget is worth in TWD.

**Live app:** https://agenticai-project1-taiwantrip-git-353565629353.europe-west1.run.app
**Team:** ch4000, nw2608, lc4021

## Tools

| Tool | What it does |
|---|---|
| `legal_stay_check` ⭐ | Checks whether a hotel or B&B is on the official lodging register, or recommends registered stays by area, type and price. |
| `crowd_risk_check` ⭐ | Shows official holidays and rates how busy travel will be on each date, calibrated on 2026 railway ridership. |
| `typhoon_backup_plan` ⭐ | Checks the forecast and typhoon warnings for a city and date, and says whether to plan outdoors or indoors. |
| `find_local_food` | Finds restaurants for a dish or cuisine, and night markets, ranked by awards and local favorites. |
| `find_attractions` | Recommends sights by interests and indoor/outdoor preference, and fits them into a time budget. |
| `hsr_trip_planner` | Lists High Speed Rail or Taiwan Railway trains with times and fares for a date. |
| `twd_exchange` | Converts between TWD and other currencies and compares today's rate with recent weeks. |

⭐ Original tools, one per team member. All tools call external data (TDX transport and tourism APIs,
the Central Weather Administration, government open data, an exchange-rate API) and return an
`error` with a `hint` when a lookup fails, so the agent can recover or explain.

## How to use

Type a question in the box at the bottom, in English or Chinese. Under each answer you'll see the
lookups the agent made (click **details** for the raw request and response). Places, trains, dates and
weather collect in the **Trip notes** panel and on the map. The agent remembers the conversation until
you press **New trip**, and ends each answer with a suggestion for what to ask next.

Lookups are shared and capped at **5 per minute**; the meter in the header shows how many are left.

**Sample queries** (also the three buttons on the start screen; ask them in order in one chat)

1. `Find registered B&Bs in Hualien under 3,000 TWD a night.`
   → `legal_stay_check` ranks registered B&Bs by reported starting rate under the cap, with licence
   numbers and districts. They appear in **Trip notes → Stays**.
2. `I have $1,500 USD for a week. How much is that in TWD?`
   → `twd_exchange` converts at today's mid-market rate and compares it with the
   last four weeks. The answer remembers the 3,000 TWD B&B budget from query 1 and works out a daily budget.
3. `What should I eat in Tainan? I want beef soup for breakfast and a night market in the evening.`
   → `find_local_food` runs twice: beef soup spots (Michelin and 500 Bowls picks) and night markets,
   including the evenings each rotating market opens. Both lists appear in **Trip notes → Food**.

Each answer ends with a **Next:** suggestion you can type as a follow-up, such as `What are some good food spots in Hualien City?`

## What we built beyond the starter

| | What we added |
|---|---|
| **Our own data** | Popularity rankings that the official listings lack, built by our scripts in [`scripts/`](scripts/): Wikipedia-based fame for sights, 48,401 restaurants from OpenStreetMap, Michelin and 500盤/500碗 award lists, 226 local favorites labelled by a model and reviewed by hand ([`data/local_review.csv`](data/local_review.csv)), and a crowd calibration from official railway ridership. |
| **Guardrails** | Four layers on the OpenAI Agents SDK: input (off-topic, prompt injection, harmful requests), tool input (oversized arguments), tool output (injected instructions in data), and output (prompt leaks, stays no lookup returned, untrusted links). See [`guardrails.py`](guardrails.py). |
| **Memory** | Beyond chat history, each session keeps the trip's city, dates, budget, diet and interests, each backed by a quote from the user, and the places already suggested, so follow-ups like "the second one" work. |
| **Grounded answers** | Replies are structured: every recommended place and source must match a real lookup in the session, and only those places are pinned on the map. |
| **Fair use of APIs** | A shared budget of 5 tool calls per minute, enforced on the server and shown as a live meter in the UI. |
| **English-first UI** | A timetable-style design built from scratch. Holidays and weather are translated, Chinese-only listing names are romanized, and the map only ever shows English labels. |
| **Tested** | 530+ automated tests with mocked APIs and a scripted model, run on every push by GitHub Actions. |

## Assignment checklist

| Requirement | Where |
|---|---|
| Remembers the conversation; sessions kept separate | Per-session history and trip context, keyed by `session_id` |
| At least three tools, at least one with external data | Seven tools, all using external data |
| One original tool per team member | `legal_stay_check`, `crowd_risk_check`, `typhoon_backup_plan` |
| Well-described tools with actionable errors | Typed tool definitions in [`tools/agent_tools.py`](tools/agent_tools.py); `error` + `hint` on failure |
| Shows tool calls; `/chat` keeps `response`, `session_id`, `tool_calls` | Lookup list under each answer |
| Frontend changed from the starter | [`static/`](static/) |
| Deployed on Cloud Run with continuous deploy from GitHub | Live app above; [`submission.json`](submission.json) |

## Run locally

```bash
uv sync --locked --group dev
cp .env.example .env          # add your TDX client ID/secret and CWA API key
gcloud auth application-default login
uv run app.py                 # http://localhost:8000
uv run pytest -q              # tests, no network needed
```

Gemini runs on Vertex AI, so you need a GCP project with the Vertex AI API enabled.

## More

[`docs/DESIGN.md`](docs/DESIGN.md) has the full details: tool rules, agent design, guardrail table,
data sources and licences, the lookup budget, and the project layout.

The Michelin and 500盤/500碗 data are used for research and education only, as their sources require.
