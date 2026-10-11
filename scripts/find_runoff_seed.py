"""Find demo seeds where the council settles a year by runoff after a 3-of-7 split.

Plays seeds 1..N with the offline policy minds over the in-process transport
(deterministic, no network) and prints, for every seed with such a runoff,
the runoff years and the reign's outcome counts. Seeds whose runoffs came
from another trigger (a tie at 4 or more, or a red-line veto on the leader)
are listed after, as fallback demo candidates.

    uv run python scripts/find_runoff_seed.py [--seeds 200]
"""

from __future__ import annotations

import argparse
import asyncio
import os

os.environ["LLM_MODE"] = "mock"
os.environ["TRANSPORT"] = "inproc"

from sumer.chamber import play_council
from sumer.transport import inproc

KINDS = ("consensus", "majority", "runoff", "escalated")


async def main(n: int) -> None:
    hits = 0
    totals = dict.fromkeys(KINDS, 0)
    triggers: dict[str, int] = {}
    others: list[str] = []
    for seed in range(1, n + 1):
        g = await play_council(seed, inproc.ask)
        for k in KINDS:
            totals[k] += g.outcomes.get(k, 0)
        runoffs = [y for y in g.years if y["outcome"] == "runoff"]
        for y in runoffs:
            triggers[y["runoff_trigger"]] = triggers.get(y["runoff_trigger"], 0) + 1
        three = [y["year"] for y in runoffs if y["runoff_trigger"] == "three"]
        counts = ", ".join(f"{k} {g.outcomes.get(k, 0)}" for k in KINDS)
        escalated = "yes" if g.outcomes.get("escalated") else "no"
        if not three:
            if runoffs:
                years = [(y["year"], y["runoff_trigger"]) for y in runoffs]
                others.append(f"seed {seed:3d}: runoff in {years} | {counts} | "
                              f"escalation: {escalated} | score {g.rating['score']}")
            continue
        hits += 1
        print(f"seed {seed:3d}: 3-of-7 runoff in year(s) {three} | {counts} | "
              f"escalation: {escalated} | score {g.rating['score']}")
    print(f"\n{hits} of {n} seeds have a runoff triggered by a 3-of-7 best plan.")
    if others:
        print("\nOther runoff seeds (fallback candidates):")
        print("\n".join(others))
    print("All years:", ", ".join(f"{k} {v}" for k, v in totals.items()))
    print("Runoffs by trigger:", triggers or "none")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=200)
    asyncio.run(main(ap.parse_args().seeds))
