"""Rehearse a demo seed with the live LLM so the venue demo replays from cache.

Plays the whole reign in-process (same advisor code and briefs as the SLIM
path), clicks the safe fallback on every escalation, and reports each year's
outcome and, for runoffs, what triggered them.

    LLM_MODE=bayleaf uv run python scripts/rehearse.py 7 34
"""

from __future__ import annotations

import argparse
import asyncio
import os

os.environ.setdefault("LLM_MODE", "bayleaf")
os.environ["TRANSPORT"] = "inproc"

from sumer import llm
from sumer.chamber import play_council
from sumer.transport import inproc


async def safe(options: list[dict], reason: str) -> str:
    return "safe"


async def rehearse(seed: int) -> str:
    g = await play_council(seed, inproc.ask, ask_human=safe)
    years = " | ".join(
        f"y{y['year']} {y['outcome']}"
        + (f"({y.get('runoff_trigger')})" if y["outcome"] == "runoff" else "")
        for y in g.years
    )
    return f"seed {seed}: {years} | score {g.rating['score']}"


async def main(seeds: list[int]) -> None:
    for line in await asyncio.gather(*(rehearse(s) for s in seeds)):
        print(line)
    print(f"LLM spend so far: ${llm.spent_usd():.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("seeds", type=int, nargs="+")
    asyncio.run(main(ap.parse_args().seeds))
