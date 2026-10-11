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

import base64
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from sumer.advisors import ADVISOR_IDS
from sumer.config import CACHE_DIR
from sumer.game import Decision, State, legalize


def uid() -> str:
    return uuid.uuid4().hex


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def state_hash(state: State | dict) -> str:
    return digest(state.public() if isinstance(state, State) else state)


def endpoint(peer: str) -> str:
    return f"sumer/council/{peer}"


def identity_dir() -> Path:
    return Path(os.getenv("SUMER_IDENTITY_DIR", str(CACHE_DIR / "identities")))


def provision_identities() -> dict[str, str]:
    """Provision distinct local credentials, independent of the SLIM shared secret."""
    directory = identity_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    public = {}
    for peer in ADVISOR_IDS:
        path = directory / f"{peer}.key"
        if not path.exists():
            raw = Ed25519PrivateKey.generate().private_bytes(
                serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
            try:
                with path.open("xb") as f:
                    f.write(raw)
                path.chmod(0o600)
            except FileExistsError:
                pass
        key = Ed25519PrivateKey.from_private_bytes(path.read_bytes())
        public[peer] = base64.b64encode(key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )).decode()
    return public


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
    peer_keys: dict[str, str] = Field(default_factory=provision_identities)
    errors: dict[str, str] = Field(default_factory=dict)
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
    sig: str = ""

    def signing_payload(self) -> bytes:
        body = self.model_dump(exclude={"sig"})
        return json.dumps(body, sort_keys=True).encode()


def bind(ballot: Ballot, brief: Brief) -> Ballot:
    return ballot.model_copy(update={"run_id": brief.run_id, "state_hash": brief.state_hash,
                                     "reply_to": brief.message_id,
                                     "request_hash": digest(brief.model_dump())})


def sign(ballot: Ballot) -> Ballot:
    provision_identities()
    key = Ed25519PrivateKey.from_private_bytes((identity_dir() / f"{ballot.advisor}.key").read_bytes())
    return ballot.model_copy(update={"sig": base64.b64encode(key.sign(ballot.signing_payload())).decode()})


def verify(ballot: Ballot, keys: dict[str, str] | None = None) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode((keys or provision_identities())[ballot.advisor], validate=True))
        key.verify(base64.b64decode(ballot.sig, validate=True), ballot.signing_payload())
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False


@dataclass
class Delivery:
    peer: str
    origin: str
    reply: Ballot | None = None
    error: str | None = None


def validate_reply(delivery: Delivery | Ballot | None, brief: Brief) -> Ballot:
    if isinstance(delivery, Delivery):
        if delivery.peer != brief.speaker or delivery.origin != endpoint(brief.speaker):
            raise ValueError("Wrong transport endpoint.")
        ballot = delivery.reply
        if ballot is None:
            raise ValueError(delivery.error or "Missing response.")
    else:
        ballot = delivery
    if ballot is None or ballot.advisor != brief.speaker or not verify(ballot, brief.peer_keys):
        raise ValueError("Missing response or invalid peer signature.")
    if (ballot.run_id, ballot.year, ballot.round, ballot.phase, ballot.state_hash,
        ballot.reply_to, ballot.request_hash) != (
        brief.run_id, brief.year, brief.round, brief.phase, brief.state_hash,
        brief.message_id, digest(brief.model_dump())):
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
