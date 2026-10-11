"""The chamber: runs one live game and streams every message to the browser (SSE).

Pacing: council messages (each turn, news, runoff steps, escalation) go out
one at a time, and after each one the server sleeps `live.pace_s` seconds
(default 1 s, POST /api/pace; 0 fast-forwards). After each year's result the
server waits for the narrator to finish reading the year's summary, plus a
second, before the next year starts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from sumer import voice
from sumer.advisors import ADVISORS, REPORT_KINDS, scout_report
from sumer.chamber import DEFAULT_DB, play_council
from sumer.config import RESULTS_DIR, ROOT, settings
from sumer.game import YEARS, roll_game
from sumer.protocol import Ballot, Brief, Delivery, uid
from sumer.storage import Store

log = logging.getLogger(__name__)
store = Store(DEFAULT_DB)

@asynccontextmanager
async def lifespan(app):
    yield
    if live.task and not live.task.done():
        live.task.cancel()
        try:
            await live.task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Council of Sumer", lifespan=lifespan)
WEB = ROOT / "web"


class Hub:
    def __init__(self):
        self.subscribers: set[tuple[str, asyncio.Queue]] = set()

    async def emit(self, kind: str, data: dict):
        event = {"kind": kind, "run_id": data["_run_id"], "sequence": data["_sequence"],
                 "data": {k: v for k, v in data.items() if not k.startswith("_")}}
        for rid, q in list(self.subscribers):
            if rid == event["run_id"]:
                q.put_nowait(event)


hub = Hub()


class Live:
    """The single game this server is running."""

    def __init__(self):
        self.run_id = ""
        self.options: set[str] = set()
        self.task: asyncio.Task | None = None
        self.seed = 0
        self.year = 1
        self.pace_s = settings.pace_s
        self.injected: list[dict] = []
        self.pending: asyncio.Future | None = None
        self.council = None
        self.game_no = 0
        self.narration: dict[int, bytes] = {}


live = Live()


# Council messages a person reads or hears: each is sent alone, then the
# server pauses, so the browser receives them one by one, never in a burst.
PACED = {"turn", "news", "runoff", "runoff_result", "escalate"}


async def paced_emit(kind: str, data: dict) -> None:
    await hub.emit(kind, data)
    if live.pace_s <= 0:
        return  # fast-forward
    if kind in PACED:
        await asyncio.sleep(live.pace_s)
    elif kind == "year":
        # Let the narrator finish reading the year before the next debate starts.
        text = data.get("summary") or ""
        await asyncio.sleep(voice.seconds(live.narration.get(data["year"]), text) + 1.0)


async def _narrate(year: int, text: str) -> str | None:
    audio = await voice.narrate(text)
    if audio is None:
        live.narration.pop(year, None)
        return None
    live.narration[year] = audio
    return f"/api/narration/{year}?game={live.game_no}"


async def _ask(brief: Brief) -> Ballot | None:
    """Send one turn's brief to its speaker, over SLIM or in process."""
    live.year = brief.year
    if live.council is None:
        if settings.transport == "slim":
            from sumer.transport.slim import SlimCouncil

            live.council = SlimCouncil()
            await live.council.connect()
        else:
            from sumer.transport import inproc

            live.council = inproc
    t0 = time.monotonic()
    ballot = await live.council.ask(brief)
    wire = dict(getattr(live.council, "last", {}) or {})
    wire.setdefault("transport", "in-process (no network)")
    wire.setdefault("recipients", [brief.speaker])
    wire.setdefault("elapsed_s", round(time.monotonic() - t0, 2))
    wire.setdefault("replies", int((ballot.reply if isinstance(ballot, Delivery) else ballot) is not None))
    await journal_emit("wire", {"year": brief.year, "round": brief.round, "phase": brief.phase,
                               "advisor": brief.speaker, "turn": brief.turn, **wire})
    return ballot


async def journal_emit(kind, data):
    record = store.append(live.run_id, kind, data)
    await hub.emit(kind, {**data, "_sequence": record["sequence"], "_run_id": live.run_id})


async def _ask_human(options: list[dict], reason: str) -> str | None:
    live.options = {o["id"] for o in options}
    live.pending = asyncio.get_running_loop().create_future()
    timeout = settings.escalation_timeout_s if settings.escalation_timeout_s > 0 else None
    try:
        return await asyncio.wait_for(live.pending, timeout)
    except TimeoutError:
        await journal_emit("escalation_timeout", {"message": "Human selection timed out; run remains paused."})
        return None
    finally:
        live.pending = None
        live.options.clear()


async def _run(seed: int, reveal: bool, max_rounds: int) -> None:
    try:
        await play_council(
            seed,
            _ask,
            reveal_news=reveal,
            max_rounds=max_rounds,
            ask_human=_ask_human,
            emit=paced_emit,
            injected_news=live.injected,
            narrate=_narrate,
            store=store, run_id=live.run_id,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:  # surface transport or model failures in the UI
        log.exception("game failed")
        await journal_emit("error", {"message": f"{type(e).__name__}: {e}"})


class StartReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int = 7
    reveal_news: bool = True
    max_rounds: int | None = Field(default=None, ge=1, le=20)


@app.post("/api/game")
async def start(req: StartReq):
    if live.task and not live.task.done():
        raise HTTPException(409, "A live run is already active.")
    live.run_id = uid()
    live.injected.clear()
    live.seed, live.year = req.seed, 1
    live.game_no += 1
    live.narration.clear()
    live.task = asyncio.create_task(
        _run(req.seed, req.reveal_news, req.max_rounds or settings.max_rounds)
    )
    await asyncio.sleep(0)
    require_run(live.run_id)
    return {"ok": True, "run_id": live.run_id}


@app.post("/api/stop")
async def stop():
    if live.task and not live.task.done():
        live.task.cancel()
        try:
            await live.task
        except asyncio.CancelledError:
            pass
    return {"ok": True}


class NewsReq(BaseModel):
    kind: str
    run_id: str


@app.post("/api/news")
async def news(req: NewsReq):
    if req.run_id != live.run_id or not live.task or live.task.done():
        raise HTTPException(409, "No matching live run.")
    if req.kind not in REPORT_KINDS:
        raise HTTPException(400, "unknown report kind")
    rolls = roll_game(live.seed)
    y = live.year
    report = scout_report(req.kind, rolls[y - 1], rolls[y] if y < len(rolls) else None)
    live.injected.append({**report, "injected": True})
    return {"ok": True, "report": report}


class ChoiceReq(BaseModel):
    run_id: str
    choice: str


@app.post("/api/escalation")
async def choose(req: ChoiceReq):
    if req.run_id != live.run_id or not live.pending or live.pending.done():
        raise HTTPException(409, "no escalation pending")
    if req.choice not in live.options:
        raise HTTPException(400, "Choose a latest valid option.")
    live.pending.set_result(req.choice)
    return {"ok": True}


class PaceReq(BaseModel):
    pace_s: float


@app.post("/api/pace")
async def pace(req: PaceReq):
    live.pace_s = max(0.0, min(10.0, req.pace_s))
    return {"ok": True, "pace_s": live.pace_s}


@app.get("/api/meta")
async def meta():
    return {
        "active_run": live.run_id if live.task and not live.task.done() else "",
        "advisors": [a.public() for a in ADVISORS.values()],
        "years": YEARS,
        "pace_s": live.pace_s,
        "transport": settings.transport,
        "llm": settings.llm_mode,
        "model": settings.llm_model if settings.llm_mode == "bayleaf" else "policy",
        "voice": voice.enabled(),
        "max_rounds": settings.max_rounds,
        "escalation_timeout_s": settings.escalation_timeout_s,
        "budget_usd": settings.llm_budget_usd,
        "report_kinds": list(REPORT_KINDS),
    }


@app.get("/api/spend")
async def spend():
    from sumer import llm

    return {"spent_usd": llm.spent_usd(), "budget_usd": settings.llm_budget_usd}


def require_run(run_id: str):
    try:
        return store.run(run_id)
    except KeyError:
        raise HTTPException(404, "Unknown run.") from None



@app.get("/api/events")
async def events(request: Request, run_id: str):
    require_run(run_id)
    after = 0
    last_id = request.headers.get("last-event-id", "")
    if last_id.startswith(run_id + ":"):
        try:
            after = int(last_id.split(":")[-1])
        except ValueError:
            pass
    q: asyncio.Queue = asyncio.Queue()
    subscription = (run_id, q)
    hub.subscribers.add(subscription)
    backlog = store.events(run_id, after=after)

    async def stream():
        cursor = after
        def frame(event):
            return f"id: {run_id}:{event['sequence']}\ndata: {json.dumps(event)}\n\n"
        try:
            for event in backlog:
                cursor = event["sequence"]
                yield frame(event)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), 15)
                    if event["sequence"] > cursor:
                        cursor = event["sequence"]
                        yield frame(event)
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            hub.subscribers.discard(subscription)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/bench")
async def bench():
    p = RESULTS_DIR / "bench.json"
    if not p.exists():
        return JSONResponse({"missing": True})
    data = json.loads(p.read_text())
    if not data.get("rule_version"):
        return JSONResponse({"historical": True, "message": "Historical benchmark; regenerate for current game rules."})
    return JSONResponse(data)


@app.get("/api/voice/{advisor}")
async def speak(advisor: str, text: str):
    if advisor not in ADVISORS:
        raise HTTPException(404)
    audio = await voice.speak(advisor, text)
    if audio is None:
        raise HTTPException(404, "voice unavailable")
    return Response(audio, media_type="audio/mpeg")


@app.get("/api/narration/{year}")
async def narration(year: int):
    audio = live.narration.get(year)
    if audio is None:
        raise HTTPException(404, "no narration for that year")
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/{name}")
async def static(name: str):
    p = (WEB / name).resolve()
    if p.parent != WEB.resolve() or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)

