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

## Conversation behavior

Broad recommendation requests get a small initial selection from tool results, with areas and
brief reasons, followed by at most one optional question to refine the next answer. The agent
reuses details from the same conversation and respects corrections or requests to skip questions.
It asks first when required lookup information is missing, such as the date for train schedules.
District filtering narrows an area; it does not confirm walking distance or travel time.

### Trip context within a session

Each session saves the user's city, area, travel dates, departure point, budget, interests,
dietary needs, and follow-up question preference separately from the last 20 user turns.
`trip_context.py` extracts changes using a typed SDK output and `prompts/trip_context.txt`;
each saved value includes an exact supporting quote from the current user message. Missing
details, explicit "no preference", and withdrawn details are distinct. Latest corrections
replace old values; changing city clears the old area, while unrelated preferences remain.
Budget values retain the stated currency and scope; extraction does not convert prices.

The blocking input guardrail screens the message before extraction, then the main agent's
dynamic instructions include the updated context. This adds one model call per accepted
message, with a 10-second extraction timeout. Invalid output or extraction failure retains
the previous context and lets the conversation continue. Failed/rejected main runs roll back
preference changes. Evidence checks validate provenance and format; semantic extraction still
depends on the model. Requests within one session run in order to avoid overlapping updates.

Context is isolated by `session_id`, survives history trimming, and is removed by **New trip**,
session eviction, or server restart. It is not a permanent user profile.

After changing either file in `prompts/`, restart the app and check these conversations:

- `Give me some food recommendations in Taipei.` → recommendations first, then an optional refinement.
  Follow with `Around Ximen, and vegetarian.` → uses Taipei and the new preferences; does not ask for the city again.
- In a new trip, `Recommend sights in Taipei. Just give me three options, no questions.`
  → three tool-backed suggestions without a refinement question.
- In a new trip, `Find a train from Taipei to Tainan.` → asks for the travel date before a timetable lookup.
- After the Taipei/Ximen food conversation, `Actually, Tainan. Keep it vegetarian, no questions.`
  → uses Tainan, drops Ximen, retains vegetarian, and skips optional refinement questions.
  Click **New trip**, then ask for food without a city → asks for a city rather than reusing Tainan.

Inspect the displayed tool calls as well as the answer. Scripted-model tests cover the harness;
they do not establish whether Gemini follows this policy.

## Tools

| Tool | What it does | Data source |
|---|---|---|
| `legal_stay_check` ⭐ | Checks if a hotel/B&B is registered, or lists registered stays (Taiwan Host certified first, optional price cap) | Tourism Administration lodging register via [TDX](https://tdx.transportdata.tw/) |
| `find_local_food` | Restaurants by dish and night markets; preference evidence first when criteria are supplied, then awards/local score. Returns sourced facts, missing fields, and comparison checks; `style: local` for locals' favorites | Tourism Administration [daily open data](https://data.gov.tw/dataset/7779) (TDX as fallback), [OpenStreetMap](https://www.openstreetmap.org/copyright), award lists (see below), plus local night-market schedules |
| `twd_exchange` | Converts to/from TWD and compares with the 30-day average | [fawazahmed0/exchange-api](https://github.com/fawazahmed0/exchange-api) daily rates |
| `hsr_trip_planner` | THSR or TRA options ranked by time/fare preferences, with a recommendation and computed trade-offs; default three, up to ten | TDX rail timetables and adult one-way standard-class fares |
| `crowd_risk_check` ⭐ | Official days off and travel-pressure estimates for trips up to 30 days | [Government office calendar](https://data.gov.tw/dataset/14718) and [historical TRA station entries](https://data.gov.tw/dataset/8792) |
| `find_attractions` | Sights by city, keyword (English translated to Chinese), and district. Returns candidates plus every other listing by district so the model can pick famous places; the map pins only the places the answer names. Name lookups tolerate different wording (士林夜市 finds 士林觀光夜市) and report closed places. Ranked by fame; `style: local` for places locals like, and one or two local gems in default results | Tourism Administration [daily open data](https://data.gov.tw/dataset/7777) (TDX as fallback), plus [Wikidata](https://www.wikidata.org/) and Wikipedia pageviews for fame and missing sights |
| `typhoon_backup_plan` ⭐ | Forecast (weather, rain chance, temperatures) for a date within about a week plus active typhoon warnings; on a typhoon warning or 70%+ rain, up to 3 indoor backups. Further dates get a seasonal note | [CWA open data](https://opendata.cwa.gov.tw/) (`F-D0047-091`, `W-C0034-001`), backups via TDX |

⭐ = original tool. Every tool returns `{"error", "hint"}` on failure so the model knows what to do next.

Attractions and restaurants come from the Tourism Administration's daily open-data files, downloaded in the background at startup and refreshed daily ([tools/tourism_data.py](tools/tourism_data.py)). They hold every listing, with no TDX quota and no 500-row cap per query; until they load, or if the download fails, the tools query TDX. Hotels stay on TDX: their file is too large for a 512 MiB instance. Data is used under the [Open Government Data License, version 1.0](https://data.gov.tw/license).

### Tool output review and comparison boundaries

| Tool | Facts the agent can compare | Missing information / limits |
|---|---|---|
| Food | Dietary reports, cuisine, district, relative price band, awards, listed hours | No exact current menu prices or ingredient guarantees; some districts are estimated |
| Attractions | Categories, description, address, listed hours and admission information | Many hours/fees are missing; no measured visit duration or walking time |
| Lodging | Registration, license, address, certification, owner-reported price range | Registration is not a quality rating; no live rooms or booking prices |
| Rail | Train type/number, departure, arrival/date, duration, fare, time windows, ranked recommendation and trade-offs | Up to ten options per query within one rail service; no live seats/delays; fares can be missing |
| Crowds | Holiday pattern, estimated risk/reason, historical ratio and sample size | Preliminary TRA network estimate, not route occupancy or HSR demand |
| Weather | Forecast dates, rain chance, temperature, warning, backup listings | Limited forecast horizon; seasonal notes are not forecasts and warnings are current |
| Exchange | Rate, rate date, converted amount, sampled historical comparison | Mid-market snapshot; no actual cash-counter quote or travel prices; average uses weekly samples |

Treat missing facts as **unknown**, explicit contrary reports as **conflicts**, and failed lookups
as **unavailable**. Source claims are **reported**, not independently verified. Compare only
available facts, explain the best supported fit and alternatives, and name relevant uncertainty.
Food and rail now include explicit comparison fields; the other tools retain their existing
domain outputs. Existing `/chat` fields and map behavior are preserved.

### Food preference comparisons

Optional arguments `dietary` (`vegetarian`/`vegan`), `price_preference` (`budget`/`mid_range`/`any`),
`max_price_twd`, and `confirmed_only` accompany existing city/district/dish filters. Keep them
on `names` lookups. Set `confirmed_only: true` when the user requests only confirmed matches;
this requires reported support for every requested criterion, not live independent verification.
`budget` selects the relative `$` category and `mid_range` allows `$`/`$$`; unknown bands remain
unconfirmed. A numeric cap is explicitly per person per meal in TWD. No returned band verifies
that exact cap; lower known bands simply rank first among otherwise equal unconfirmed leads.

Each restaurant's `facts` contains `value`, `status`, and `source`, plus dietary evidence when
available. `comparison` checks each requested criterion and marks the overall fit as
`reported_match`, `needs_confirmation`, `conflict`, or `not_requested`. Conflicting options
appear in `excluded`, not `results`. With `confirmed_only`, uncertain candidates are also excluded
without their names; exact meal caps currently cannot be confirmed. `comparison_summary` counts
returned matches/leads, excluded conflicts, and excluded unconfirmed candidates. Ranking favors
dietary reports, then dietary name/cuisine indications, ahead of candidates with no dietary
evidence. It next compares other criteria, dietary variety, relative prices, and keyword/awards.
Ordinary recommendations offer promising dietary leads with brief caveats when reports or
prices are unavailable; "cheap" does not require exact meal-price confirmation. Constrained
searches do not inject lower-fit local gems just to fill the list. Searches are not exhaustive.

This is everyday travel planning: useful suggestions and reasonable estimates take priority
over exhaustive verification. Traveler-facing answers start with choices and concrete comparisons. Evidence/status labels
stay internal; relevant data gaps are combined into one short practical note after suggestions.
Missing fields alone do not trigger a refusal. Strict evidence filtering requires an explicit
request for confirmed matches. This response policy also applies to other recommendation tools.

OSM dietary tags retain their [vegetarian](https://wiki.openstreetmap.org/wiki/Key:diet:vegetarian)
and [vegan](https://wiki.openstreetmap.org/wiki/Key:diet:vegan) distinctions. Names/cuisine terms
are only indications; contradictory tags remain uncertain. The builder now preserves dietary
tags and reported districts on future intentional refreshes. Existing bundled records without
these tags remain unknown; this change does not regenerate the dataset or manufacture facts.
Merged listings preserve the source of borrowed hours/dietary information.

Test in a fresh trip: `Recommend vegetarian food around Ximen in Taipei. I prefer cheap places.`
Then `What about vegan options under TWD 300 per person per meal?`
Inspect dietary/price arguments and checks: the agent should explain its choice and alternatives,
and disclose unconfirmed dietary evidence and exact prices. Missing results must not become
invented recommendations. Automated comparison tests use competing fictional candidates;
real-model behavior needs a separate conversation check.

### Train preferences and comparisons

`hsr_trip_planner` ranks the whole matching timetable before selecting `limit` options (1–10,
default 3). `preference` accepts `earliest_arrival` (default), `fastest`, `cheapest`, or
`earliest_departure`. Default ranking favors arriving soonest, with ties favoring shorter journeys;
earliest departure applies only when requested. Cheapest compares adult standard-class fares, with ties favoring
shorter journeys; unknown fares are never treated as free. Each query compares one rail service.

`depart_after` and `depart_before` define an inclusive departure window in `HH:MM`.
`arrive_by` is an inclusive arrival deadline on the **same travel date**, not the following day.
Overnight journeys include `arrival_date`, and arrival ranking accounts for the day change.

The existing `trains` list retains train types, times, durations, and fares. `comparison` adds
the recommended train, reason, matching/returned counts, equal-fare flag, and computed time/fare
differences for alternatives. Missing fares leave schedules usable; for `cheapest` with no fares,
the tool recommends the earliest arrival instead. Station/timetable failures still return errors.
Ranking uses the same station, timetable, and fare requests as before, without per-train requests.

Try: `Find three HSR options from Taipei to Tainan on October 8, 2026. Depart between 09:00 and
12:00 and arrive by 14:00. Prefer the fastest journey. Which would you choose?`
Then: `Keep the same route and date, but show five options and prioritize the earliest arrival.`

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

## Ranking data

Official listings carry no popularity signal, so the bundled files in `tools/data/` add one. Each has a build script; none is needed at runtime.

| File | Built by | What it holds | Sources and terms |
|---|---|---|---|
| `attraction_fame.json` | [scripts/build_fame.py](scripts/build_fame.py) | Fame (Chinese Wikipedia views, article length, languages, per county), English fame (English Wikipedia views), and local fame (known in Chinese, little read in English) | Wikidata (CC0), Wikimedia pageviews |
| `extra_attractions.json` | same | Sights the register lacks (駁二, 花園夜市), not closed | Wikidata (CC0) |
| `local_favorites.json` | [scripts/label_local_favorites.py](scripts/label_local_favorites.py) | 226 places labeled as where locals go: Qwen labels, then a second review ([data/local_review.csv](data/local_review.csv)) | Model labels |
| `osm_food.json.gz` | [scripts/build_osm_food.py](scripts/build_osm_food.py) | 48,401 restaurants, cafes and stalls | © OpenStreetMap contributors, [ODbL 1.0](https://www.openstreetmap.org/copyright) |
| `food_fame.json` | [scripts/build_food_fame.py](scripts/build_food_fame.py) | Food fame (strongest award) and local score (500盤/500碗 rating, lowered by Michelin and for chains), awards per place, and Michelin restaurants OSM lacks | Michelin Guide Taiwan via [michelin-my-maps](https://github.com/ngshiheng/michelin-my-maps); 500盤 and 500碗 lists by 500輯 (udn), in `data/food_awards/` |

**Research and education use only.** This is a course project. The Michelin Guide data (michelin-my-maps states its data is for research use only) and the 500盤/500碗 lists (© 500輯) are used for research and education, not commercially, and are not redistributed for other use. Remove `food_fame.json` and `data/food_awards/` before any commercial use; the food tool then ranks by OpenStreetMap order.

## Crowd-risk evidence

`crowd_risk_check` compares day types with 2026 TRA station-entry counts through September 1. For each historical date, the [calibration script](scripts/calibrate_crowd_risk.py) divides total entries by the median on ordinary days of the same weekday within 56 days. A pattern needs at least five sampled days and a median ratio of 1.2 or higher for a high rating. The pre-break days meet that threshold; first and last days of long breaks do not. The [compact calibration](tools/data/crowd_calibration.json) is bundled, so normal lookups only download the annual calendar. Run `uv run python scripts/calibrate_crowd_risk.py` to refresh the calibration from the official files. Station entries are a network-wide proxy, not train occupancy, HSR demand, or a route-specific forecast.

## Deploy

Cloud Run with continuous deploy from GitHub. Set `TDX_CLIENT_ID`, `TDX_CLIENT_SECRET`, and
`CWA_API_KEY` as environment variables on the service. Keep max instances at 1: sessions are stored in memory.

## Layout

```
app.py              routes, session store, Agents SDK agent and tools, sight pins
trip_context.py     validated user preference updates, separate from bounded chat history
guardrails.py       input, output, and tool guardrails
prompts/system.txt  system prompt (with {today} filled in on each turn)
prompts/trip_context.txt  rules for extracting user-stated trip preferences
tools/__init__.py   tool registry (TOOLS + run_tool)
tools/tdx_client.py TDX token, caching, rate-limit handling, city names
tools/tourism_data.py daily open-data files for attractions and restaurants, searched in memory
tools/lodging.py    legal_stay_check
tools/food.py       find_local_food
tools/food_preferences.py  sourced food facts and deterministic preference comparisons
tools/exchange.py   twd_exchange
tools/transport.py  hsr_trip_planner (THSR and TRA)
tools/holidays.py   crowd_risk_check (official calendar, estimated travel pressure)
tools/attractions.py find_attractions (and pins for the places an answer recommends)
tools/weather.py    typhoon_backup_plan (CWA forecast and typhoon warnings)
scripts/            builds for the bundled data (fame, extra sights, local favorites, OSM food, food awards)
data/               source lists for builds (food awards, local-favorite review)
static/             frontend (chat, tool cards, Leaflet map, trip board with train options)
tests/              tool, harness, and guardrail tests
```
