"""The authoritative commit boundary: validate evidence, then advance exactly once."""
from __future__ import annotations

import json
from dataclasses import asdict

from sumer.deliberation import YearResult
from sumer.game import State, YearRolls, advance, legalize
from sumer.observability import context_headers, span
from sumer.protocol import state_hash, verify_certificate
from sumer.storage import Store


def commit(
    store: Store, run_id: str, state: State, result: YearResult, rolls: list[YearRolls],
) -> tuple[State, dict]:
    manifest = store.run(run_id)["manifest"]
    candidate = result.candidate
    recorded = store.db.execute("SELECT data FROM proposals WHERE run_id=? AND hash=?",
                                (run_id, candidate.hash)).fetchone()
    if recorded is None or json.loads(recorded["data"]) != candidate.model_dump():
        raise ValueError("Candidate is not an immutable recorded proposal.")
    if (candidate.run_id, candidate.year, candidate.state_hash) != (
        run_id, state.year, state_hash(state),
    ) or candidate.assumed_price != state.land_price:
        raise ValueError("Candidate is not bound to the current state.")
    if (result.certificate is None) == (result.human_resolution is None):
        raise ValueError("Exactly one passing certificate or human resolution is required.")
    if result.certificate:
        if result.certificate.candidate != candidate:
            raise ValueError("Certificate commits a different candidate.")
        verify_certificate(result.certificate, state, run_id, manifest["peer_keys"])
        for ballot in result.certificate.ballots:
            row = store.db.execute("SELECT data FROM ballots WHERE run_id=? AND id=?",
                                   (run_id, ballot.message_id)).fetchone()
            if row is None or json.loads(row["data"]) != ballot.model_dump():
                raise ValueError("Certificate contains an unrecorded or changed ballot.")
    else:
        resolution = result.human_resolution
        deadlocks = [e["data"] for e in store.events(run_id, year=state.year)
                     if e["kind"] == "escalate"]
        if not deadlocks or candidate.model_dump() not in deadlocks[-1]["options"]:
            raise ValueError("Human selection must be a latest valid deadlock option.")
        if (resolution["run_id"], resolution["year"], resolution["state_hash"],
            resolution["candidate_hash"], resolution["candidate_id"], resolution["source"]) != (
            run_id, state.year, state_hash(state), candidate.hash, candidate.id, "human",
        ):
            raise ValueError("Human resolution has a wrong candidate/state binding.")
    legalize(state, result.plan)
    with span("engine.commit", run_id=run_id, year=state.year, candidate_hash=candidate.hash):
        next_state = advance(state, result.plan, rolls)
        data = {
            **result.summary(), "start_state": state.public(), "state": next_state.public(),
            "report": next_state.last_report,
            "random_inputs": asdict(rolls[state.year - 1]),
            "next_market_quote": next_state.land_price, **context_headers(),
        }
        event = store.commit_year(run_id, data)
    return next_state, event
