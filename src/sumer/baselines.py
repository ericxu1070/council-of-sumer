"""Simpler deciders to compare the council against. All see the same news."""

from __future__ import annotations

import json

from sumer import llm
from sumer.advisors import ADVISOR_IDS, ADVISORS, Beliefs, Scorer
from sumer.config import settings
from sumer.game import Decision, State, legalize


def lone_ruler(state: State, beliefs: Beliefs) -> Decision:
    """One decider that weighs all seven mandates equally, in one shot, no debate."""
    sc = Scorer(state, beliefs)
    ok = [d for d in sc.pool if not any(sc.vetoes(a, d) for a in ADVISOR_IDS)] or sc.pool
    return max(ok, key=lambda d: sum(sc.norm(a, d) for a in ADVISOR_IDS))


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


async def lone_llm(state: State, beliefs: Beliefs, news: list[dict]) -> Decision:
    """Single LLM call with all seven mandates in one prompt. Falls back to lone_ruler."""
    if settings.llm_mode != "bayleaf":
        return lone_ruler(state, beliefs)
    mandates = "\n".join(f"- {a.title}: {a.mandate}" for a in ADVISORS.values())
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
                {"city": state.public(), "beliefs": beliefs.__dict__,
                 "news": [n["text"] for n in news]}
            ),
        },
    ]
    try:
        p = llm.parse_json((await llm.chat(msgs)).content)
        return legalize(state, Decision(int(p["land_delta"]), int(p["feed"]), int(p["plant"])))
    except (llm.LLMUnavailable, ValueError, KeyError, TypeError):
        return lone_ruler(state, beliefs)


SIMPLE = {
    "lone_ruler": lone_ruler,
    "average": average_of_openings,
    **{f"dictator_{a}": dictator(a) for a in ADVISOR_IDS},
}
