"""Same advisor code, called directly. Used by tests and fast benchmarks."""

from __future__ import annotations

from sumer.agent import decide
from sumer.protocol import Ballot, Brief


async def ask(brief: Brief) -> Ballot | None:
    """Deliver a Brief to its one speaker and return that advisor's Ballot."""
    return await decide(brief.speaker, brief)
