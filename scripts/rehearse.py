"""Rehearse demo seeds with the live LLM so the venue demo replays from cache.

Plays each whole reign in-process (same advisor code and briefs as the SLIM
path), clicks the safe fallback on every escalation (as DEMO.md tells the
presenter to), and prints one line per year: outcome, runoff trigger, rounds,
turns, and LLM fallbacks.

A seed is only safe to demo if no live LLM call failed: a failed call falls
back to the policy mind, which is not cached, so the next replay would make a
fresh call and could diverge. Such seeds are marked UNUSABLE. Rehearse them
again (the cached turns cost nothing) until they come back clean.

Seeds run in small parallel batches to stay clear of API timeouts.

    uv run python scripts/rehearse.py 7 34 95 --batch 3
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections import Counter

os.environ.setdefault("LLM_MODE", "bayleaf")
os.environ["TRANSPORT"] = "inproc"

from sumer import llm
from sumer.chamber import play_council
from sumer.config import settings
from sumer.transport import inproc


async def safe(options: list[dict], reason: str) -> str:
    return "safe"


async def rehearse(seed: int, max_rounds: int, reveal: bool) -> str:
    fallbacks: Counter = Counter()  # (year, kind) -> count
    calls: Counter = Counter()  # "cached" / "live"
    cost = 0.0

    async def ask(brief):
        nonlocal cost
        b = await inproc.ask(brief)
        kind = b.trace.get("fallback_kind") if b else "missing"
        if kind:
            fallbacks[brief.year, kind] += 1
        meta = (b.trace.get("llm") or {}) if b else {}
        if meta:
            calls["cached" if meta.get("cached") else "live"] += 1
            cost += meta.get("cost_usd", 0.0)
        return b

    g = await play_council(seed, ask, ask_human=safe, max_rounds=max_rounds, reveal_news=reveal)
    lines = []
    for y in g.years:
        trig = y.get("runoff_trigger") or ""
        fb = ", ".join(f"{k} {n}" for (yr, k), n in sorted(fallbacks.items()) if yr == y["year"])
        lines.append(
            f"  y{y['year']}  {y['outcome']:<9} {trig:<6} rounds {y['rounds']}  "
            f"turns {y['turns']:>2}  fallbacks: {fb or 'none'}"
        )
    unusable = any(k in ("failed", "missing", "unavailable") for (_, k) in fallbacks)
    three = sum(1 for y in g.years if y.get("runoff_trigger") == "three")
    turns = sum(y["turns"] for y in g.years)
    head = (
        f"seed {seed}: score {g.rating['score']} | {dict(g.outcomes)} | 3-of-7 runoffs {three} | "
        f"turns {turns} (mean {turns / max(len(g.years), 1):.1f}/yr) | "
        f"calls {calls['live']} live, {calls['cached']} cached, ${cost:.3f} | "
        + ("UNUSABLE: a live call failed, rehearse again" if unusable else "replayable")
    )
    return "\n".join([head, *lines])


async def main(seeds: list[int], batch: int, max_rounds: int, reveal: bool) -> None:
    start = llm.spent_usd()
    for i in range(0, len(seeds), batch):
        for out in await asyncio.gather(*(rehearse(s, max_rounds, reveal)
                                          for s in seeds[i:i + batch])):
            print(out, flush=True)
    print(f"Spent this run: ${llm.spent_usd() - start:.3f}. "
          f"Total so far: ${llm.spent_usd():.3f} of ${settings.llm_budget_usd:.2f}.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("seeds", type=int, nargs="+")
    ap.add_argument("--batch", type=int, default=3, help="seeds played at once")
    ap.add_argument("--rounds", type=int, default=settings.max_rounds,
                    help="must match the Rounds setting in the UI")
    ap.add_argument("--no-news", action="store_true", help="match Scouts: off in the UI")
    a = ap.parse_args()
    asyncio.run(main(a.seeds, a.batch, a.rounds, not a.no_news))
