"""Same advisor code, called directly. Used by tests and fast benchmarks."""

from __future__ import annotations

import asyncio

from sumer.advisors import ADVISOR_IDS
from sumer.agent import decide
from sumer.protocol import Ballot, Brief


async def gather(brief: Brief) -> list[Ballot]:
    return list(await asyncio.gather(*(decide(a, brief) for a in ADVISOR_IDS)))
