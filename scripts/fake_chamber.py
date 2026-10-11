"""A scripted stand-in for the chamber backend, for building and testing web/ in isolation.

Serves web/ and streams a realistic 5-year, 7-advisor game over GET /api/events,
following the same event contract as the real backend (sumer.web).

    uv run python scripts/fake_chamber.py            # http://localhost:8010
    uv run python scripts/fake_chamber.py --port 8000 --autostart

Year 1 reaches consensus after news, year 2 is a majority with dissent, year 3 goes to
a runoff from a 3-of-7 plan, year 4 escalates and waits for POST /api/escalation,
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

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
RESULTS = ROOT / "results"
TMP = Path(tempfile.mkdtemp(prefix="fake-chamber-"))

ADVISORS = [
    {"id": "farmer", "title": "Voice of the Farmers", "short": "Farmer", "emoji": "🌾", "color": "#5f8f3a",
     "mandate": "Grow the population. Nobody starves."},
    {"id": "priest", "title": "High Priest of Enlil", "short": "Priest", "emoji": "🔮", "color": "#7a4fa3",
     "mandate": "Keep the granaries full for a bad year."},
    {"id": "general", "title": "General of the Walls", "short": "General", "emoji": "🛡️", "color": "#a33a32",
     "mandate": "Hold and expand our land."},
    {"id": "merchant", "title": "Chief Merchant", "short": "Merchant", "emoji": "💰", "color": "#b5862a",
     "mandate": "Buy land cheap, sell it dear."},
    {"id": "builder", "title": "Master Builder", "short": "Builder", "emoji": "🧱", "color": "#b2622f",
     "mandate": "Plant every acre we can work."},
    {"id": "healer", "title": "Royal Healer", "short": "Healer", "emoji": "🌿", "color": "#2f8a7c",
     "mandate": "Keep people healthy and well fed."},
    {"id": "diplomat", "title": "Envoy to Kish", "short": "Diplomat", "emoji": "🕊️", "color": "#2f5ea8",
     "mandate": "Keep the peace inside the council."},
]
IDS = [a["id"] for a in ADVISORS]
SHORT = {a["id"]: a["short"] for a in ADVISORS}


class Hub:
    def __init__(self):
        self.backlog: list[dict] = []
        self.subs: set[asyncio.Queue] = set()

    async def emit(self, kind: str, data: dict) -> None:
        ev = {"kind": kind, "data": data}
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
    "farmer": ["Every mouth fed comes first. {to}, a full ration this year means more hands next spring.",
               "I can live with less land, {to}, but I will not watch children go hungry.",
               "We fed everyone last year and the city grew. Let us not get greedy now."],
    "priest": ["The gods are fickle. {to}, a full granary is the only prayer that always works.",
               "Keep a year of grain in store. A lean harvest has ruined richer cities than ours.",
               "{to}, your plan leaves the store bare by winter. I cannot bless that."],
    "general": ["Land is power. {to}, if we sell now, Kish will be at our walls by spring.",
               "Buy while the price is low. Every acre we hold is an acre the enemy does not.",
               "I will give up some ambition, {to}, but not one acre of what we hold."],
    "merchant": ["Land is cheap this year. {to}, buy now and sell when the price climbs.",
               "Grain rotting in the store earns nothing. Put it to work.",
               "{to}, think of it as a trade: a little hunger now for a rich city later."],
    "builder": ["Seed in the ground is worth twice the grain in the jar. Plant every acre, {to}.",
               "We have the hands to work more fields. Idle land is wasted land.",
               "{to}, more planting now means a bigger harvest for all of us next year."],
    "healer": ["Hungry people catch fever. {to}, feed them well and plague finds no foothold.",
               "A full ration is medicine. I will not sign anything that cuts it.",
               "{to}, the sick cannot plant or fight. Feed the city first."],
    "diplomat": ["{to}, you both want a strong city. Let us meet in the middle.",
               "Nobody gets everything this year. Can we agree on a plan the city can afford?",
               "I hear the Priest and the Farmer. {to}, would you accept a smaller purchase?"],
}

THOUGHTS = {
    "farmer": "Feeding 20 per head is non-negotiable. I can concede on land if the General backs full rations.",
    "priest": "With a 40% chance of a bad harvest, we need about 2000 bushels in reserve.",
    "general": "Price is 19 now and likely 22 next year. Buying 50 acres costs under 1000 bushels.",
    "merchant": "Buying low and selling next year nets about 150 bushels. Worth some grumbling.",
    "builder": "We can work 10 acres per person. Planting 1000 acres maximizes next year's harvest.",
    "healer": "Malnourished people double plague losses. Full rations matter more than reserves.",
    "diplomat": "The Builder's and Farmer's plans overlap most. Pushing them together could win 5 votes.",
}

PROPOSALS = {
    "farmer": {"land_delta": 0, "feed": 2080, "plant": 700},
    "priest": {"land_delta": -20, "feed": 1900, "plant": 600},
    "general": {"land_delta": 60, "feed": 1800, "plant": 600},
    "merchant": {"land_delta": 80, "feed": 1600, "plant": 500},
    "builder": {"land_delta": 30, "feed": 2000, "plant": 1000},
    "healer": {"land_delta": 0, "feed": 2200, "plant": 600},
    "diplomat": {"land_delta": 20, "feed": 2000, "plant": 800},
}


def others(a):
    return [i for i in IDS if i != a]


async def pause(mult=1.0):
    await asyncio.sleep(live.pace_s * mult)


async def maybe_news(year, rnd, scripted=None):
    kinds = list(live.injected)
    live.injected.clear()
    if scripted:
        await hub.emit("news", {"year": year, "round": rnd, "kind": scripted, "text": NEWS_TEXT[scripted], "injected": False})
    for k in kinds:
        await hub.emit("news", {"year": year, "round": rnd, "kind": k, "text": NEWS_TEXT[k], "injected": True})


async def discuss_round(year, rnd, max_rounds, support: dict[str, list[str]],
                        burst=False, rng: random.Random | None = None):
    """support: plan author -> advisors accepting it."""
    rng = rng or RNG
    await hub.emit("round", {"year": year, "round": rnd, "phase": "discuss", "max_rounds": max_rounds})
    await pause(0.5)
    for i, a in enumerate(IDS):
        prop = dict(PROPOSALS[a])
        prop["feed"] += 20 * rnd * (1 if prop["feed"] < 2000 else -1)
        pid = f"{a}-y{year}-r{rnd}"
        addressed = [rng.choice(others(a))] if rng.random() < 0.7 else []
        if a == "diplomat":
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
                            "recipients": IDS, "bytes_out": 2148, "replies": 7, "elapsed_s": 1.84})
    await hub.emit("tally", {"year": year, "round": rnd, "phase": "discuss", "rows": rows[:4]})
    await pause(0.5)


async def finish_year(year, outcome, adopted_author, dissent, rounds, summary, narration):
    st = STATES[year]
    plan = PROPOSALS.get(adopted_author, {"land_delta": -10, "feed": 2100, "plant": 900})
    adopted = "safe" if adopted_author == "safe" else f"{adopted_author}-y{year}-r{rounds - 1}"
    p_plain = "Play it safe: feed everyone, sell a little land, plant the most we can" if adopted_author == "safe" else plain(plan)
    await hub.emit("year", {
        "year": year, "outcome": outcome, "adopted": adopted, "plan": plan, "plain": p_plain, "dissent": dissent,
        "rounds": rounds, "summary": summary, "narration_url": narration, "report": st["last_report"], "state": st,
        "satisfaction": {a: round(random.uniform(0.4, 1.0), 2) for a in IDS}, "news": [],
    })
    await pause(1.5)


async def run(narration: str | None) -> None:
    await hub.emit("game", {"seed": 7, "years": 5, "state": STATES[0], "advisors": ADVISORS})
    await pause(1)

    # Year 1: consensus after the scouts report locusts.
    await hub.emit("year_start", {"year": 1, "state": STATES[0]})
    await maybe_news(1, 0)
    await discuss_round(1, 0, 4, {"farmer": ["farmer", "healer", "diplomat"], "builder": ["builder", "general", "diplomat"],
                                  "general": ["general", "merchant"]})
    await maybe_news(1, 1, scripted="harvest")
    await discuss_round(1, 1, 4, {"farmer": IDS})
    await finish_year(1, "consensus", "farmer", [], 2,
                      "All seven advisors agreed to feed everyone and keep our land. The city grew by four people.", narration)

    # Year 2: majority with dissent, one round in a burst.
    await hub.emit("year_start", {"year": 2, "state": STATES[1]})
    await maybe_news(2, 0)
    await discuss_round(2, 0, 4, {"general": ["general", "merchant", "builder"], "farmer": ["farmer", "healer"]}, burst=True)
    await maybe_news(2, 1)
    await discuss_round(2, 1, 4, {"general": ["general", "merchant", "builder", "diplomat"]})
    await maybe_news(2, 2)
    await discuss_round(2, 2, 4, {"builder": ["general", "builder", "diplomat", "farmer", "healer"]})
    await finish_year(2, "majority", "builder", ["merchant", "priest"], 3,
                      "Five of seven backed the Builder's plan to buy land and plant more. Rats ate 450 bushels and six people starved.", narration)

    # Year 3: no majority, runoff between the top two plans.
    await hub.emit("year_start", {"year": 3, "state": STATES[2]})
    await maybe_news(3, 0, scripted="rats")
    await discuss_round(3, 0, 4, {"builder": ["builder", "general", "merchant"], "farmer": ["farmer", "healer"],
                                  "priest": ["priest"]})
    await maybe_news(3, 1)
    await discuss_round(3, 1, 4, {"builder": ["builder", "general", "merchant"], "farmer": ["farmer", "healer", "diplomat"]})
    await hub.emit("round", {"year": 3, "round": 2, "phase": "runoff", "max_rounds": 4})
    a = {"id": "builder-y3-r1", "author": "builder", "plain": plain(PROPOSALS["builder"])}
    b = {"id": "farmer-y3-r1", "author": "farmer", "plain": plain(PROPOSALS["farmer"])}
    await hub.emit("runoff", {"year": 3, "a": a, "b": b, "reason": "No plan reached a majority after 2 rounds. The top two go head to head."})
    await pause(1)
    votes = {"farmer": b, "priest": b, "general": a, "merchant": a, "builder": a, "healer": b, "diplomat": a}
    why = {a["id"]: "I back the Builder: more seed in the ground means more food next year.",
           b["id"]: "I back the Farmer: a full ration this year keeps the city strong."}
    for adv in IDS:
        v = votes[adv]
        await hub.emit("turn", {"year": 3, "round": 2, "phase": "runoff", "advisor": adv, "speech": why[v["id"]],
                                "proposal": None, "plan_id": None, "plain": None, "accept": [],
                                "runoff_vote": v["id"], "addressed": [], "thought": THOUGHTS[adv], "trace": {}, "source": "scripted"})
        await pause(0.6)
    await hub.emit("runoff_result", {"year": 3, "votes": {k: v["id"] for k, v in votes.items()}, "winner": a["id"],
                                     "count": {a["id"]: 4, b["id"]: 3}})
    await pause(1)
    await finish_year(3, "runoff", "builder", ["farmer", "priest", "healer"], 3,
                      "The Builder's plan won the runoff four to three. A bumper harvest filled the granary.", narration)

    # Year 4: deadlock, the human decides.
    await hub.emit("year_start", {"year": 4, "state": STATES[3]})
    await maybe_news(4, 0, scripted="plague")
    await discuss_round(4, 0, 4, {"healer": ["healer", "farmer"], "general": ["general", "merchant"]})
    await maybe_news(4, 1)
    await discuss_round(4, 1, 4, {"healer": ["healer", "farmer"], "general": ["general", "merchant"]})
    options = [
        {"id": "healer-y4-r1", "plan": PROPOSALS["healer"], "plain": plain(PROPOSALS["healer"]),
         "accept": ["healer", "farmer"]},
        {"id": "general-y4-r1", "plan": PROPOSALS["general"], "plain": plain(PROPOSALS["general"]),
         "accept": ["general", "merchant"]},
        {"id": "safe", "plan": {"land_delta": -10, "feed": 2100, "plant": 900},
         "plain": "Play it safe: feed everyone, sell a little land, plant the most we can", "accept": [],
         "note": "Agreed in advance as the fallback"},
    ]
    positions = [{"advisor": a, "speech": LINES[a][0].format(to="friends"), "thought": THOUGHTS[a]}
                 for a in IDS]
    await hub.emit("escalate", {"year": 4, "reason": "The council remains divided over food and land, with no plan reaching three accepts.",
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
    await finish_year(4, "escalated", author if author in PROPOSALS else "safe", [], 2,
                      "You broke the deadlock. Plague struck and took fifteen people, but the granary held.", narration)

    # Year 5: quick consensus.
    await hub.emit("year_start", {"year": 5, "state": STATES[4]})
    await maybe_news(5, 0, scripted="market")
    await discuss_round(5, 0, 4, {"diplomat": IDS})
    await finish_year(5, "consensus", "diplomat", [], 1,
                      "In the final year everyone agreed on the Diplomat's balanced plan. The city ends larger than it began.", narration)

    await hub.emit("end", {"rating": {"verdict": "fair", "verdict_reason": "2% starved on average, 10.5 acres per person",
                                      "avg_starved_pct": 1.8, "acres_per_person": 10.46, "population": 109, "grain": 3150,
                                      "acres": 1140, "starved_total": 9, "impeached": False, "score": 1312.4},
                           "outcomes": {"consensus": 2, "majority": 1, "runoff": 1, "escalated": 1}, "years": 5})


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


@app.post("/api/game")
async def start(req: StartReq):
    if live.task and not live.task.done():
        live.task.cancel()
    hub.backlog.clear()
    live.injected.clear()
    live.task = asyncio.create_task(_run_safe())
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
    if req.kind not in NEWS_TEXT:
        raise HTTPException(400, "unknown report kind")
    live.injected.append(req.kind)
    return {"ok": True}


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
    return {"advisors": ADVISORS, "transport": "slim", "llm": "bayleaf", "model": "deepseek-v4.1-flash",
            "voice": NARRATION is not None, "max_rounds": 4, "escalation_timeout_s": 0, "budget_usd": 3.0,
            "report_kinds": list(NEWS_TEXT)}


@app.get("/api/spend")
async def spend():
    return {"spent_usd": 0.012, "budget_usd": 3.0}


@app.get("/api/bench")
async def bench():
    p = RESULTS / "bench.json"
    return JSONResponse(json.loads(p.read_text()) if p.exists() else {"missing": True})


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
