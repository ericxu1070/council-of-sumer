"""Wire format for the council, carried as JSON inside A2A text messages.

The council talks in turns. For each turn the clerk sends a Brief to exactly
one advisor (the `speaker`). The Brief carries the table with every plan's
current support, what the earlier speakers said this round, and earlier
rounds. The speaker answers with a signed Ballot: a proposal (or the plan it
backs), accept and veto votes on the tabled plans, a short public speech, and
a private thought. In a runoff the Ballot carries `runoff_vote` instead.

The clerk only relays and tallies with a public rule; it never chooses a plan
(see deliberation.py).
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
    plain: str = ""
    accept: list[str] = Field(default_factory=list)  # advisors accepting it right now
    veto: list[str] = Field(default_factory=list)
    backers: list[str] = Field(default_factory=list)  # advisors whose proposal this is


class Brief(BaseModel):
    type: str = "brief"
    seed: int
    year: int
    round: int
    max_rounds: int
    state: dict
    beliefs: dict
    speaker: str = ""  # the one advisor who must answer this brief
    phase: str = "discuss"  # "discuss" or "runoff"
    turn: int = 0  # position of the speaker in this round's order
    order: list[str] = Field(default_factory=list)  # this round's speaking order
    news: list[dict] = Field(default_factory=list)
    table: list[TabledProposal] = Field(default_factory=list)
    turns_this_round: list[dict] = Field(default_factory=list)
    earlier_turns: list[dict] = Field(default_factory=list)  # earlier rounds this year
    runoff: list[TabledProposal] = Field(default_factory=list)  # [A, B] in a runoff
    history: list[dict] = Field(default_factory=list)


class Ballot(BaseModel):
    advisor: str
    year: int
    round: int
    phase: str = "discuss"
    proposal: Plan
    back: str | None = None  # id of a tabled plan this advisor backs as its proposal
    accept: list[str] = Field(default_factory=list)
    veto: list[str] = Field(default_factory=list)
    runoff_vote: str | None = None
    speech: str = ""  # public: the other advisors hear this
    addressed: list[str] = Field(default_factory=list)  # advisors the speech names
    source: str = "policy"  # "llm" or "policy"
    # Glass box: private reasoning. Humans see it; the other advisors never do.
    trace: dict = Field(default_factory=dict)
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
