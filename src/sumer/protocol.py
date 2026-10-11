"""Immutable phase contracts, independently signed replies, and passing certificates."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from sumer.advisors import ADVISOR_IDS
from sumer.config import CACHE_DIR
from sumer.game import Decision, State, legalize

Phase = Literal["proposal", "objection", "revision", "vote"]


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


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Plan(Contract):
    land_delta: StrictInt
    feed: StrictInt
    plant: StrictInt

    def decision(self) -> Decision:
        return Decision(self.land_delta, self.feed, self.plant)


class Candidate(Contract):
    id: str
    run_id: str
    year: int
    revision: int
    author: str
    state_hash: str
    plan: Plan
    assumed_price: StrictInt
    speech: str
    arithmetic: str = ""
    assumptions: str = ""
    expected_effects: str = ""
    objections_addressed: list[str] = Field(default_factory=list)
    source_message_id: str

    @property
    def hash(self) -> str:
        return digest(self.model_dump())


class Brief(Contract):
    message_id: str = Field(default_factory=uid)
    run_id: str
    year: int
    round: int
    max_rounds: int
    phase: Phase
    state: dict
    state_hash: str
    table: list[Candidate] = Field(default_factory=list)
    candidate: Candidate | None = None
    history: list[dict] = Field(default_factory=list)
    transcript: list[dict] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
    targets: list[str] = Field(default_factory=lambda: list(ADVISOR_IDS))
    peer_keys: dict[str, str]
    traceparent: str = ""


class Reply(Contract):
    message_id: str = Field(default_factory=uid)
    run_id: str
    year: int
    round: int
    phase: Phase
    advisor: str
    state_hash: str
    reply_to: str
    candidate_hash: str | None = None
    proposal: Plan | None = None
    assumed_price: StrictInt | None = None
    choice: Literal["YES", "NO"] | None = None
    speech: str
    arithmetic: str = ""
    assumptions: str = ""
    expected_effects: str = ""
    objections_addressed: list[str] = Field(default_factory=list)
    cited_message_ids: list[str] = Field(default_factory=list)
    source: Literal["llm", "policy"]
    trace: dict = Field(default_factory=dict)
    traceparent: str = ""
    sig: str = ""

    def signing_payload(self) -> bytes:
        return json.dumps(self.model_dump(exclude={"sig"}), sort_keys=True).encode()


def sign(reply: Reply) -> Reply:
    key = Ed25519PrivateKey.from_private_bytes((identity_dir() / f"{reply.advisor}.key").read_bytes())
    signature = base64.b64encode(key.sign(reply.signing_payload())).decode()
    return reply.model_copy(update={"sig": signature})


def verify(reply: Reply, keys: dict[str, str]) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(keys[reply.advisor], validate=True))
        key.verify(base64.b64decode(reply.sig, validate=True), reply.signing_payload())
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False


@dataclass
class Delivery:
    """Transport-owned origin is separate from the untrusted payload sender label."""
    peer: str
    origin: str
    reply: Reply | None = None
    error: str | None = None


class Certificate(Contract):
    candidate: Candidate
    ballots: list[Reply]


def validate_reply(delivery: Delivery, brief: Brief) -> Reply:
    r = delivery.reply
    if delivery.peer not in brief.targets or delivery.origin != endpoint(delivery.peer):
        raise ValueError("Unknown or wrong transport endpoint.")
    if r is None:
        raise ValueError(delivery.error or "Missing response.")
    if r.advisor != delivery.peer or not verify(r, brief.peer_keys):
        raise ValueError("Sender identity or signature does not match the configured peer.")
    if (r.run_id, r.year, r.round, r.phase, r.state_hash, r.reply_to) != (
        brief.run_id, brief.year, brief.round, brief.phase, brief.state_hash, brief.message_id,
    ):
        raise ValueError("Stale response or wrong run/state/phase.")
    expected = brief.candidate.hash if brief.candidate else None
    if r.candidate_hash != expected:
        raise ValueError("Wrong candidate.")
    if not r.speech.strip():
        raise ValueError("A decision explanation is required.")
    if brief.phase in ("proposal", "revision"):
        if r.proposal is None or r.assumed_price != brief.state["land_price"] or r.choice is not None:
            raise ValueError("A complete budget and the known current price are required.")
        public = dict(brief.state)
        public.pop("acres_per_person", None)
        legalize(State(**public), r.proposal.decision())
        if not all(t.strip() for t in (r.arithmetic, r.assumptions, r.expected_effects)):
            raise ValueError("Budget arithmetic, assumptions, and expected effects are required.")
    elif brief.phase == "vote":
        if r.choice not in ("YES", "NO") or r.proposal is not None:
            raise ValueError("Ballot must contain exactly one YES or NO choice.")
    elif r.proposal is not None or r.choice is not None:
        raise ValueError("Objections cannot change budgets or cast ballots.")
    known = {e.get("message_id") for e in brief.transcript}
    if any(m not in known for m in r.cited_message_ids + r.objections_addressed):
        raise ValueError("Cited message IDs must refer to the public transcript.")
    if brief.phase == "revision" and brief.transcript and not r.objections_addressed:
        raise ValueError("A revision must address a recorded objection or failed ballot.")
    return r


def verify_certificate(cert: Certificate, state: State, run_id: str, keys: dict[str, str]) -> None:
    c = cert.candidate
    if c.run_id != run_id or c.year != state.year or c.state_hash != state_hash(state):
        raise ValueError("Certificate does not bind to the current run and state.")
    if c.author not in ADVISOR_IDS or c.assumed_price != state.land_price:
        raise ValueError("Unknown candidate author or wrong market price.")
    if len(cert.ballots) != 4 or {b.advisor for b in cert.ballots} != set(ADVISOR_IDS):
        raise ValueError("Certificate requires four distinct configured voters.")
    if len({b.message_id for b in cert.ballots}) != 4:
        raise ValueError("Ballot message IDs must be distinct.")
    for b in cert.ballots:
        if not verify(b, keys) or not b.speech.strip() or b.proposal is not None:
            raise ValueError("Invalid ballot signature or content.")
        if (b.run_id, b.year, b.round, b.phase, b.state_hash, b.candidate_hash) != (
            run_id, c.year, c.revision, "vote", c.state_hash, c.hash,
        ) or b.choice not in ("YES", "NO"):
            raise ValueError("Wrong ballot binding.")
    if sum(b.choice == "YES" for b in cert.ballots) < 3:
        raise ValueError("Certificate does not contain three YES votes.")
    legalize(state, c.plan.decision())
