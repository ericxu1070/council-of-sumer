"""Explicit, bounded council phases; a clerk validates messages but never selects budgets."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sumer.advisors import ADVISOR_IDS
from sumer.game import Decision, State
from sumer.observability import context_headers, span
from sumer.protocol import (
    Brief,
    Candidate,
    Certificate,
    Delivery,
    Reply,
    state_hash,
    uid,
    validate_reply,
    verify_certificate,
)

Emit = Callable[[str, dict], Awaitable[None]]
Gather = Callable[[Brief], Awaitable[list[Delivery]]]
AskHuman = Callable[[list[dict], str], Awaitable[str | None]]


class TechnicalFailure(Exception):
    def __init__(self, message: str, **details):
        super().__init__(message)
        self.details = details


class Deadlock(Exception):
    pass


@dataclass
class YearResult:
    year: int
    candidate: Candidate
    rounds: int
    certificate: Certificate | None = None
    human_resolution: dict | None = None
    transcript: list[dict] = field(default_factory=list)

    @property
    def plan(self) -> Decision:
        return self.candidate.plan.decision()

    def summary(self) -> dict:
        ballots = self.certificate.ballots if self.certificate else []
        yes = [b.advisor for b in ballots if b.choice == "YES"]
        no = [b.advisor for b in ballots if b.choice == "NO"]
        return {
            "year": self.year,
            "outcome": "human_resolution" if self.human_resolution else (
                "consensus" if len(yes) == 4 else "majority"),
            "plan": self.plan.as_dict(), "adopted": self.candidate.id,
            "candidate": self.candidate.model_dump(), "rounds": self.rounds,
            "yes_voters": yes, "dissent": no,
            "certificate": self.certificate.model_dump() if self.certificate else None,
            "human_resolution": self.human_resolution,
            "transcript": self.transcript,
        }


async def _noop_emit(kind: str, data: dict) -> None:
    pass


async def collect(brief: Brief, gather: Gather, emit: Emit) -> list[Reply]:
    """Keep valid responses; retry only failed peers once with a fresh request binding."""
    accepted: dict[str, Reply] = {}
    errors: dict[str, str] = {}
    for attempt in (1, 2):
        request = brief.model_copy(update={
            "message_id": uid(), "targets": [p for p in ADVISOR_IDS if p not in accepted],
            "errors": dict(errors),
        })
        with span("council.phase", run_id=brief.run_id, year=brief.year, phase=brief.phase,
                  cycle=brief.round, attempt=attempt,
                  candidate_hash=brief.candidate.hash if brief.candidate else None):
            request = request.model_copy(update={
                "traceparent": context_headers().get("traceparent", "")})
            await emit("phase", {
                "year": brief.year, "round": brief.round, "phase": brief.phase,
                "attempt": attempt, "targets": request.targets,
                "request_id": request.message_id,
                "candidate": brief.candidate.model_dump() if brief.candidate else None,
                **context_headers(),
            })
            try:
                deliveries = await asyncio.wait_for(gather(request), timeout=120)
            except Exception as exc:  # noqa: BLE001 - transport errors become recorded retry failures
                deliveries = []
                errors = {p: f"{type(exc).__name__}: {exc}" for p in request.targets}
            grouped: dict[str, list[Delivery]] = {}
            for delivery in deliveries:
                grouped.setdefault(delivery.peer, []).append(delivery)
                if delivery.peer not in request.targets:
                    await emit("validation_error", {
                        "year": brief.year, "phase": brief.phase, "attempt": attempt,
                        "peer": delivery.peer, "message": "Unexpected or duplicate peer response."})
            for peer in request.targets:
                responses = grouped.get(peer, [])
                try:
                    if len(responses) != 1:
                        raise ValueError(errors.get(peer) or
                                         f"Expected one response, received {len(responses)}.")
                    with span("message.verify", parent=responses[0].reply.traceparent
                              if responses[0].reply else "", run_id=brief.run_id,
                              year=brief.year, phase=brief.phase, peer=peer,
                              request_id=request.message_id,
                              message_id=responses[0].reply.message_id
                              if responses[0].reply else None,
                              candidate_hash=brief.candidate.hash if brief.candidate else None):
                        reply = validate_reply(responses[0], request)
                    if reply.message_id in {r.message_id for r in accepted.values()}:
                        raise ValueError("Duplicate response message ID.")
                    accepted[peer] = reply
                    errors.pop(peer, None)
                    # Receipts are durable even if a different peer ultimately fails.
                    # They are not added to another agent's transcript before the barrier.
                    await emit("receipt", {"year": brief.year, "responses": [reply.model_dump()]})
                except ValueError as exc:
                    errors[peer] = str(exc)
                    await emit("validation_error", {
                        "year": brief.year, "round": brief.round, "phase": brief.phase,
                        "attempt": attempt, "peer": peer, "message": str(exc),
                        "request_id": request.message_id,
                        "responses": [d.reply.model_dump() for d in responses if d.reply],
                        "response": responses[0].reply.model_dump()
                        if len(responses) == 1 and responses[0].reply else None})
        if len(accepted) == 4:
            ordered = [accepted[p] for p in ADVISOR_IDS]
            if brief.phase != "vote":
                await emit("responses", {
                    "year": brief.year, "round": brief.round, "phase": brief.phase,
                    "responses": [r.model_dump() for r in ordered]})
            return ordered
    raise TechnicalFailure(f"{brief.phase} failed after one retry: {errors}",
                           phase=brief.phase, round=brief.round, attempt=2,
                           peers=list(errors), errors=errors, request_id=request.message_id,
                           candidate_hash=brief.candidate.hash if brief.candidate else None)


def candidates(replies: list[Reply]) -> list[Candidate]:
    return [Candidate(
        id=uid(), run_id=r.run_id, year=r.year, revision=r.round, author=r.advisor,
        state_hash=r.state_hash, plan=r.proposal, assumed_price=r.assumed_price,
        speech=r.speech, arithmetic=r.arithmetic, assumptions=r.assumptions,
        expected_effects=r.expected_effects, objections_addressed=r.objections_addressed,
        source_message_id=r.message_id,
    ) for r in replies]


async def deliberate_year(
    *, run_id: str, state: State, gather: Gather, history: list[dict],
    peer_keys: dict[str, str], max_rounds: int,
    ask_human: AskHuman | None = None, emit: Emit = _noop_emit,
) -> YearResult:
    if max_rounds < 1:
        raise ValueError("At least one negotiation cycle is required.")
    transcript: list[dict] = []
    table: list[Candidate] = []

    async def phase(kind: str, cycle: int, candidate: Candidate | None = None):
        brief = Brief(run_id=run_id, year=state.year, round=cycle, max_rounds=max_rounds,
                      phase=kind, state=state.public(), state_hash=state_hash(state),
                      table=table, candidate=candidate, history=history,
                      transcript=list(transcript), peer_keys=peer_keys)
        with span(f"council.{kind}", run_id=run_id, year=state.year, phase=kind,
                  cycle=cycle, candidate_hash=candidate.hash if candidate else None):
            replies = await collect(brief, gather, emit)
        transcript.extend(r.model_dump() for r in replies)
        return replies

    table = candidates(await phase("proposal", 0))
    await emit("candidates", {"year": state.year, "round": 0,
                              "candidates": [c.model_dump() for c in table]})
    failed: list[dict] = []
    for cycle in range(1, max_rounds + 1):
        await phase("objection", cycle)
        table = candidates(await phase("revision", cycle))
        await emit("candidates", {"year": state.year, "round": cycle,
                                  "candidates": [c.model_dump() for c in table]})
        # Every frozen candidate gets its own complete four-peer ballot.
        for candidate in table:
            with span("council.ballot", run_id=run_id, year=state.year, phase="vote",
                      cycle=cycle, candidate_hash=candidate.hash):
                ballots = await phase("vote", cycle, candidate)
            yes = [b.advisor for b in ballots if b.choice == "YES"]
            no = [b.advisor for b in ballots if b.choice == "NO"]
            passing = len(yes) >= 3
            record = {
                "message_id": uid(), "kind": "passed_ballot" if passing else "failed_ballot",
                "year": state.year, "round": cycle, "candidate": candidate.model_dump(),
                "candidate_hash": candidate.hash, "ballots": [b.model_dump() for b in ballots],
                "yes_voters": yes, "dissent": no, "passing": passing, "verified": 4,
            }
            await emit("ballots", record)
            transcript.append(record)
            if passing:
                cert = Certificate(candidate=candidate, ballots=ballots)
                verify_certificate(cert, state, run_id, peer_keys)
                return YearResult(state.year, candidate, cycle, certificate=cert,
                                  transcript=list(transcript))
            failed.append(record)

    reason = f"No candidate received three YES votes in {max_rounds} completed cycles."
    options = [c.model_dump() for c in table]
    await emit("escalate", {
        "year": state.year, "reason": reason, "options": options,
        "failed_ballots": failed, "state_hash": state_hash(state)})
    if ask_human is None:
        raise Deadlock(reason)
    with span("council.human_resolution", run_id=run_id, year=state.year) as human_span:
        choice = await ask_human(options, reason)
        if choice is None:
            raise Deadlock(reason)
        chosen = next((c for c in table if c.id == choice), None)
        if chosen is None:
            raise TechnicalFailure("Human selection does not identify a latest valid proposal.",
                                   phase="human_resolution", round=max_rounds)
        if human_span is not None:
            human_span.set_attribute("sumer.candidate_hash", chosen.hash)
        resolution = {
            "id": uid(), "run_id": run_id, "year": state.year, "candidate_id": chosen.id,
            "candidate_hash": chosen.hash, "state_hash": state_hash(state),
            "reason": reason, "source": "human", "human_assisted": True, **context_headers(),
        }
    return YearResult(state.year, chosen, max_rounds, human_resolution=resolution,
                      transcript=list(transcript))
