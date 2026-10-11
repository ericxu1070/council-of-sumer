"""The same signed phase contracts, called directly for tests and offline fixtures."""
from __future__ import annotations

import asyncio

from sumer.agent import decide
from sumer.protocol import Brief, Delivery, endpoint


async def gather(brief: Brief) -> list[Delivery]:
    async def one(peer: str):
        try:
            return Delivery(peer, endpoint(peer), reply=await decide(peer, brief))
        except Exception as exc:  # noqa: BLE001 - transport boundary returns failures to the clerk
            return Delivery(peer, endpoint(peer), error=f"{type(exc).__name__}: {exc}")
    return list(await asyncio.gather(*(one(peer) for peer in brief.targets)))
