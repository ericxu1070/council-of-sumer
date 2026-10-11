import asyncio
import copy
import json
import sqlite3
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sumer import agent, llm
from sumer.chamber import play_council
from sumer.deliberation import deliberate_year
from sumer.engine import commit
from sumer.game import State, new_game
from sumer.protocol import (
    Delivery,
    Plan,
    bind,
    endpoint,
    validate_reply,
)
from sumer.storage import Store
from tests.support import unanimous


@pytest.mark.parametrize("amount", [2000.5, True])
def test_budgets_require_whole_numbers(amount):
    with pytest.raises(ValidationError):
        Plan(land_delta=0, feed=amount, plant=500)


async def test_turn_retries_only_its_speaker_with_a_fresh_request():
    seen = []
    async def ask(brief):
        seen.append(brief)
        ballot = await unanimous(brief)
        return ballot.model_copy(update={"request_hash": "stale-request"}) if len(seen) == 1 else ballot
    state = State()
    result = await deliberate_year(seed=1, state=state, ask=ask, history=[], max_rounds=3,
                                   scheduled_news=None)
    assert result.outcome == "consensus"
    assert seen[0].speaker == seen[1].speaker
    assert seen[0].message_id != seen[1].message_id and seen[1].errors
    assert len(result.certificate["ballots"]) == 7
    reply = await unanimous(seen[-1])
    with pytest.raises(ValueError, match="transport endpoint"):
        validate_reply(Delivery(seen[-1].speaker, endpoint("unknown"), reply), seen[-1])
    with pytest.raises(ValueError, match="request"):
        validate_reply(bind(reply, seen[0]), seen[-1])


async def test_engine_rechecks_evidence_and_advances_only_once():
    state, rolls = new_game(1)
    store = Store(":memory:")
    manifest = {"initial_state": state.public(), "max_rounds": 3}
    store.create_run("proof", manifest)
    async def emit(kind, data):
        store.append("proof", kind, data)
    result = await deliberate_year(seed=1, run_id="proof", state=state, ask=unanimous,
        history=[], max_rounds=3, scheduled_news=None, emit=emit)
    bad = copy.deepcopy(result)
    bad.certificate["ballots"].pop()
    with pytest.raises(ValueError, match="all seven"):
        commit(store, "proof", state, bad, rolls)
    missing_requests = Store(":memory:")
    missing_requests.create_run("proof", manifest)
    for event in store.events("proof"):
        if event["kind"] != "phase":
            missing_requests.append("proof", event["kind"], event["data"])
    with pytest.raises(ValueError, match="unrecorded request"):
        commit(missing_requests, "proof", state, result, rolls)
    advanced, event = commit(store, "proof", state, result, rolls)
    assert advanced.year == 2 and event["data"]["plan"] == result.plan.as_dict()
    with pytest.raises(ValueError, match="advanced"):
        commit(store, "proof", state, result, rolls)
    assert store.db.execute("SELECT COUNT(*) FROM years").fetchone()[0] == 1
    for table in ("events", "proposals", "ballots", "years"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.db.execute(f"DELETE FROM {table}")


@pytest.mark.parametrize("empty", [False, True])
async def test_live_model_failure_is_not_a_policy_vote_and_keeps_cost(monkeypatch, empty):
    monkeypatch.setattr(agent, "settings", replace(agent.settings, llm_mode="bayleaf"))
    usage = {"model": "test", "cost_usd": 0.01}
    prompts = []
    async def broken(*args, **kwargs):
        prompts.append(str(args[0]))
        if empty:
            raise llm.LLMUnavailable("empty reply", usage=usage)
        return llm.Reply('{"proposal":', "test", False, 0.1, 0.01)
    monkeypatch.setattr(llm, "chat", broken)
    store = Store(":memory:")
    from sumer.transport import inproc
    result = await play_council(1, inproc.ask, store=store)
    assert result.status == "technical_failure" and not result.years
    assert len(prompts) == 2 and "response_correction" in prompts[1]
    recorded = [json.loads(r["data"]) for r in store.db.execute("SELECT data FROM model_usage")]
    assert len(recorded) == 2 and sum(r["cost_usd"] for r in recorded) == pytest.approx(0.02)
    assert not any(e["kind"] == "escalate" for e in store.events(result.run_id))


async def test_stopped_run_retains_its_summary():
    store = Store(":memory:")
    async def cancelled(brief):
        raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await play_council(1, cancelled, store=store, run_id="stopped")
    run = store.run("stopped")
    assert run["status"] == run["result"]["status"] == "stopped"
    assert store.events("stopped")[-1]["kind"] == "end"


def test_live_api_preserves_seven_advisors_and_rejects_stale_choices(monkeypatch, llm_reply):
    from sumer import web
    monkeypatch.setattr(web, "store", Store(":memory:"))
    monkeypatch.setattr(web, "live", web.Live())
    web.live.pace_s = 0
    with TestClient(web.app) as client:
        assert len(client.get("/api/meta").json()["advisors"]) == 7
        assert client.get("/").status_code == 200 and client.get("/app.js").status_code == 200
        response = client.post("/api/game", json={"seed": 1, "max_rounds": 3})
        assert response.status_code == 200
        run_id = response.json()["run_id"]
        assert web.store.run(run_id)["manifest"]["reign_years"] == 5
        assert client.post("/api/escalation", json={"run_id": "old", "choice": "safe"}).status_code == 409
        assert client.post("/api/stop").status_code == 200


async def test_repeated_seed_keeps_each_runs_proposals_separate():
    store = Store(":memory:")
    first = await play_council(1, unanimous, store=store)
    second = await play_council(1, unanimous, store=store)
    assert first.run_id != second.run_id and first.status == second.status == "completed"
    for run_id in (first.run_id, second.run_id):
        assert store.db.execute("SELECT COUNT(*) FROM proposals WHERE run_id=?", (run_id,)).fetchone()[0] == 5


async def test_runoff_certificate_preserves_the_seven_advisor_rule():
    from tests.test_council import A, _ballot, _three_way, scripted
    def script(brief):
        if brief.phase == "runoff":
            a, b = brief.runoff
            vote = a.id if brief.speaker in ("farmer", "priest", "general", "merchant") else b.id
            return _ballot(brief, A, vote=vote)
        return _three_way(brief)
    state, rolls = new_game(1)
    store = Store(":memory:")
    store.create_run("runoff", {"initial_state": state.public(), "max_rounds": 6})
    async def emit(kind, data):
        store.append("runoff", kind, data)
    result = await deliberate_year(seed=1, run_id="runoff", state=state, ask=scripted(script),
        history=[], max_rounds=6, scheduled_news=None, emit=emit)
    advanced, event = commit(store, "runoff", state, result, rolls)
    assert advanced.year == 2 and event["data"]["outcome"] == "runoff"
    assert len(event["data"]["yes_voters"]) == 4 and len(event["data"]["dissent"]) == 3


@pytest.mark.parametrize("outcome,supporters", [("majority", 4), ("runoff", 3)])
async def test_engine_commits_majority_and_runoff_without_red_line_clearance(outcome, supporters):
    from tests.test_council import _ballot, _ids_for, scripted
    # All of these are legal budgets that the former Farmer/Physician vetoes blocked.
    camps = [(0, 0, 1000), (0, 0, 900), (0, 0, 800)]
    def script(brief):
        if brief.phase == "runoff":
            a, b = brief.runoff
            vote = a.id if brief.speaker in ("farmer", "priest", "general", "merchant") else b.id
            return _ballot(brief, vote=vote)
        index = ["farmer", "priest", "general", "merchant", "builder", "healer", "diplomat"].index(brief.speaker)
        plan = camps[0] if index < supporters else camps[1] if index < supporters + 2 else camps[2]
        return _ballot(brief, plan, accept=_ids_for(brief, plan))
    state, rolls = new_game(1)
    store = Store(":memory:")
    store.create_run(outcome, {"initial_state": state.public(), "max_rounds": 6})
    async def emit(kind, data):
        store.append(outcome, kind, data)
    result = await deliberate_year(seed=1, run_id=outcome, state=state, ask=scripted(script),
        history=[], max_rounds=6, scheduled_news=None, emit=emit)
    advanced, event = commit(store, outcome, state, result, rolls)
    assert result.outcome == outcome and advanced.over
    assert len(event["data"]["yes_voters"]) == 4
    assert "vetoes" not in (result.runoff or {})
