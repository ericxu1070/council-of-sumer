"""Existing offline comparison policies. None supplies live council guidance."""

from __future__ import annotations

import json

from sumer import llm
from sumer.advisors import ADVISOR_IDS, ADVISORS, Beliefs, Scorer
from sumer.config import settings
from sumer.game import (
    ACRES_PER_BUSHEL,
    ACRES_PER_WORKER,
    FOOD_PER_PERSON,
    Decision,
    State,
    legalize,
)


def lone_ruler(state: State, beliefs: Beliefs) -> Decision:
    """One decider that weighs all default mandates equally, in one shot, no debate."""
    sc = Scorer(state, beliefs)
    return max(sc.pool, key=lambda d: sum(sc.norm(a, d) for a in ADVISOR_IDS))


def average_of_openings(state: State, beliefs: Beliefs) -> Decision:
    """Each advisor names its selfish best plan; the city does the average. No negotiation."""
    sc = Scorer(state, beliefs)
    picks = [max(sc.pool, key=lambda d, a=a: sc.norm(a, d)) for a in ADVISOR_IDS]
    n = len(picks)
    land = min(round(sum(p.land_delta for p in picks) / n), state.grain // state.land_price)
    available = state.grain - land * state.land_price
    feed = min(round(sum(p.feed for p in picks) / n), available)
    plant = min(round(sum(p.plant for p in picks) / n), state.acres + land,
                state.population * 10, (available - feed) * 2 + 1)
    return legalize(state, Decision(land, feed, plant))


def dictator(advisor_id: str):
    def decide(state: State, beliefs: Beliefs) -> Decision:
        sc = Scorer(state, beliefs)
        return max(sc.pool, key=lambda d: sc.norm(advisor_id, d))

    decide.__name__ = f"dictator_{advisor_id}"
    return decide


async def lone_llm(state: State, beliefs: Beliefs, news: list[dict],
                   stats: dict | None = None) -> Decision:
    """Single LLM call with all default mandates in one prompt.

    It sees the same budget rules as the council's advisors and, like an
    advisor, gets one corrected retry when its plan breaks the budget. Only a
    plan that is still invalid falls back to lone_ruler; `stats["fallbacks"]`
    counts those years so the benchmark can report them."""
    if settings.llm_mode != "bayleaf":
        return lone_ruler(state, beliefs)
    mandates = "\n".join(f"- {a.title}: {a.mandate}" for a in ADVISORS.values())
    s = state.public()
    budget_rules = {
        "workforce_limit_acres": s["population"] * ACRES_PER_WORKER,
        "food_for_all_residents_bushels": s["population"] * FOOD_PER_PERSON,
        "grain_after_trade": f"{s['grain']} - land_delta * {s['land_price']}",
        "acres_after_trade": f"{s['acres']} + land_delta",
        "grain_after_seed": "grain_after_trade - feed - plant // 2 must be >= 0",
        "max_plant": "min(acres_after_trade, workforce_limit_acres, "
                     f"{ACRES_PER_BUSHEL} * (grain_after_trade - feed + 1) - 1)",
    }
    msgs = [
        {
            "role": "system",
            "content": "You rule ancient Sumer (the game Hamurabi). People eat 20 bushels "
            "each per year, sowing costs 1 bushel per 2 acres, each person tends 10 acres. "
            f"Balance these goals:\n{mandates}\nReply with ONLY JSON "
            '{"land_delta": int, "feed": int, "plant": int}.',
        },
        {
            "role": "user",
            "content": json.dumps(
                {"city": s, "beliefs": beliefs.__dict__,
                 "news": [n["text"] for n in news], "budget_rules": budget_rules}
            ),
        },
    ]
    for attempt in range(2):
        try:
            reply = (await llm.chat(msgs)).content
        except llm.LLMUnavailable:
            break
        try:
            p = llm.parse_json(reply)
            return legalize(state, Decision(int(p["land_delta"]), int(p["feed"]), int(p["plant"])))
        except (ValueError, KeyError, TypeError) as e:
            if attempt == 0:
                msgs = [*msgs, {"role": "assistant", "content": reply},
                        {"role": "user", "content": f"That plan is invalid: {e} "
                         "Reply with a corrected plan as ONLY JSON."}]
    if stats is not None:
        stats["fallbacks"] = stats.get("fallbacks", 0) + 1
    return lone_ruler(state, beliefs)


SIMPLE = {
    "lone_ruler": lone_ruler,
    "average": average_of_openings,
    **{f"dictator_{a}": dictator(a) for a in ADVISOR_IDS},
}
