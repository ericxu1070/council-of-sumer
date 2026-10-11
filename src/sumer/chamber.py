"""Plays whole games: the council (over a transport) or a baseline."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from importlib.metadata import version

from sumer.advisors import (
    ADVISOR_IDS,
    ADVISORS,
    Beliefs,
    Scorer,
    apply_report,
    report_for_year,
    scout_report,
)
from sumer.baselines import SIMPLE, lone_llm
from sumer.config import CACHE_DIR, settings
from sumer.deliberation import (
    Ask,
    AskHuman,
    Deadlock,
    Emit,
    YearResult,
    _noop_emit,
    deliberate_year,
)
from sumer.engine import commit
from sumer.game import RULE_VERSION, YEARS, advance, new_game, rating
from sumer.narrator import summarize
from sumer.protocol import provision_identities, uid
from sumer.storage import Store

# Turns the narrator's line for a year into audio; returns its URL or None.
DEFAULT_DB = os.getenv("SUMER_DB_PATH", str(CACHE_DIR / "runs.sqlite3"))

Narrate = Callable[[int, str], Awaitable[str | None]]


@dataclass
class GameResult:
    seed: int
    policy: str
    rating: dict
    run_id: str = ""
    status: str = "completed"
    human_assisted: bool = False
    years: list[dict] = field(default_factory=list)
    outcomes: dict[str, int] = field(default_factory=dict)
    mean_rounds: float = 0.0
    mean_turns: float = 0.0
    satisfaction: dict[str, float] = field(default_factory=dict)
    info_shifted: int = 0  # years where news changed at least one advisor's proposal

    def as_dict(self) -> dict:
        return self.__dict__


def scheduled_news(seed: int, year: int, rolls, reveal: bool) -> dict | None:
    if not reveal or year - 1 >= len(rolls):
        return None
    r = rolls[year - 1]
    kind = report_for_year(seed, year, r)
    nxt = rolls[year] if year < len(rolls) else None
    return scout_report(kind, r, nxt) if kind else None


async def play_council(
    seed: int,
    ask: Ask,
    *,
    reveal_news: bool = True,
    max_rounds: int | None = None,
    ask_human: AskHuman | None = None,
    emit: Emit = _noop_emit,
    injected_news: list[dict] | None = None,
    narrate: Narrate | None = None,
    store: Store | None = None,
    run_id: str | None = None,
) -> GameResult:
    own_store = store is None
    store = store or Store(DEFAULT_DB)
    run_id = run_id or uid()
    keys = provision_identities()
    state, rolls = new_game(seed)
    store.create_run(run_id, {"rule_version": RULE_VERSION, "seed": seed, "initial_state": state.public(),
        "application_version": version("council-of-sumer"), "peer_keys": keys,
        "advisors": [a.public() for a in ADVISORS.values()], "reign_years": YEARS,
        "max_rounds": max_rounds or settings.max_rounds, "transport": settings.transport,
        "llm_mode": settings.llm_mode, "model": settings.llm_model,
        "dependencies": {name: version(name) for name in ("agntcy-app-sdk", "cryptography", "pydantic")}})
    publish = emit
    async def emit(kind, data):
        if kind == "escalate":
            store.status(run_id, "deadlock")
        record = store.append(run_id, kind, data)
        await publish(kind, {**data, "_sequence": record["sequence"], "_run_id": run_id})

    cancelled = False
    status = "running"
    history: list[dict] = []
    results: list[YearResult] = []
    try:
        await emit("game", {"seed": seed, "years": YEARS, "state": state.public(),
                            "max_rounds": max_rounds or settings.max_rounds,
                            "advisors": [a.public() for a in ADVISORS.values()]})
        while not state.over:
            await emit("year_start", {"year": state.year, "state": state.public()})
            before = state
            yr = await deliberate_year(
                seed=seed,
                state=state,
                ask=ask,
                history=history,
                max_rounds=max_rounds or settings.max_rounds,
                scheduled_news=scheduled_news(seed, state.year, rolls, reveal_news),
                injected_news=injected_news,
                ask_human=ask_human,
                emit=emit,
                run_id=run_id, peer_keys=keys,
            )
            preview = advance(state, yr.plan, rolls)
            yr.summary = summarize(yr, before, preview)
            state, event = commit(store, run_id, state, yr, rolls)
            results.append(yr)
            history.append({**event["data"], "result": state.last_report})
            url = await narrate(yr.year, yr.summary) if narrate else None
            if yr.human_resolution:
                for resolution in store.events(run_id, after=event["sequence"] - 2):
                    if resolution["kind"] == "human_resolution":
                        await publish("human_resolution", {**resolution["data"], "_sequence": resolution["sequence"], "_run_id": run_id})
            await publish("year", {**event["data"], "narration_url": url,
                                "satisfaction": yr.satisfaction, "runoff": yr.runoff,
                                "human_choice": yr.human_choice,
                                "opening": {k: v.model_dump() for k, v in yr.opening.items()},
                                "after_news": {k: v.model_dump() for k, v in yr.after_news.items()},
                                "news_items": yr.news, "_sequence": event["sequence"], "_run_id": run_id})
        status = state.status
    except Deadlock:
        status = "deadlock"
    except asyncio.CancelledError:
        cancelled = True
        status = "stopped"
        await emit("stopped", {"year": state.year, "message": "Run stopped by user."})
    except Exception as exc:  # noqa: BLE001 - retain terminal failures in the journal
        status = "technical_failure"
        await emit("technical_failure", {"year": state.year, "message": f"{type(exc).__name__}: {exc}"})
    g = _summarize(seed, "council", state, results)
    g.run_id, g.status = run_id, status
    g.human_assisted = store.run(run_id)["human_assisted"]
    store.status(run_id, status, g.as_dict())
    await emit("end", g.as_dict())
    if own_store:
        store.db.close()
    if cancelled:
        raise asyncio.CancelledError
    return g


async def play_simple(seed: int, policy: str, *, reveal_news: bool = True) -> GameResult:
    state, rolls = new_game(seed)
    sats: list[dict] = []
    while not state.over:
        news = scheduled_news(seed, state.year, rolls, reveal_news)
        beliefs = apply_report(Beliefs(), news)
        if policy == "lone_llm":
            d = await lone_llm(state, beliefs, [news] if news else [])
        else:
            d = SIMPLE[policy](state, beliefs)
        sc = Scorer(state, beliefs, extra=[d])
        sats.append({a: sc.norm(a, d) for a in ADVISOR_IDS})
        state = advance(state, d, rolls)
    g = GameResult(seed=seed, policy=policy, rating=rating(state), status=state.status)
    g.satisfaction = {a: round(sum(s[a] for s in sats) / len(sats), 3) for a in ADVISOR_IDS}
    return g


def _summarize(seed, policy, state, results: list[YearResult]) -> GameResult:
    g = GameResult(seed=seed, policy=policy, rating=rating(state), status=state.status)
    g.years = [{**r.record(), "summary": r.summary} for r in results]
    for r in results:
        g.outcomes[r.outcome] = g.outcomes.get(r.outcome, 0) + 1
    g.mean_rounds = round(sum(r.rounds for r in results) / max(len(results), 1), 2)
    g.mean_turns = round(sum(r.turns for r in results) / max(len(results), 1), 1)
    g.satisfaction = {
        a: round(sum(r.satisfaction[a] for r in results) / max(len(results), 1), 3)
        for a in ADVISOR_IDS
    }
    g.info_shifted = sum(
        1 for r in results
        if r.news and r.after_news and any(r.after_news[a] != r.opening.get(a) for a in r.after_news)
    )
    return g
