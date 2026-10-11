"""Plays whole games: the council (over a transport) or a baseline."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

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
from sumer.config import settings
from sumer.deliberation import Ask, AskHuman, Emit, YearResult, _noop_emit, deliberate_year
from sumer.game import YEARS, advance, new_game, rating
from sumer.narrator import summarize

# Turns the narrator's line for a year into audio; returns its URL or None.
Narrate = Callable[[int, str], Awaitable[str | None]]


@dataclass
class GameResult:
    seed: int
    policy: str
    rating: dict
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
) -> GameResult:
    state, rolls = new_game(seed)
    history: list[dict] = []
    results: list[YearResult] = []
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
        )
        state = advance(state, yr.plan, rolls)
        yr.summary = summarize(yr, before, state)
        results.append(yr)
        history.append({**yr.record(), "result": state.last_report})
        url = await narrate(yr.year, yr.summary) if narrate else None
        await emit("year", {**yr.record(), "summary": yr.summary, "narration_url": url,
                            "satisfaction": yr.satisfaction, "runoff": yr.runoff,
                            "human_choice": yr.human_choice,
                            "opening": {k: v.model_dump() for k, v in yr.opening.items()},
                            "after_news": {k: v.model_dump() for k, v in yr.after_news.items()},
                            "news_items": yr.news,
                            "report": state.last_report, "state": state.public()})
    g = _summarize(seed, "council", state, results)
    await emit("end", g.as_dict())
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
    g = GameResult(seed=seed, policy=policy, rating=rating(state))
    g.satisfaction = {a: round(sum(s[a] for s in sats) / len(sats), 3) for a in ADVISOR_IDS}
    return g


def _summarize(seed, policy, state, results: list[YearResult]) -> GameResult:
    g = GameResult(seed=seed, policy=policy, rating=rating(state))
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
