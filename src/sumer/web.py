"""The chamber: runs one live game and streams every message to the browser (SSE)."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from sumer import voice
from sumer.advisors import ADVISORS, REPORT_KINDS, scout_report
from sumer.chamber import play_council
from sumer.config import RESULTS_DIR, ROOT, settings
from sumer.game import roll_game
from sumer.protocol import Brief

log = logging.getLogger(__name__)
app = FastAPI(title="Council of Sumer")
WEB = ROOT / "web"


class Hub:
    """Fan-out of game events to every open browser tab, with replay."""

    def __init__(self):
        self.backlog: list[dict] = []
        self.subscribers: set[asyncio.Queue] = set()

    async def emit(self, kind: str, data: dict) -> None:
        ev = {"kind": kind, "data": data}
        self.backlog.append(ev)
        for q in list(self.subscribers):
            q.put_nowait(ev)

    def reset(self) -> None:
        self.backlog.clear()


hub = Hub()


class Live:
    """The single game this server is running."""

    def __init__(self):
        self.task: asyncio.Task | None = None
        self.seed = 0
        self.year = 1
        self.pace_s = 1.2
        self.injected: list[dict] = []
        self.pending: asyncio.Future | None = None
        self.council = None


live = Live()


async def _gather(brief: Brief):
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
    ballots = await live.council.gather(brief)
    wire = dict(getattr(live.council, "last", {}) or {})
    wire.setdefault("transport", "in-process (no network)")
    wire.setdefault("elapsed_s", round(time.monotonic() - t0, 2))
    wire.setdefault("replies", len(ballots))
    await hub.emit("wire", {"year": brief.year, "round": brief.round, **wire})
    await asyncio.sleep(live.pace_s)
    return ballots


async def _ask_human(options: list[dict], reason: str) -> str | None:
    live.pending = asyncio.get_running_loop().create_future()
    timeout = settings.escalation_timeout_s if settings.escalation_timeout_s > 0 else None
    try:
        return await asyncio.wait_for(live.pending, timeout)
    except TimeoutError:
        await hub.emit("escalation_timeout", {"fallback": "safe"})
        return None
    finally:
        live.pending = None


async def _run(seed: int, reveal: bool, max_rounds: int) -> None:
    try:
        await play_council(
            seed,
            _gather,
            reveal_news=reveal,
            max_rounds=max_rounds,
            ask_human=_ask_human,
            emit=hub.emit,
            injected_news=live.injected,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:  # surface transport or model failures in the UI
        log.exception("game failed")
        await hub.emit("error", {"message": f"{type(e).__name__}: {e}"})


class StartReq(BaseModel):
    seed: int = 7
    reveal_news: bool = True
    max_rounds: int | None = None


@app.post("/api/game")
async def start(req: StartReq):
    if live.task and not live.task.done():
        live.task.cancel()
    hub.reset()
    live.injected.clear()
    live.seed, live.year = req.seed, 1
    live.task = asyncio.create_task(
        _run(req.seed, req.reveal_news, req.max_rounds or settings.max_rounds)
    )
    return {"ok": True}


@app.post("/api/stop")
async def stop():
    if live.task and not live.task.done():
        live.task.cancel()
    return {"ok": True}


class NewsReq(BaseModel):
    kind: str


@app.post("/api/news")
async def news(req: NewsReq):
    if req.kind not in REPORT_KINDS:
        raise HTTPException(400, "unknown report kind")
    rolls = roll_game(live.seed)
    y = live.year
    report = scout_report(req.kind, rolls[y - 1], rolls[y] if y < len(rolls) else None)
    live.injected.append({**report, "injected": True})
    return {"ok": True, "report": report}


class ChoiceReq(BaseModel):
    choice: str


@app.post("/api/escalation")
async def choose(req: ChoiceReq):
    if not live.pending or live.pending.done():
        raise HTTPException(409, "no escalation pending")
    live.pending.set_result(req.choice)
    return {"ok": True}


class PaceReq(BaseModel):
    pace_s: float


@app.post("/api/pace")
async def pace(req: PaceReq):
    live.pace_s = max(0.0, min(10.0, req.pace_s))
    return {"ok": True}


@app.get("/api/meta")
async def meta():
    return {
        "advisors": [a.__dict__ for a in ADVISORS.values()],
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


@app.get("/api/events")
async def events():
    q: asyncio.Queue = asyncio.Queue()
    backlog = list(hub.backlog)
    hub.subscribers.add(q)

    async def stream():
        try:
            for ev in backlog:
                yield f"data: {json.dumps(ev)}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            hub.subscribers.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/bench")
async def bench():
    p = RESULTS_DIR / "bench.json"
    if not p.exists():
        return JSONResponse({"missing": True})
    return JSONResponse(json.loads(p.read_text()))


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
    p = (WEB / name).resolve()
    if p.parent != WEB.resolve() or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)

