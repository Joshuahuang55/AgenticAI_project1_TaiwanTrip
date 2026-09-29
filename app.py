import datetime as dt
import json
import os
import uuid
from pathlib import Path

import litellm
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = Path(__file__).parent


def load_dotenv(path: Path = HERE / ".env") -> None:
    """Local dev only: read KEY=VALUE lines from .env. On Cloud Run, env vars are set on the service."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


load_dotenv()

from tools import TOOLS, run_tool  # noqa: E402  (tools read credentials from the environment)

# --- Config ---

SYSTEM_PROMPT = """You are "Taiwan Like a Local", a warm local friend helping foreign travelers plan trips in Taiwan.
Today is {today}. Answer in the user's language (default English), concise and practical.

Rules:
- Lodging: always use legal_stay_check. Never recommend a hotel or B&B the tool did not return.
  Registration means the place is licensed and bound by the rules (e.g. required liability insurance);
  do not claim more than the tool returned. If a place is not registered, explain why that matters
  (no official oversight or insurance guarantee) and offer registered alternatives.
- Money: every conversion or budget number must come from twd_exchange. Do not do the math from memory.
  If the user gives a budget in their currency and wants cheap stays, convert first, then pass max_price_twd.
- Food: use find_local_food. Translate Chinese names and descriptions, but keep the Chinese name in
  parentheses so the traveler can show it to locals.
- Rail: use hsr_trip_planner for train schedules, journey times, and fares. Ask for a travel date
  when missing. For each train option, state its train number, train type, departure, arrival,
  and fare (or say when the fare is unavailable). Published timetables and fares do not confirm
  live delays or available seats.
- Crowds: for dated Taiwan itineraries or holiday travel questions, use crowd_risk_check on the
  relevant dates (at most 30 days). Explain the holiday and why a day may be busy. Its risk
  levels are calendar-based estimates, not measured crowds or ticket availability.
- If the city or dates are missing and matter, ask instead of guessing.
- If a tool returns "error", follow its "hint". Never pretend you found data.
- End with a short source line naming only sources used, such as TDX or the government office calendar.
"""
MAX_TOOL_ROUNDS = 8

# --- The Harness ---


def run_agent(messages: list[dict]) -> tuple[str, list[dict]]:
    """Complete until the model answers without asking for a tool.

    Returns the final text and a record of every tool call made along the way.
    """
    tool_calls = []

    for _ in range(MAX_TOOL_ROUNDS):
        reply = litellm.completion(
            model="vertex_ai/gemini-3.5-flash-lite",
            vertex_location="global",
            messages=messages,
            tools=TOOLS,
        ).choices[0].message

        # Append assistant's reply (text, tool calls, or both) to the context.
        # model_dump() keeps it a plain dict: the raw object carries provider-specific
        # fields that trip Pydantic when LiteLLM re-serializes it next round.
        messages += [reply.model_dump()]

        if not reply.tool_calls:
            return reply.content, tool_calls

        # The harness, not the model, runs each tool and appends the result
        for call in reply.tool_calls:
            args = json.loads(call.function.arguments)
            result = run_tool(call.function.name, args)
            tool_calls += [{"name": call.function.name, "args": args, "result": result}]

            messages += [{"role": "tool", "tool_call_id": call.id, "content": result}]

    return "Sorry, I hit my tool-call limit before finishing.", tool_calls


# --- Session Store ---

# session_id -> list of messages. In-memory, single process.
sessions: dict[str, list] = {}

# --- FastAPI App ---

app = FastAPI()
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[dict]


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    # Get or create the session
    session_id = request.session_id or str(uuid.uuid4())
    if session_id not in sessions:
        today = dt.date.today().strftime("%A, %Y-%m-%d")
        sessions[session_id] = [{"role": "system", "content": SYSTEM_PROMPT.format(today=today)}]

    # Append user's message to the context
    sessions[session_id] += [{"role": "user", "content": request.message}]

    try:
        response, tool_calls = run_agent(sessions[session_id])
    except Exception as e:
        # Auth, billing, a model that is not running: show it in the chat, not as a 500.
        response, tool_calls = f"Model call failed: {type(e).__name__}: {str(e)[:300]}", []

    return ChatResponse(response=response, session_id=session_id, tool_calls=tool_calls)


@app.post("/clear")
def clear(session_id: str | None = None):
    sessions.pop(session_id, None)
    return {"status": "ok"}


if __name__ == "__main__":
    # Cloud Run injects PORT and requires listening on all interfaces.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
