"""Plays whole games: the council (over a transport) or a baseline."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from importlib.metadata import version

from sumer.advisors import (
    ADVISOR_IDS,
    Advisor,
    Beliefs,
    Scorer,
    apply_report,
    report_for_year,
    resolve_council,
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
    voting_rules,
)
from sumer.engine import commit
from sumer.game import RULE_VERSION, YEARS, advance, new_game, rating
from sumer.narrator import summarize, year_speech
from sumer.observability import context_headers, flush, span
from sumer.protocol import uid
from sumer.scouts import apply_scout
from sumer.storage import Store

DEFAULT_DB = os.getenv("SUMER_DB_PATH", str(CACHE_DIR / "runs.sqlite3"))

# Voices a year's end speech (narrator.year_speech); returns audio URLs by part
# ("speech", "headline"), leaving out any part with no audio.
Narrate = Callable[[int, dict], Awaitable[dict[str, str]]]
Scout = Callable[[int, int, str, list[dict]], Awaitable[str]]


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
    advisors: dict[str, Advisor] | None = None,
    ask_scout: Scout | None = None,
) -> GameResult:
    advisors = resolve_council() if advisors is None else dict(advisors)
    public_advisors = [a.public() for a in advisors.values()]
    rules = voting_rules(len(advisors))
    own_store = store is None
    store = store or Store(DEFAULT_DB)
    run_id = run_id or uid()
    state, rolls = new_game(seed)
    store.create_run(run_id, {"rule_version": RULE_VERSION, "seed": seed, "initial_state": state.public(),
        "application_version": version("council-of-sumer"),
        "advisors": public_advisors, "advisor_ids": list(advisors), "rules": rules,
        "reign_years": YEARS, "scout_mode": "after_vote" if ask_scout else "legacy",
        "max_rounds": max_rounds or settings.max_rounds, "transport": settings.transport,
        "llm_mode": settings.llm_mode, "model": settings.llm_model,
        "dependencies": {name: version(name) for name in ("agntcy-app-sdk", "ioa-observe-sdk", "pydantic")}})
    publish = emit
    async def emit(kind, data):
        if kind == "escalate":
            store.status(run_id, "deadlock")
        data = {**context_headers(), **data}
        record = store.append(run_id, kind, data)
        await publish(kind, {**data, "_sequence": record["sequence"], "_run_id": run_id})

    cancelled = False
    status = "running"
    history: list[dict] = []
    results: list[YearResult] = []
    try:
        await emit("game", {"seed": seed, "years": YEARS, "state": state.public(),
                            "max_rounds": max_rounds or settings.max_rounds,
                            "advisors": public_advisors, "rules": rules})
        while not state.over:
            with span("council.year", run_id=run_id, year=state.year):
                await emit("year_start", {"year": state.year, "state": state.public()})
                before = state
                async def after_vote(round_no, phase, rows, year=state.year):
                    choice = await ask_scout(year, round_no, phase, rows)
                    return apply_scout(rolls, year, choice)

                yr = await deliberate_year(
                    seed=seed,
                    state=state,
                    ask=ask,
                    history=history,
                    max_rounds=max_rounds or settings.max_rounds,
                    scheduled_news=None if ask_scout else scheduled_news(seed, state.year, rolls, reveal_news),
                    injected_news=injected_news,
                    ask_human=ask_human,
                    emit=emit,
                    run_id=run_id,
                    advisors=advisors,
                    after_vote=after_vote if ask_scout else None,
                )
                preview = advance(state, yr.plan, rolls)
                yr.summary = summarize(yr, before, preview, advisors=advisors)
                yr.speech = year_speech(yr, before, preview, advisors=advisors)
                state, event = commit(store, run_id, state, yr, rolls)
                results.append(yr)
                history.append({**event["data"], "result": state.last_report})
                urls = await narrate(yr.year, yr.speech) if narrate else {}
                if yr.human_resolution:
                    for resolution in store.events(run_id, after=event["sequence"] - 2):
                        if resolution["kind"] == "human_resolution":
                            await publish("human_resolution", {**resolution["data"], "_sequence": resolution["sequence"], "_run_id": run_id})
                await publish("year", {**event["data"], "speech_url": urls.get("speech"),
                                    "headline_url": urls.get("headline"),
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
    finally:
        try:
            flush()
            store.import_traces(os.getenv("SUMER_TRACE_PATH", str(CACHE_DIR / "otel-traces.jsonl")))
        except Exception as exc:  # noqa: BLE001 - collection must preserve the authoritative result
            await emit("observability_error", {"year": state.year,
                "message": f"Trace collection failed: {type(exc).__name__}: {exc}"})
    g = _summarize(seed, "council", state, results, advisor_ids=list(advisors))
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


def _summarize(seed, policy, state, results: list[YearResult], advisor_ids=None) -> GameResult:
    advisor_ids = ADVISOR_IDS if advisor_ids is None else advisor_ids
    g = GameResult(seed=seed, policy=policy, rating=rating(state), status=state.status)
    g.years = [{**r.record(), "summary": r.summary} for r in results]
    for r in results:
        g.outcomes[r.outcome] = g.outcomes.get(r.outcome, 0) + 1
    g.mean_rounds = round(sum(r.rounds for r in results) / max(len(results), 1), 2)
    g.mean_turns = round(sum(r.turns for r in results) / max(len(results), 1), 1)
    g.satisfaction = {
        a: round(sum(r.satisfaction[a] for r in results) / max(len(results), 1), 3)
        for a in advisor_ids if a in ADVISOR_IDS
    }
    g.info_shifted = sum(
        1 for r in results
        if r.news and r.after_news and any(r.after_news[a] != r.opening.get(a) for a in r.after_news)
    )
    return g
