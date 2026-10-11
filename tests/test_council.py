import asyncio
import json
import sqlite3
from dataclasses import replace

import pytest
from pydantic import ValidationError

from sumer import llm
from sumer.agent import clean, decide, messages
from sumer.chamber import play_council, play_simple
from sumer.deliberation import TechnicalFailure, collect, deliberate_year
from sumer.engine import commit
from sumer.game import State, new_game
from sumer.protocol import (
    ADVISOR_IDS,
    Delivery,
    Plan,
    sign,
    validate_reply,
    verify,
    verify_certificate,
)
from sumer.storage import Store
from sumer.transport import inproc
from tests.support import _emit, brief, voters


def test_budget_contract_rejects_floats():
    with pytest.raises(ValidationError):
        Plan(land_delta=0, feed=2000.1, plant=500)


async def test_signatures_and_transport_origin(identities):
    request = brief(identities)
    reply = await decide("farmer", request)
    assert verify(reply, identities)
    altered = reply.model_copy(update={"speech": "Changed after signing."})
    assert not verify(altered, identities)
    with pytest.raises(ValueError, match="endpoint"):
        validate_reply(Delivery("farmer", "sumer/council/priest", reply), request)
    with pytest.raises(ValueError, match="identity"):
        validate_reply(Delivery("priest", "sumer/council/priest", reply), request)
    stale = sign(reply.model_copy(update={"run_id": "another-run"}))
    with pytest.raises(ValueError, match="Stale"):
        validate_reply(Delivery("farmer", "sumer/council/farmer", stale), request)


async def test_public_prompt_contains_facts_without_computed_guidance(identities):
    request = brief(identities, history=[{"year": 0, "ledger": "public"}],
                    transcript=[{"message_id": "prior", "speech": "An objection."}])
    payload = messages("priest", request)
    context = json.loads(payload[1]["content"])
    assert context["city"]["land_price"] == 19
    assert context["historical_ledger"] == request.history
    assert context["negotiation_transcript"] == request.transcript
    assert all(a in payload[0]["content"] for a in ADVISOR_IDS)
    assert not any(k in context for k in ("scores", "beliefs", "suggested_compromise", "seed"))
    assert not any(k in context["city"] for k in ("rolls", "future_yield", "next_price"))


async def test_three_yes_adopts_only_after_four_valid_ballots(identities):
    events = []
    async def emit(kind, data):
        events.append((kind, data))
    yr = await deliberate_year(run_id="test-run", state=State(), gather=voters,
                              history=[], peer_keys=identities, max_rounds=2, emit=emit)
    assert yr.summary()["outcome"] == "majority"
    assert yr.summary()["dissent"] == ["merchant"]
    assert len(yr.certificate.ballots) == 4
    assert [e[1]["phase"] for e in events if e[0] == "responses"] == [
        "proposal", "objection", "revision"]
    assert sum(e[0] == "ballots" for e in events) == 1
    verify_certificate(yr.certificate, State(), "test-run", identities)
    with pytest.raises(ValueError, match="four"):
        verify_certificate(yr.certificate.model_copy(update={
            "ballots": yr.certificate.ballots[:3]}), State(), "test-run", identities)
    changed = yr.candidate.model_copy(update={"plan": Plan(land_delta=0, feed=2000, plant=100)})
    with pytest.raises(ValueError, match="binding"):
        verify_certificate(yr.certificate.model_copy(update={"candidate": changed}),
                           State(), "test-run", identities)


async def test_duplicate_reply_retries_only_affected_peer(identities):
    requests = []
    async def duplicate_once(request):
        requests.append(request)
        responses = await inproc.gather(request)
        return responses + [responses[0]] if len(requests) == 1 else responses
    received = await collect(brief(identities), duplicate_once, _emit)
    assert len(received) == 4
    assert requests[1].targets == ["farmer"]
    assert requests[1].message_id != requests[0].message_id
    assert "Expected one" in requests[1].errors["farmer"]


async def test_invalid_budget_retries_without_clamping(identities):
    requests = []
    async def invalid_once(request):
        requests.append(request)
        responses = await inproc.gather(request)
        if len(requests) == 1:
            responses[0].reply = sign(responses[0].reply.model_copy(update={
                "proposal": Plan(land_delta=999999, feed=1, plant=1)}))
        return responses
    result = await collect(brief(identities), invalid_once, _emit)
    assert requests[1].targets == ["farmer"]
    assert result[0].proposal.land_delta != 999999


async def test_missing_fourth_voter_is_technical_failure_never_human(identities):
    phases, asked = [], []
    async def missing(request):
        phases.append((request.phase, list(request.targets)))
        replies = await voters(request)
        return [d for d in replies if request.phase != "vote" or d.peer != "merchant"]
    async def human(options, reason):
        asked.append(options)
        return options[0]["id"]
    store = Store(":memory:")
    g = await play_council(1, missing, store=store, max_rounds=1, ask_human=human)
    assert g.status == "technical_failure" and g.years == [] and asked == []
    assert phases[-1] == ("vote", ["merchant"])
    assert sum(p[0] == "vote" for p in phases) == 2
    assert not any(e["kind"] == "escalate" for e in store.events(g.run_id))
    assert store.db.execute("SELECT COUNT(*) FROM ballots").fetchone()[0] == 3
    failure = next(e["data"] for e in store.events(g.run_id) if e["kind"] == "technical_failure")
    assert failure["phase"] == "vote" and failure["peers"] == ["merchant"]
    assert failure["attempt"] == 2


async def test_live_model_failure_never_falls_back(identities, monkeypatch):
    from sumer import agent
    monkeypatch.setattr(agent, "settings", replace(agent.settings, llm_mode="bayleaf"))
    calls = []
    async def broken(*args, **kwargs):
        calls.append(1)
        raise llm.LLMUnavailable("simulated outage")
    monkeypatch.setattr(llm, "chat", broken)
    with pytest.raises(TechnicalFailure):
        await collect(brief(identities), inproc.gather, _emit)
    assert len(calls) == 8  # four peers and one retry, no offline answers


async def test_failed_ballots_feed_revisions_and_deadlock_has_latest_options():
    requests = []
    async def no_votes(request):
        requests.append(request)
        return await voters(request, yes=())
    store = Store(":memory:")
    g = await play_council(1, no_votes, store=store, max_rounds=2)
    assert g.status == "deadlock" and not g.years
    events = store.events(g.run_id)
    ballots = [e for e in events if e["kind"] == "ballots"]
    assert len(ballots) == 8 and all(len(e["data"]["ballots"]) == 4 for e in ballots)
    revision = next(r for r in requests if r.phase == "revision" and r.round == 2)
    assert any(e.get("kind") == "failed_ballot" for e in revision.transcript)
    options = next(e["data"]["options"] for e in events if e["kind"] == "escalate")
    assert len(options) == 4 and all(o["revision"] == 2 for o in options)
    assert all(o["author"] in ADVISOR_IDS for o in options)
    assert store.run(g.run_id)["human_assisted"] is False


async def test_human_choice_is_recorded_without_fabricating_ballots():
    async def no_votes(request):
        return await voters(request, yes=())
    async def human(options, reason):
        return options[0]["id"]
    store = Store(":memory:")
    g = await play_council(7, no_votes, store=store, max_rounds=1, ask_human=human)
    assert g.human_assisted and g.years
    year = next(e["data"] for e in store.events(g.run_id) if e["kind"] == "year")
    assert year["outcome"] == "human_resolution"
    assert year["certificate"] is None and year["yes_voters"] == []
    assert year["human_resolution"]["source"] == "human"


async def test_engine_verifies_certificate_and_commits_exactly_once(identities):
    state, tape = new_game(1)
    store = Store(":memory:")
    store.create_run("test-run", {"initial_state": state.public(), "peer_keys": identities})
    async def emit(kind, data):
        store.append("test-run", kind, data)
    yr = await deliberate_year(run_id="test-run", state=state, gather=voters,
                              history=[], peer_keys=identities, max_rounds=1, emit=emit)
    bad = replace(yr, certificate=yr.certificate.model_copy(update={
        "ballots": yr.certificate.ballots[:3]}))
    with pytest.raises(ValueError):
        commit(store, "test-run", state, bad, tape)
    assert store.db.execute("SELECT COUNT(*) FROM years").fetchone()[0] == 0
    without_requests = Store(":memory:")
    without_requests.create_run("test-run", {"initial_state": state.public(), "peer_keys": identities})
    for recorded in store.events("test-run"):
        if recorded["kind"] != "phase":
            without_requests.append("test-run", recorded["kind"], recorded["data"])
    with pytest.raises(ValueError, match="recorded request"):
        commit(without_requests, "test-run", state, yr, tape)
    nxt, event = commit(store, "test-run", state, yr, tape)
    assert nxt.year == 2 and event["data"]["plan"] == yr.plan.as_dict()
    with pytest.raises(ValueError, match="advanced"):
        commit(store, "test-run", state, yr, tape)
    assert store.db.execute("SELECT COUNT(*) FROM years").fetchone()[0] == 1
    for table in ("events", "proposals", "ballots", "years"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.db.execute(f"DELETE FROM {table}")
    with pytest.raises(sqlite3.IntegrityError, match="manifest"):
        store.db.execute("UPDATE runs SET manifest='{}'")


async def test_full_offline_run_and_existing_baselines():
    g = await play_council(7, inproc.gather, store=Store(":memory:"), max_rounds=1)
    assert g.status == "completed" and len(g.years) == 10
    assert not g.human_assisted
    for policy in ("average", "lone_ruler", "dictator_farmer"):
        result = await play_simple(1, policy)
        assert result.status in ("completed", "overthrown", "depopulated")


def test_clean_and_existing_json_parser():
    assert "—" not in clean("We hold — firm")
    assert llm.parse_json('```json\n{"a": 1}\n```') == {"a": 1}


@pytest.mark.parametrize("content", ['{"choice":"YES","speech":"Yes",', '[]'])
def test_live_parser_rejects_incomplete_or_non_object_decisions(content):
    with pytest.raises(ValueError):
        llm.parse_json(content, strict=True)
    assert llm.parse_json('```json\n{"choice":"NO","speech":"Reason"}\n```', strict=True)["choice"] == "NO"


@pytest.mark.parametrize("empty", [False, True])
async def test_failed_model_attempts_keep_cost_without_adopting(identities, monkeypatch, empty):
    from sumer import agent
    monkeypatch.setattr(agent, "settings", replace(agent.settings, llm_mode="bayleaf"))
    usage = {"model": "configured-model", "cached": False, "latency_s": 0.1, "cost_usd": 0.01}
    async def broken(*args, **kwargs):
        if empty:
            raise llm.LLMUnavailable("empty reply", usage=usage)
        return llm.Reply('{"speech":"Incomplete",', usage["model"], False, 0.1, 0.01)
    monkeypatch.setattr(llm, "chat", broken)
    journal = Store(":memory:")
    g = await play_council(7, inproc.gather, store=journal, max_rounds=1)
    assert g.status == "technical_failure" and not g.years
    recorded_usage = [json.loads(row["data"]) for row in journal.db.execute(
        "SELECT data FROM model_usage WHERE run_id=?", (g.run_id,))]
    assert len(recorded_usage) == 8
    assert sum(u["cost_usd"] for u in recorded_usage) == pytest.approx(0.08)
    assert all(u["model"] == usage["model"] for u in recorded_usage)


async def test_revision_cannot_claim_an_opening_proposal_is_an_objection(identities):
    request = brief(identities, phase="revision", transcript=[
        {"message_id": "opening", "phase": "proposal"},
        {"message_id": "objection", "phase": "objection"},
    ])
    reply = await decide("farmer", request)
    unrelated = sign(reply.model_copy(update={"objections_addressed": ["opening"]}))
    with pytest.raises(ValueError, match="recorded objection"):
        validate_reply(Delivery("farmer", "sumer/council/farmer", unrelated), request)
    validate_reply(Delivery("farmer", "sumer/council/farmer", reply), request)


async def test_stopped_run_retains_summary_and_terminal_event():
    journal = Store(":memory:")
    async def cancelled(request):
        raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await play_council(7, cancelled, store=journal, run_id="cancelled")
    run = journal.run("cancelled")
    assert run["status"] == run["result"]["status"] == "stopped"
    assert run["result"]["rating"]["population"] == 100
    assert journal.events("cancelled")[-1]["kind"] == "end"


async def test_live_human_resolution_is_published_in_recorded_order():
    async def no_votes(request):
        return await voters(request, yes=())
    async def human(options, reason):
        return options[0]["id"]
    published = []
    async def emit(kind, data):
        published.append((kind, data))
    journal = Store(":memory:")
    g = await play_council(7, no_votes, store=journal, max_rounds=1, ask_human=human, emit=emit)
    resolutions = [data for kind, data in published if kind == "human_resolution"]
    assert len(resolutions) == len(g.years)
    for resolution in resolutions:
        committed = next(data for kind, data in published if kind == "year"
                         and data["year"] == resolution["year"])
        assert resolution["_sequence"] + 1 == committed["_sequence"]
        assert resolution["candidate_id"] == committed["adopted"]
