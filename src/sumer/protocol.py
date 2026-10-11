"""Wire format for the council, carried as JSON inside A2A text messages.

The council talks in turns. For each turn the clerk sends a Brief to exactly
one advisor (the `speaker`). The Brief carries the table with every plan's
current support, what the earlier speakers said this round, and earlier
rounds. The speaker answers with a Ballot: a proposal (or the plan it
backs), accept and veto votes on the tabled plans, a short public speech, and
a private thought. In a runoff the Ballot carries `runoff_vote` instead.

The clerk only relays and tallies with a public rule; it never chooses a plan
(see deliberation.py).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from sumer.game import Decision, State, legalize
from sumer.observability import span


def uid() -> str:
    return uuid.uuid4().hex


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def state_hash(state: State | dict) -> str:
    return digest(state.public() if isinstance(state, State) else state)


def endpoint(peer: str) -> str:
    return f"sumer/council/{peer}"


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    land_delta: StrictInt
    feed: StrictInt
    plant: StrictInt

    def decision(self):
        return Decision(self.land_delta, self.feed, self.plant)


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
    model_config = ConfigDict(extra="forbid")
    message_id: str = Field(default_factory=uid)
    run_id: str = Field(default_factory=uid)
    state_hash: str = ""
    traceparent: str = ""
    errors: dict[str, str] = Field(default_factory=dict)
    type: str = "brief"
    seed: int
    year: int
    round: int
    max_rounds: int
    state: dict
    beliefs: dict
    advisors: list[dict] = Field(default_factory=list)  # immutable run-local public roster
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


    @model_validator(mode="after")
    def bind_state(self):
        if not self.state_hash:
            self.state_hash = state_hash(self.state)
        return self


class Ballot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str = Field(default_factory=uid)
    run_id: str = ""
    state_hash: str = ""
    reply_to: str = ""
    request_hash: str = ""
    traceparent: str = ""
    error: str | None = None
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


def bind(ballot: Ballot, brief: Brief) -> Ballot:
    return ballot.model_copy(update={"run_id": brief.run_id, "state_hash": brief.state_hash,
                                     "reply_to": brief.message_id,
                                     "request_hash": digest(brief.model_dump())})


def matches_request(ballot: Ballot, brief: Brief) -> bool:
    """Check local turn correlation; transport supplies the addressed endpoint."""
    return (ballot.advisor, ballot.run_id, ballot.year, ballot.round, ballot.phase,
            ballot.state_hash, ballot.reply_to, ballot.request_hash) == (
        brief.speaker, brief.run_id, brief.year, brief.round, brief.phase,
        brief.state_hash, brief.message_id, digest(brief.model_dump()))


@dataclass
class Delivery:
    peer: str
    origin: str
    reply: Ballot | None = None
    error: str | None = None


def validate_reply(delivery: Delivery | Ballot | None, brief: Brief) -> Ballot:
    with span("message.verify", run_id=brief.run_id, year=brief.year, phase=brief.phase, peer=brief.speaker):
        if isinstance(delivery, Delivery):
            if delivery.peer != brief.speaker or delivery.origin != endpoint(brief.speaker):
                raise ValueError("Wrong transport endpoint.")
            ballot = delivery.reply
            if ballot is None:
                raise ValueError(delivery.error or "Missing response.")
        else:
            ballot = delivery
        if ballot is None:
            raise ValueError("Missing response.")
        if not matches_request(ballot, brief):
            raise ValueError("Response does not bind to this run, state, turn, or request.")
        if ballot.error:
            raise ValueError(ballot.error)
        if not ballot.speech.strip():
            raise ValueError("A decision explanation is required.")
        public = {k: v for k, v in brief.state.items() if k not in ("acres_per_person", "starved")}
        legalize(State(**public), ballot.proposal.decision())
        table = {t.id: t for t in brief.table}
        if any(pid not in table for pid in ballot.accept + ballot.veto):
            raise ValueError("Votes refer to an unknown proposal.")
        if ballot.back and (ballot.back not in table or table[ballot.back].plan != ballot.proposal):
            raise ValueError("Backing must identify the unchanged tabled plan.")
        if brief.phase == "runoff":
            pair = {t.id: t for t in brief.runoff}
            if ballot.runoff_vote not in pair or pair[ballot.runoff_vote].plan != ballot.proposal:
                raise ValueError("Runoff vote must bind to the selected frozen finalist.")
            if ballot.runoff_vote in ballot.veto:
                raise ValueError("A runoff ballot cannot vote for its vetoed finalist.")
        elif ballot.runoff_vote is not None:
            raise ValueError("Discussion cannot cast a runoff ballot.")
        return ballot


def proposal_id(advisor: str, year: int, rnd: int) -> str:
    return f"{advisor}-y{year}-r{rnd}"
