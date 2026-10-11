"""A scripted stand-in for the chamber backend, for building and testing web/ in isolation.

Serves web/ and streams a scripted 5-year, 5-advisor game over GET /api/events,
following the same event contract as the real backend (sumer.web).
Outcomes and city numbers are scripted UI examples, not actual agent decisions.
The fixed Provider/Farmer/Merchant/Priest/Villager roster is supported here;
custom councils require the real backend.

    uv run python scripts/fake_chamber.py            # http://localhost:8010
    uv run python scripts/fake_chamber.py --port 8000 --autostart

Year 1 reaches consensus after news, year 2 is a majority with dissent, year 3 goes to
a runoff from a 2-of-5 plan, year 4 escalates and waits for POST /api/escalation,
year 5 is a quick consensus.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from sumer.advisors import ADVISORS as PRESETS
from sumer.advisors import MAX_ADVISORS
from sumer.rulebooks import EXAMPLE_RULEBOOKS
from sumer.scouts import SCOUT_IDS, SCOUT_OPTIONS

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
RESULTS = ROOT / "results"
TMP = Path(tempfile.mkdtemp(prefix="fake-chamber-"))

ADVISORS = [a.public() for a in PRESETS.values()]
IDS = [a["id"] for a in ADVISORS]
SHORT = {a["id"]: a["short"] for a in ADVISORS}
RULES = {"majority": 3, "strong_majority": 4, "runoff_floor": 2}


class Hub:
    def __init__(self):
        self.backlog: list[dict] = []
        self.subs: set[asyncio.Queue] = set()

    async def emit(self, kind: str, data: dict) -> None:
        ev = {"kind": kind, "data": data, "run_id": live.run_id,
              "sequence": len(self.backlog)}
        self.backlog.append(ev)
        for q in list(self.subs):
            q.put_nowait(ev)


hub = Hub()


class Live:
    def __init__(self):
        self.task: asyncio.Task | None = None
        self.pace_s = 1.0
        self.injected: list[str] = []
        self.pending: asyncio.Future | None = None
        self.scout: tuple[str, asyncio.Future] | None = None
        self.resume = asyncio.Event()
        self.resume.set()
        self.run_id = uuid4().hex


live = Live()
RNG = random.Random(0)

NEWS_TEXT = {
    "harvest": "Scouts walked the fields: the barley is thin this year. Expect a poor harvest.",
    "rats": "Scouts found rat nests in the east granary. Rats may eat a share of the grain.",
    "plague": "Travellers from Lagash bring fever. Plague may reach the city this year.",
    "market": "Merchants from Kish say land prices will climb next spring.",
}


def plain(p: dict) -> str:
    land = p["land_delta"]
    land_s = f"buy {land} acres" if land > 0 else f"sell {-land} acres" if land < 0 else "keep our land"
    return f"{land_s.capitalize()}, feed {p['feed']:,} bushels, plant {p['plant']:,} acres"


def make_state(year, pop, grain, acres, price, starved, starved_total=0, report=None):
    return {"year": year, "population": pop, "grain": grain, "acres": acres, "land_price": price,
            "starved": starved, "starved_total": starved_total, "last_report": report or {},
            "acres_per_person": round(acres / max(pop, 1), 2)}


# Year-by-year city numbers after each year (index 0 = start).
STATES = [
    make_state(1, 100, 2800, 1000, 19, 0),
    make_state(2, 104, 2950, 1030, 22, 0, 0, {"harvest": 3000, "yield_per_acre": 3, "rats": 200, "starved": 0, "immigrants": 4, "plague_deaths": 0}),
    make_state(3, 101, 2400, 1080, 17, 6, 6, {"harvest": 2700, "yield_per_acre": 3, "rats": 450, "starved": 6, "immigrants": 3, "plague_deaths": 0}),
    make_state(4, 112, 3300, 1120, 24, 0, 6, {"harvest": 4400, "yield_per_acre": 4, "rats": 0, "starved": 0, "immigrants": 11, "plague_deaths": 0}),
    make_state(5, 96, 2900, 1100, 21, 3, 9, {"harvest": 3300, "yield_per_acre": 3, "rats": 150, "starved": 3, "immigrants": 2, "plague_deaths": 15}),
    make_state(6, 109, 3150, 1140, 20, 0, 9, {"harvest": 3900, "yield_per_acre": 4, "rats": 100, "starved": 0, "immigrants": 13, "plague_deaths": 0}),
]

LINES = {
    "provider": ["Every mouth fed comes first. {to}, a full ration this year means more hands next spring.",
               "I can live with less land, {to}, but I will not watch children go hungry.",
               "We fed everyone last year and the city grew. Let us not get greedy now."],
    "merchant": ["Land is cheap this year. {to}, buy now and sell when the price climbs.",
               "Land prices are high. Sell a few acres and buy back when they fall.",
               "{to}, compare what land costs today with what it could earn next spring."],
    "farmer": ["Seed in the ground is worth twice the grain in the jar. Plant every acre, {to}.",
               "We have the hands to work more fields. Idle land is wasted land.",
               "{to}, more planting now means a bigger harvest for all of us next year."],
    "priest": ["{to}, keep a reserve against famine. I can release some grain for seed.",
                  "Emergency stores matter, but starvation has a heavy cost.",
                  "{to}, a rich harvest fills our stores too. Let us bargain."],
    "villager": ["{to}, we share a threat from famine. Let us weigh the whole outcome.",
                 "Survivors, grain, and land all matter, as do the costs of hunger.",
                 "{to}, I can trade some land for a plan that improves our final score."],
}

THOUGHTS = {
    "provider": "Feed every person before spending grain on land or planting.",
    "farmer": "Planting every workable acre is my first priority.",
    "merchant": "Buying low and selling next year nets about 150 bushels. Worth some grumbling.",
    "priest": "Weigh final grain and emergency stores against survivors and starvation costs.",
    "villager": "Maximize the final score without any extra preference; negotiate useful concessions.",
}

PROPOSALS = {
    "provider": {"land_delta": 0, "feed": 2200, "plant": 700},
    "farmer": {"land_delta": 30, "feed": 2000, "plant": 1000},
    "merchant": {"land_delta": 50, "feed": 2000, "plant": 500},
    "priest": {"land_delta": 80, "feed": 1600, "plant": 500},
    "villager": {"land_delta": 0, "feed": 2000, "plant": 900},
}


def others(a):
    return [i for i in IDS if i != a]


async def pause(mult=1.0):
    await asyncio.sleep(live.pace_s * mult)
    await live.resume.wait()


async def maybe_news(year, rnd, scripted=None):
    kinds = list(live.injected)
    live.injected.clear()
    if scripted:
        await hub.emit("news", {"year": year, "round": rnd, "kind": scripted, "text": NEWS_TEXT[scripted], "injected": False})
    for k in kinds:
        await hub.emit("news", {"year": year, "round": rnd, "kind": k, "text": NEWS_TEXT[k], "injected": True})


async def discuss_round(year, rnd, max_rounds, support: dict[str, list[str]],
                        burst=False, rng: random.Random | None = None, revote=False) -> int:
    """support: plan author -> advisors accepting it. Returns the last round played."""
    rng = rng or RNG
    await hub.emit("round", {"year": year, "round": rnd, "phase": "discuss", "max_rounds": max_rounds,
                             "revote": revote})
    await pause(0.5)
    for i, a in enumerate(IDS):
        prop = dict(PROPOSALS[a])
        prop["feed"] += 20 * rnd * (1 if prop["feed"] < 2000 else -1)
        pid = f"{a}-y{year}-r{rnd}"
        addressed = [rng.choice(others(a))] if rng.random() < 0.7 else []
        if a == "villager":
            addressed = rng.sample(others(a), 2)
        to = " and ".join(SHORT[x] for x in addressed) if addressed else "friends"
        speech = rng.choice(LINES[a]).format(to=to)
        speech = re.sub(r"(^|[.!?] )([a-z])", lambda m: m.group(1) + m.group(2).upper(), speech)
        acc = [f"{au}-y{year}-r{rnd}" for au, who in support.items() if a in who]
        await hub.emit("turn", {
            "year": year, "round": rnd, "phase": "discuss", "advisor": a, "speech": speech,
            "proposal": prop, "plan_id": pid, "plain": plain(prop), "accept": acc,
            "runoff_vote": None, "addressed": addressed, "thought": THOUGHTS[a],
            "trace": {"forecast": {"yield": 3, "rat_chance": 0.4, "plague_chance": 0.15},
                      "llm": {"latency_s": round(rng.uniform(0.6, 2.4), 2), "cached": True, "cost_usd": 0.0004},
                      "scores": {p: round(rng.uniform(0.2, 0.9), 2) for p in acc}},
            "source": "scripted",
        })
        if not burst:
            await pause(0.7)
    rows = []
    for au in IDS:
        rows.append({"id": f"{au}-y{year}-r{rnd}", "author": au, "plain": plain(PROPOSALS[au]),
                     "accept": support.get(au, [])})
    rows.sort(key=lambda r: -len(r["accept"]))
    await hub.emit("wire", {"year": year, "round": rnd, "transport": "AGNTCY SLIM", "topic": f"sumer/council/y{year}r{rnd}",
                            "recipients": IDS, "bytes_out": 2148, "replies": len(IDS), "elapsed_s": 1.84})
    await hub.emit("tally", {"year": year, "round": rnd, "phase": "discuss", "rows": rows[:4]})
    await pause(0.5)
    # Like the real chamber, a scout event buys one re-vote round, up to the round limit.
    if await ask_scout(year, rnd, "discuss", rows[:4]) and rnd + 1 < max_rounds:
        return await discuss_round(year, rnd + 1, max_rounds, support, burst, rng, revote=True)
    return rnd


async def ask_scout(year, rnd, phase, rows) -> bool:
    """Mirror the real chamber: after each full vote the user picks an event or skips."""
    request = {"request_id": uuid4().hex, "year": year, "round": rnd, "phase": phase,
               "rows": rows, "options": SCOUT_OPTIONS}
    fut = asyncio.get_running_loop().create_future()
    live.scout = (request["request_id"], fut)
    await hub.emit("scout_request", request)
    try:
        choice = await fut
    finally:
        live.scout = None
    await hub.emit("scout_choice", {**request, "choice": choice})
    if choice == "skip":
        return False
    text = next(o["detail"] for o in SCOUT_OPTIONS if o["id"] == choice)
    await hub.emit("news", {"year": year, "round": rnd + 1, "kind": choice, "text": text, "injected": True})
    await pause(0.5)
    return True


async def finish_year(year, outcome, adopted_author, dissent, rounds, summary, narration,
                      adopted_round=None):
    st = STATES[year]
    plan = PROPOSALS.get(adopted_author, {"land_delta": -10, "feed": 2100, "plant": 900})
    adopted_round = rounds - 1 if adopted_round is None else adopted_round
    adopted = "safe" if adopted_author == "safe" else f"{adopted_author}-y{year}-r{adopted_round}"
    p_plain = "Play it safe: feed everyone, sell a little land, plant the most we can" if adopted_author == "safe" else plain(plan)
    await hub.emit("year", {
        "year": year, "outcome": outcome, "adopted": adopted, "plan": plan, "plain": p_plain, "dissent": dissent,
        "rounds": rounds, "summary": summary, "narration_url": narration, "report": st["last_report"], "state": st,
        "satisfaction": {a: round(random.uniform(0.4, 1.0), 2) for a in IDS}, "news": [],
    })
    await pause(1.5)


async def run(narration: str | None) -> None:
    await hub.emit("game", {"seed": 7, "years": 5, "state": STATES[0], "advisors": ADVISORS,
                            "rules": RULES})
    await pause(1)

    # Year 1: scripted consensus after the scouts report a poor harvest.
    await hub.emit("year_start", {"year": 1, "state": STATES[0]})
    await maybe_news(1, 0)
    r = await discuss_round(1, 0, 4, {"provider": ["provider", "farmer"],
                                    "merchant": ["merchant", "priest"], "villager": ["villager"]})
    await maybe_news(1, r + 1, scripted="harvest")
    r = await discuss_round(1, r + 1, 4, {"provider": IDS})
    await finish_year(1, "consensus", "provider", [], r + 1,
                      "All five backed the Provider's full-ration plan in this scripted scene. The city grew by four people.", narration)

    # Year 2: majority with dissent, one round in a burst.
    await hub.emit("year_start", {"year": 2, "state": STATES[1]})
    await maybe_news(2, 0)
    r = await discuss_round(2, 0, 4, {"farmer": ["provider", "farmer", "merchant"],
                                    "priest": ["priest"], "villager": ["villager"]}, burst=True)
    await maybe_news(2, r + 1)
    r = await discuss_round(2, r + 1, 4, {"farmer": ["provider", "farmer", "merchant", "priest"],
                                        "villager": ["villager"]})
    await finish_year(2, "majority", "farmer", ["villager"], r + 1,
                      "Four of five backed the Farmer's plan to buy land and plant more. Rats ate 450 bushels and six people starved.", narration)

    # Year 3: no majority, runoff between the top two plans.
    await hub.emit("year_start", {"year": 3, "state": STATES[2]})
    await maybe_news(3, 0, scripted="rats")
    split = {"farmer": ["farmer", "priest"], "provider": ["provider", "merchant"], "villager": ["villager"]}
    r = await discuss_round(3, 0, 4, split)
    await maybe_news(3, r + 1)
    r = await discuss_round(3, r + 1, 4, split)
    await hub.emit("round", {"year": 3, "round": r + 1, "phase": "runoff", "max_rounds": 4})
    a = {"id": f"farmer-y3-r{r}", "author": "farmer", "plain": plain(PROPOSALS["farmer"])}
    b = {"id": f"provider-y3-r{r}", "author": "provider", "plain": plain(PROPOSALS["provider"])}
    await hub.emit("runoff", {"year": 3, "a": a, "b": b, "reason": "No plan reached a majority after 2 rounds. The top two go head to head."})
    await pause(1)
    votes = {"provider": b, "farmer": a, "merchant": a, "priest": a, "villager": b}
    why = {
        "provider": "I back the Provider's plan: feed everyone first.",
        "farmer": "I back my planting plan: put every available field to work.",
        "merchant": "I back the Farmer: productive land should hold its value.",
        "priest": "I back the Farmer: more harvest can mean more wealth.",
        "villager": "Feeding people protects our score. I can back the Provider.",
    }
    for adv in IDS:
        v = votes[adv]
        await hub.emit("turn", {"year": 3, "round": r + 1, "phase": "runoff", "advisor": adv, "speech": why[adv],
                                "proposal": None, "plan_id": None, "plain": None, "accept": [],
                                "runoff_vote": v["id"], "addressed": [], "thought": THOUGHTS[adv], "trace": {}, "source": "scripted"})
        await pause(0.6)
    await hub.emit("runoff_result", {"year": 3, "votes": {k: v["id"] for k, v in votes.items()}, "winner": a["id"],
                                     "count": {a["id"]: 3, b["id"]: 2}})
    await pause(1)
    await finish_year(3, "runoff", "farmer", ["provider", "villager"], r + 2,
                      "The Farmer's plan won the runoff three to two. A bumper harvest filled the granary.", narration,
                      adopted_round=r)

    # Year 4: deadlock, the human decides.
    await hub.emit("year_start", {"year": 4, "state": STATES[3]})
    await maybe_news(4, 0, scripted="plague")
    r = await discuss_round(4, 0, 4, {a: [a] for a in IDS})
    await maybe_news(4, r + 1)
    r = await discuss_round(4, r + 1, 4, {a: [a] for a in IDS})
    options = [
        {"id": f"provider-y4-r{r}", "plan": PROPOSALS["provider"], "plain": plain(PROPOSALS["provider"]),
         "accept": ["provider"]},
        {"id": f"farmer-y4-r{r}", "plan": PROPOSALS["farmer"], "plain": plain(PROPOSALS["farmer"]),
         "accept": ["farmer"]},
        {"id": "safe", "plan": {"land_delta": -10, "feed": 2100, "plant": 900},
         "plain": "Play it safe: feed everyone, sell a little land, plant the most we can", "accept": [],
         "note": "Agreed in advance as the fallback"},
    ]
    positions = [{"advisor": a, "speech": LINES[a][0].format(to="friends"), "thought": THOUGHTS[a]}
                 for a in IDS]
    await hub.emit("escalate", {"year": 4, "reason": "Every advisor backs only their own plan. None reaches the two-vote runoff floor or the three-vote majority.",
                                "options": options, "positions": positions})
    live.pending = asyncio.get_running_loop().create_future()
    try:
        choice = await asyncio.wait_for(live.pending, 600)
    except TimeoutError:
        await hub.emit("escalation_timeout", {"fallback": "safe"})
        choice = "safe"
    finally:
        live.pending = None
    author = choice.split("-")[0]
    await finish_year(4, "escalated", author if author in PROPOSALS else "safe", [], r + 1,
                      "You broke the deadlock. Plague struck and took fifteen people, but the granary held.", narration)

    # Year 5: quick consensus.
    await hub.emit("year_start", {"year": 5, "state": STATES[4]})
    await maybe_news(5, 0, scripted="market")
    r = await discuss_round(5, 0, 4, {"provider": IDS})
    await finish_year(5, "consensus", "provider", [], r + 1,
                      "In the final scripted scene all five backed the Provider. The city ends larger than it began.", narration)

    await hub.emit("end", {"rating": {"verdict": "fair", "verdict_reason": "2% starved on average, 10.5 acres per person",
                                      "avg_starved_pct": 1.8, "acres_per_person": 10.46, "population": 109, "grain": 3150,
                                      "acres": 1140, "starved_total": 9, "impeached": False, "score": 1312.4},
                           "outcomes": {"consensus": 2, "majority": 1, "runoff": 1, "escalated": 1},
                           "years": [{"year": y} for y in range(1, 6)]})


def make_narration() -> str | None:
    if not shutil.which("ffmpeg"):
        return None
    out = TMP / "narration.mp3"
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
           "sine=frequency=523:duration=0.5", "-f", "lavfi", "-i", "sine=frequency=659:duration=0.5",
           "-f", "lavfi", "-i", "sine=frequency=784:duration=0.9",
           "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1,volume=0.3,afade=t=out:st=1.4:d=0.5", str(out)]
    try:
        subprocess.run(cmd, check=True, timeout=20)
    except (subprocess.SubprocessError, OSError):
        return None
    return "/fake/narration.mp3"


app = FastAPI(title="Fake Council of Sumer")
NARRATION = make_narration()


async def _run_safe():
    try:
        await run(NARRATION)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - surface any fake-game failure in the UI
        await hub.emit("error", {"message": f"{type(e).__name__}: {e}"})


class StartReq(BaseModel):
    seed: int = 7
    reveal_news: bool = True
    max_rounds: int | None = None
    advisors: list[dict] | None = None


@app.post("/api/game")
async def start(req: StartReq):
    # Setup sends every seat with its bias; only the roster order matters to the script.
    if req.advisors is not None and [a.get("id") for a in req.advisors] != IDS:
        raise HTTPException(400, "This scripted preview supports the five default voters only. Use the real backend for custom councils.")
    if live.task and not live.task.done():
        live.task.cancel()
    hub.backlog.clear()
    live.injected.clear()
    live.resume.set()
    live.run_id = uuid4().hex
    live.task = asyncio.create_task(_run_safe())
    return {"ok": True, "run_id": live.run_id}


@app.post("/api/stop")
async def stop():
    if live.task and not live.task.done():
        live.task.cancel()
    return {"ok": True}


class NewsReq(BaseModel):
    kind: str


@app.post("/api/news")
async def news(req: NewsReq):
    if req.kind not in NEWS_TEXT:
        raise HTTPException(400, "unknown report kind")
    live.injected.append(req.kind)
    return {"ok": True}


class ScoutReq(BaseModel):
    run_id: str = ""
    request_id: str
    choice: str


@app.post("/api/scout")
async def scout(req: ScoutReq):
    if not live.scout or live.scout[0] != req.request_id or live.scout[1].done():
        raise HTTPException(409, "No matching scout choice is waiting.")
    if req.choice not in SCOUT_IDS:
        raise HTTPException(422, "Choose a listed event or skip.")
    live.scout[1].set_result(req.choice)
    return {"ok": True}


class ChoiceReq(BaseModel):
    choice: str


@app.post("/api/escalation")
async def choose(req: ChoiceReq):
    if not live.pending or live.pending.done():
        raise HTTPException(409, "no escalation pending")
    live.pending.set_result(req.choice)
    return {"ok": True}


class PauseReq(BaseModel):
    run_id: str = ""
    paused: bool


@app.post("/api/pause")
async def pause_run(req: PauseReq):
    if not live.task or live.task.done():
        raise HTTPException(409, "No matching live council.")
    live.resume.clear() if req.paused else live.resume.set()
    await hub.emit("pause", {"paused": req.paused})
    return {"ok": True, "paused": req.paused}


class PaceReq(BaseModel):
    pace_s: float


@app.post("/api/pace")
async def pace(req: PaceReq):
    live.pace_s = max(0.0, min(10.0, req.pace_s))
    return {"ok": True}


@app.get("/api/meta")
async def meta():
    return {"advisors": ADVISORS, "example_rulebooks": EXAMPLE_RULEBOOKS, "max_advisors": MAX_ADVISORS, "years": 5,
            "active_run": live.run_id if live.task and not live.task.done() else "",
            "transport": "scripted preview", "llm": "scripted", "model": "UI fixture",
            "voice": NARRATION is not None, "max_rounds": 4, "escalation_timeout_s": 0, "budget_usd": 3.0,
            "report_kinds": list(NEWS_TEXT)}


@app.get("/api/runs")
async def runs():
    return []  # This transient fixture does not persist recorded runs.


@app.get("/api/spend")
async def spend():
    return {"spent_usd": 0.012, "budget_usd": 3.0}


@app.get("/api/bench")
async def bench():
    p = RESULTS / "bench.json"
    data = json.loads(p.read_text()) if p.exists() else {}
    if not data.get("rule_version"):  # same rule as sumer.web: stale results are not evidence
        return JSONResponse({"missing": True})
    return JSONResponse(data)


@app.get("/api/events")
async def events():
    q: asyncio.Queue = asyncio.Queue()
    backlog = list(hub.backlog)
    hub.subs.add(q)

    async def stream():
        try:
            for ev in backlog:
                yield f"data: {json.dumps(ev)}\n\n"
            while True:
                try:
                    yield f"data: {json.dumps(await asyncio.wait_for(q.get(), 15))}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            hub.subs.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/fake/narration.mp3")
async def narration():
    p = TMP / "narration.mp3"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/mpeg")


@app.get("/fake/frame")
async def frame(w: int = 1280, h: int = 720, scale: float = 1.0):
    """Test harness: the chamber in an iframe of an exact size (for screenshots under tiling WMs)."""
    html = (f"<!doctype html><body style='margin:0;background:#333'>"
            f"<iframe src='/' style='width:{w}px;height:{h}px;border:0;transform:scale({scale});transform-origin:0 0'></iframe>")
    return Response(html, media_type="text/html")


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html", headers={"cache-control": "no-store"})


@app.get("/{name}")
async def static(name: str):
    p = (WEB / name).resolve()
    if p.parent != WEB.resolve() or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, headers={"cache-control": "no-store"})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--autostart", action="store_true", help="start a game as soon as the server boots")
    args = ap.parse_args()
    if args.autostart:
        @app.on_event("startup")
        async def _auto():
            live.task = asyncio.create_task(_run_safe())
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
