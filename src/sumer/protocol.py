"""Wire format for the council, carried as JSON inside A2A text messages.

Each round the clerk broadcasts a Brief to every advisor; each advisor answers
with a signed Ballot. The clerk only relays and tallies with a public rule;
it never chooses a plan (see deliberation.py).
"""

from __future__ import annotations

import hashlib
import hmac
import json

from pydantic import BaseModel, Field

from sumer.config import settings


class Plan(BaseModel):
    land_delta: int
    feed: int
    plant: int


class TabledProposal(BaseModel):
    id: str
    author: str
    plan: Plan
    speech: str = ""


class Brief(BaseModel):
    type: str = "brief"
    seed: int
    year: int
    round: int
    max_rounds: int
    state: dict
    beliefs: dict
    news: list[dict] = Field(default_factory=list)
    table: list[TabledProposal] = Field(default_factory=list)
    history: list[dict] = Field(default_factory=list)


class Ballot(BaseModel):
    advisor: str
    year: int
    round: int
    proposal: Plan
    accept: list[str] = Field(default_factory=list)
    veto: list[str] = Field(default_factory=list)
    speech: str = ""
    source: str = "policy"  # "llm" or "policy"
    sig: str = ""

    def signing_payload(self) -> bytes:
        body = self.model_dump(exclude={"sig"})
        return json.dumps(body, sort_keys=True).encode()


def _key(advisor_id: str) -> bytes:
    return hashlib.sha256(f"{settings.slim_secret}:{advisor_id}".encode()).digest()


def sign(ballot: Ballot) -> Ballot:
    ballot.sig = hmac.new(_key(ballot.advisor), ballot.signing_payload(), "sha256").hexdigest()
    return ballot


def verify(ballot: Ballot) -> bool:
    want = hmac.new(_key(ballot.advisor), ballot.signing_payload(), "sha256").hexdigest()
    return hmac.compare_digest(want, ballot.sig)


def proposal_id(advisor: str, year: int, rnd: int) -> str:
    return f"{advisor}-y{year}-r{rnd}"
