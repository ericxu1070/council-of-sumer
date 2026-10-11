"""The chamber: runs one live game and streams every message to the browser (SSE).

Pacing: council messages (each turn, news, runoff steps, escalation) go out
one at a time, exactly `live.pace_s` seconds apart (default 0.8 s, PACE_S or
POST /api/pace; 0 fast-forwards). After each year's result the
server waits for the narrator to finish reading the year's summary, plus a
second, before the next year starts.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import time
import zipfile
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sumer import voice
from sumer.advisors import (
    ADVISORS,
    MAX_ADVISORS,
    CouncilSeat,
    resolve_council,
)
from sumer.chamber import DEFAULT_DB, play_council
from sumer.config import CACHE_DIR, RESULTS_DIR, ROOT, settings
from sumer.game import YEARS
from sumer.protocol import Ballot, Brief, Delivery, uid
from sumer.scouts import SCOUT_IDS, SCOUT_OPTIONS
from sumer.storage import Store

log = logging.getLogger(__name__)
store = Store(DEFAULT_DB)

@asynccontextmanager
async def lifespan(app):
    # Interrupted turns cannot be resumed without fresh turn responses.
    for run in store.runs():
        if run["status"] in ("running", "deadlock"):
            store.append(run["id"], "technical_failure", {"message": "Server restarted before the live run finished."})
            store.status(run["id"], "technical_failure")
    yield
    if live.task and not live.task.done():
        live.resume.set()
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
        self.paused = False
        self.resume = asyncio.Event()
        self.resume.set()
        self.scout_pending: asyncio.Future | None = None
        self.scout_request: dict | None = None
        self.pending: asyncio.Future | None = None
        self.council = None
        self.game_no = 0
        self.narration: dict[int, bytes] = {}
        self.next_at = 0.0  # loop time when the next paced message may go out


live = Live()


# Council messages a person reads or hears: each is sent alone, exactly one
# pace apart, so the browser receives them one by one, never in a burst. The
# next speaker thinks while the clock runs, so the wire time does not add up.
PACED = {"turn", "news", "runoff", "runoff_result", "escalate"}


async def paced_emit(kind: str, data: dict) -> None:
    await live.resume.wait()
    loop = asyncio.get_running_loop()
    if kind in PACED and live.pace_s > 0:
        await asyncio.sleep(max(0.0, live.next_at - loop.time()))
    await live.resume.wait()
    await hub.emit(kind, data)
    if live.pace_s <= 0:
        return  # fast-forward
    if kind in PACED:
        live.next_at = loop.time() + live.pace_s
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


async def _ask(brief: Brief) -> Ballot | Delivery | None:
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


async def _run(seed: int, max_rounds: int, advisors: dict) -> None:
    try:
        await play_council(
            seed,
            _ask,
            reveal_news=False,
            max_rounds=max_rounds,
            ask_human=_ask_human,
            emit=paced_emit,
            ask_scout=_ask_scout,
            narrate=_narrate,
            store=store, run_id=live.run_id,
            advisors=advisors,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:  # surface transport or model failures in the UI
        log.exception("game failed")
        await journal_emit("error", {"message": f"{type(e).__name__}: {e}"})
    finally:
        council, live.council = live.council, None
        if council is not None and hasattr(council, "close"):
            await council.close()


class StartReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int = 7
    max_rounds: int | None = Field(default=None, ge=1, le=20)
    advisors: list[CouncilSeat] | None = Field(default=None, min_length=1, max_length=MAX_ADVISORS)

    @model_validator(mode="after")
    def validate_council(self):
        resolve_council(self.advisors)
        return self


@app.post("/api/game")
async def start(req: StartReq):
    if live.task and not live.task.done():
        raise HTTPException(409, "A live run is already active.")
    advisors = resolve_council(req.advisors)
    live.run_id = uid()
    live.paused = False
    live.resume.set()
    live.scout_request = None
    live.seed, live.year = req.seed, 1
    live.game_no += 1
    live.narration.clear()
    live.task = asyncio.create_task(
        _run(req.seed, req.max_rounds or settings.max_rounds, advisors)
    )
    await asyncio.sleep(0)
    require_run(live.run_id)
    return {"ok": True, "run_id": live.run_id}


@app.post("/api/stop")
async def stop():
    if live.task and not live.task.done():
        live.resume.set()
        live.task.cancel()
        try:
            await live.task
        except asyncio.CancelledError:
            pass
    return {"ok": True}


async def _ask_scout(year: int, round_no: int, phase: str, rows: list[dict]) -> str:
    await live.resume.wait()
    request = {"request_id": uid(), "year": year, "round": round_no,
               "phase": phase, "rows": rows, "options": SCOUT_OPTIONS}
    live.scout_request = request
    live.scout_pending = asyncio.get_running_loop().create_future()
    try:
        await journal_emit("scout_request", request)
        choice = await live.scout_pending
        await live.resume.wait()
        await journal_emit("scout_choice", {**request, "choice": choice})
        return choice
    finally:
        live.scout_pending = None
        live.scout_request = None


class ScoutReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    request_id: str
    choice: str


@app.post("/api/scout")
async def scout(req: ScoutReq):
    if (req.run_id != live.run_id or not live.scout_pending or live.scout_pending.done()
            or not live.scout_request or req.request_id != live.scout_request["request_id"]):
        raise HTTPException(409, "No matching scout choice is waiting.")
    if req.choice not in SCOUT_IDS:
        raise HTTPException(422, "Choose a listed event or skip.")
    if live.paused:
        raise HTTPException(409, "Resume the council before choosing a scout event.")
    live.scout_pending.set_result(req.choice)
    return {"ok": True}


class PauseReq(BaseModel):
    run_id: str
    paused: bool


@app.post("/api/pause")
async def pause(req: PauseReq):
    if req.run_id != live.run_id or not live.task or live.task.done():
        raise HTTPException(409, "No matching live council.")
    live.paused = req.paused
    if req.paused:
        live.resume.clear()
    else:
        live.resume.set()
    await journal_emit("pause", {"year": live.year, "paused": live.paused})
    return {"ok": True, "paused": live.paused}


class ChoiceReq(BaseModel):
    run_id: str
    choice: str


@app.post("/api/escalation")
async def choose(req: ChoiceReq):
    if req.run_id != live.run_id or not live.pending or live.pending.done():
        raise HTTPException(409, "no escalation pending")
    if req.choice not in live.options:
        raise HTTPException(400, "Choose a latest valid option.")
    if live.paused:
        raise HTTPException(409, "Resume the council before choosing a plan.")
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
        "max_advisors": MAX_ADVISORS,
        "years": YEARS,
        "pace_s": live.pace_s,
        "transport": settings.transport,
        "llm": settings.llm_mode,
        "model": settings.llm_model if settings.llm_mode == "bayleaf" else "unavailable",
        "voice": voice.enabled(),
        "max_rounds": settings.max_rounds,
        "escalation_timeout_s": settings.escalation_timeout_s,
        "budget_usd": settings.llm_budget_usd,
        "scout_options": SCOUT_OPTIONS,
        "scout_pending": live.scout_request,
        "paused": live.paused,
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



def import_traces():
    store.import_traces(os.getenv("SUMER_TRACE_PATH", str(CACHE_DIR / "otel-traces.jsonl")))


@app.get("/api/runs/{run_id}/traces")
async def traces(run_id: str, year: int | None = None):
    require_run(run_id)
    import_traces()
    values = store.traces(run_id)
    if year is not None:
        values = [s for s in values if any(a["key"] == "sumer.year" and
                  str(a["value"].get("intValue", a["value"].get("stringValue"))) == str(year)
                  for a in s.get("attributes", []))]
    return values



@app.get("/api/runs")
async def runs():
    return store.runs()


@app.get("/api/runs/{run_id}")
async def run_details(run_id: str):
    return require_run(run_id)


@app.get("/api/runs/{run_id}/events")
async def recorded_events(run_id: str, year: int | None = None, after: int = 0):
    require_run(run_id)
    return store.events(run_id, after=after, year=year)


@app.get("/api/runs/{run_id}/export")
async def export(run_id: str):
    import_traces()
    require_run(run_id)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in store.exports(run_id).items():
            archive.writestr(name, content)
    return Response(output.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="sumer-{run_id}.zip"'})



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
async def narration(year: int, game: int | None = None):
    if game is not None and game != live.game_no:
        raise HTTPException(404, "narration belongs to an earlier game")
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

