"""One live council game with verified, recorded event streams."""
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
from sumer.advisors import ADVISORS
from sumer.chamber import DEFAULT_DB, play_council
from sumer.config import RESULTS_DIR, ROOT, settings
from sumer.protocol import Brief, uid
from sumer.storage import Store

log = logging.getLogger(__name__)
WEB = ROOT / "web"
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


class Hub:
    def __init__(self):
        self.subscribers: set[tuple[str, asyncio.Queue]] = set()

    async def publish(self, kind: str, data: dict):
        event = {"kind": kind, "run_id": data["_run_id"], "sequence": data["_sequence"],
                 "data": {k: v for k, v in data.items() if not k.startswith("_")}}
        for rid, q in list(self.subscribers):
            if rid == event["run_id"]:
                q.put_nowait(event)


hub = Hub()


class Live:
    def __init__(self):
        self.task: asyncio.Task | None = None
        self.run_id = ""
        self.pace_s = 1.2
        self.pending: asyncio.Future | None = None
        self.options: set[str] = set()
        self.council = None


live = Live()


async def _gather(brief: Brief):
    if live.council is None:
        if settings.transport == "slim":
            from sumer.transport.slim import SlimCouncil
            live.council = SlimCouncil()
            await live.council.connect()
        else:
            from sumer.transport import inproc
            live.council = inproc
    t0 = time.monotonic()
    deliveries = await live.council.gather(brief)
    wire = dict(getattr(live.council, "last", {}) or {})
    wire.setdefault("transport", "in-process (no network)")
    wire.setdefault("elapsed_s", round(time.monotonic() - t0, 2))
    wire.setdefault("replies", sum(d.reply is not None for d in deliveries))
    data = {"year": brief.year, "round": brief.round, "phase": brief.phase, **wire}
    event = store.append(brief.run_id, "wire", data)
    await hub.publish("wire", {**data, "_sequence": event["sequence"], "_run_id": brief.run_id})
    await asyncio.sleep(live.pace_s)
    return deliveries


async def _ask_human(options: list[dict], reason: str) -> str | None:
    live.options = {o["id"] for o in options}
    live.pending = asyncio.get_running_loop().create_future()
    try:
        # Deadlock stays paused until an explicit selection or stop, with no timeout adoption.
        return await live.pending
    finally:
        live.pending = None
        live.options.clear()


class StartReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int = 7
    max_rounds: int | None = Field(default=None, ge=1, le=20)


@app.post("/api/game")
async def start(req: StartReq):
    if live.task and not live.task.done():
        raise HTTPException(409, "Stop the current run before convening another council.")
    live.run_id = uid()
    live.task = asyncio.create_task(play_council(
        req.seed, _gather, max_rounds=req.max_rounds, ask_human=_ask_human,
        emit=hub.publish, store=store, run_id=live.run_id))
    await asyncio.sleep(0)
    if live.task.done() and live.task.exception():
        raise HTTPException(500, str(live.task.exception()))
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


class ChoiceReq(BaseModel):
    run_id: str
    choice: str


@app.post("/api/escalation")
async def choose(req: ChoiceReq):
    if req.run_id != live.run_id or not live.pending or live.pending.done():
        raise HTTPException(409, "No live human selection is pending for this run.")
    if req.choice not in live.options:
        raise HTTPException(400, "Choose one of the latest valid advisor proposals.")
    live.pending.set_result(req.choice)
    return {"ok": True}


class PaceReq(BaseModel):
    pace_s: float = Field(ge=0, le=10)


@app.post("/api/pace")
async def pace(req: PaceReq):
    live.pace_s = req.pace_s
    return {"ok": True}


@app.get("/api/meta")
async def meta():
    return {
        "advisors": [a.__dict__ for a in ADVISORS.values()],
        "transport": settings.transport, "llm": settings.llm_mode,
        "model": settings.llm_model if settings.llm_mode == "bayleaf" else "policy",
        "voice": voice.enabled(), "max_rounds": settings.max_rounds,
        "budget_usd": settings.llm_budget_usd,
        "active_run": live.run_id if live.task and not live.task.done() else None,
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
    path = RESULTS_DIR / "bench.json"
    if not path.exists():
        return JSONResponse({"missing": True})
    data = json.loads(path.read_text())
    if not data.get("rule_version"):
        return JSONResponse({"historical": True, "message":
            "The checked-in benchmark predates the revised game and voting rules. "
            "Run the existing benchmark command to generate current offline results."})
    return JSONResponse(data)


@app.get("/api/voice/{advisor}")
async def speak(advisor: str, text: str):
    if advisor not in ADVISORS:
        raise HTTPException(404)
    audio = await voice.speak(advisor, text)
    if audio is None:
        raise HTTPException(404, "voice unavailable")
    return Response(audio, media_type="audio/mpeg")


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/{name}")
async def static(name: str):
    path = (WEB / name).resolve()
    if path.parent != WEB.resolve() or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)
