import json

import pytest
from pydantic import ValidationError

from sumer import agent, llm
from sumer.advisors import resolve_council
from sumer.chamber import play_council
from sumer.deliberation import deliberate_year
from sumer.game import State
from sumer.memory import BeliefUpdate, PrivateBelief, PrivateMemory, revise
from sumer.protocol import Brief, digest, validate_reply
from sumer.storage import Store
from tests.support import unanimous
from tests.test_council import _three_way, scripted
from tests.test_private_rulebooks import brief_for


def claim(id="yield", text="The old yield estimate is doubtful.", **kw):
    return PrivateBelief(id=id, claim=text, evidence="Year 1 field report.",
                         confidence="low", uncertainty="One report may not generalize.", **kw)


def test_patches_preserve_unmentioned_beliefs_and_require_explicit_removal():
    first = PrivateMemory(revision=2, beliefs=[claim(), claim("price", "Prices may fall.")])
    update = BeliefUpdate(upsert=[claim(text="A new estimate.")])
    second = revise(first, update)
    assert second.revision == 3 and second.beliefs[1] == first.beliefs[1]
    assert first.beliefs[0].claim == "The old yield estimate is doubtful."
    assert revise(second, None) == second
    assert revise(second, update) == second  # repeated content is not a new revision
    assert revise(second, BeliefUpdate(remove=["yield", "price"])) == PrivateMemory(revision=4)
    with pytest.raises(ValueError, match="unknown"):
        revise(second, BeliefUpdate(remove=["missing"]))
    full = PrivateMemory(beliefs=[claim(f"claim-{i}") for i in range(20)])
    with pytest.raises(ValidationError):
        revise(full, BeliefUpdate(upsert=[claim("overflow")]))


@pytest.mark.parametrize("bad", [
    {"upsert": [claim().model_dump(), claim().model_dump()]},
    {"upsert": [claim().model_dump()], "remove": ["yield"]},
    {"remove": ["yield", "yield"]},
    {"upsert": [claim(f"claim-{i}").model_dump() for i in range(4)]},
    {"upsert": [{**claim().model_dump(), "confidence": 1}]},
    {"upsert": [{**claim().model_dump(), "uncertainty": ""}]},
    {"upsert": [{**claim().model_dump(), "evidence": "x" * 501}]},
    {"advisor": "merchant", "upsert": [claim().model_dump()]},
])
def test_malformed_or_cross_advisor_updates_are_rejected(bad):
    with pytest.raises(ValidationError):
        BeliefUpdate.model_validate(bad)


@pytest.mark.parametrize("phase", ["discuss", "runoff"])
@pytest.mark.parametrize("bias", [0.0, 0.5, 1.0])
async def test_model_updates_its_own_memory_in_each_phase(monkeypatch, phase, bias):
    before = PrivateMemory(revision=4, beliefs=[claim("old", "OWNER_SAVED_CLAIM")])
    brief = brief_for([{"id": "farmer", "bias": bias, "rulebook": "Original farmer notes."},
                       {"id": "merchant", "rulebook": "OTHER_PRIVATE_CLAIM"}],
                      phase=phase, private_memory=before)
    messages_seen = []
    update = BeliefUpdate(upsert=[claim("new", "A revised harvest estimate.")])

    async def chat(messages):
        messages_seen.extend(messages)
        return llm.Reply(json.dumps({"proposal": {"land_delta": 0, "feed": 2000, "plant": 1000},
            "vote": "P1", "speech": "Merchant, the report changes my estimate.",
            "belief_update": update.model_dump()}), "test", False, 0, 0)

    monkeypatch.setattr(llm, "chat", chat)
    ballot = await agent.decide("farmer", brief)
    validate_reply(ballot, brief)
    assert ballot.belief_update == update
    assert ballot.trace["private_memory_before"] == before.model_dump()
    assert ballot.trace["private_memory_after"] == revise(before, update).model_dump()
    assert ballot.trace["rulebook"] == "Original farmer notes."
    payload = json.loads(messages_seen[1]["content"].split("\n\n")[0])
    assert payload["private_memory"] == before.model_dump()
    assert "OTHER_PRIVATE_CLAIM" not in json.dumps(messages_seen)
    system = messages_seen[0]["content"]
    assert f"Your bias value: {bias}" in system
    assert "Bias also governs willingness to revise saved beliefs" in system
    assert "instead of resetting to the starting claim" in system
    with pytest.raises(ValueError, match="addressed speaker"):
        agent._llm_messages("merchant", brief)


async def test_failed_and_stale_turns_cannot_change_memory():
    memories = {"farmer": PrivateMemory(revision=2, beliefs=[claim()])}
    attempts = []

    async def ask(brief):
        attempts.append(brief.model_copy(deep=True))
        ballot = await unanimous(brief)
        # Both invalid requests try to poison the memory; the third round-trip is valid.
        update = BeliefUpdate(upsert=[claim(text="REJECTED_SENTINEL")])
        if len(attempts) <= 2:
            return ballot.model_copy(update={"belief_update": update,
                "request_hash": "stale" if len(attempts) == 1 else ballot.request_hash,
                "error": None if len(attempts) == 1 else "unreadable response"})
        return ballot

    result = await deliberate_year(seed=1, state=State(), ask=ask, history=[], max_rounds=3,
        scheduled_news=None, advisors=resolve_council([{"id": "farmer"}]),
        private_memories=memories)
    assert result.outcome == "consensus"
    assert len(attempts) == 3
    assert all(b.private_memory.revision == 2 for b in attempts)
    assert "REJECTED_SENTINEL" not in memories["farmer"].model_dump_json()


async def test_learning_survives_scout_revote_and_runoff():
    memories = {}
    base = scripted(_three_way)
    seen = []

    async def ask(brief):
        seen.append(brief.model_copy(deep=True))
        ballot = await base(brief)
        if brief.phase == "runoff":
            # Retain prior memory unless the new scout creates new evidence.
            return ballot
        return ballot.model_copy(update={"belief_update": BeliefUpdate(
            upsert=[claim("claim", f"{brief.speaker} learned in round {brief.round}.")])})

    events = []
    async def after_vote(rnd, phase, rows):
        if phase == "discuss" and rnd == 0:
            return {"text": "A new public field report.", "beliefs": {"expected_yield": 1}}
        return None
    async def emit(kind, data):
        events.append((kind, data))

    # A/B camps force a runoff; choose A there to finish.
    original = ask
    async def with_runoff(brief):
        if brief.phase == "runoff":
            from sumer.protocol import bind
            from tests.test_council import _ballot
            seen.append(brief.model_copy(deep=True))
            return bind(_ballot(brief, vote=brief.runoff[0].id), brief)
        return await original(brief)

    result = await deliberate_year(seed=1, state=State(), ask=with_runoff, history=[],
        max_rounds=2, scheduled_news=None, after_vote=after_vote,
        private_memories=memories, emit=emit)
    assert result.outcome == "runoff"
    for brief in seen:
        if brief.round > 0:
            assert brief.private_memory.beliefs
            assert brief.speaker in brief.private_memory.beliefs[0].claim
        if brief.phase == "runoff":
            assert brief.private_memory.revision == 2
    # No private text rides on the public conversation supplied to any other seat.
    assert all("learned in round" not in json.dumps(b.turns_this_round + b.earlier_turns) for b in seen)


async def test_memory_persists_across_years_disk_reopen_exports_and_new_runs(tmp_path, monkeypatch):
    path = tmp_path / "memory.sqlite3"
    journal = Store(path)
    seats = [{"id": "farmer", "rulebook": "PRESERVED_START_FARMER"},
             {"id": "farmer-copy", "short": "Farmer", "mandate": "Represent fields.",
              "rulebook": "PRESERVED_START_COPY"}]
    seen = []

    async def ask(brief):
        seen.append(brief.model_copy(deep=True))
        ballot = await unanimous(brief)
        # Model-like independent beliefs from the public last outcome on the next turn.
        source = f"Year {brief.year}: last outcome {brief.state['last_report']}"
        learned = claim(text=f"{brief.speaker} current estimate for year {brief.year}.").model_copy(
            update={"evidence": source[:500]})
        return ballot.model_copy(update={"belief_update": BeliefUpdate(upsert=[learned])})

    for run_id in ("first", "second"):
        start = len(seen)
        game = await play_council(1, ask, store=journal, run_id=run_id,
                                  advisors=resolve_council(seats), reveal_news=False)
        assert game.status == "completed", journal.events(run_id)[-2:]
        run_briefs = seen[start:]
        for brief in run_briefs:
            if brief.year == 1:
                assert brief.private_memory == PrivateMemory()
            else:
                assert brief.private_memory.revision == brief.year - 1
                assert brief.speaker in brief.private_memory.beliefs[0].claim
            assert "current estimate" not in json.dumps(brief.history)
        assert [a["rulebook"] for a in journal.run(run_id)["manifest"]["advisors"]] == [
            s["rulebook"] for s in seats]
    saved = journal.private_beliefs("first")
    assert saved["farmer"]["revision"] == 5
    assert saved["farmer"]["starting_rulebook"] == "PRESERVED_START_FARMER"
    assert "year 5" in saved["farmer"]["beliefs"][0]["claim"]
    journal.db.close()
    reopened = Store(path)
    assert reopened.private_beliefs("first") == saved
    assert json.loads(reopened.exports("first")["private-beliefs.json"]) == saved
    reopened.private_memories("first", verify=True)
    from fastapi.testclient import TestClient

    from sumer import web
    monkeypatch.setattr(web, "store", reopened)
    monkeypatch.setattr(web, "live", web.Live())
    with TestClient(web.app) as client:
        assert client.get("/api/runs/first/beliefs").json() == saved
        assert client.get("/api/runs/missing/beliefs").status_code == 404
    # A corrupted request cannot quietly substitute another belief record.
    events = reopened.events("first")
    phase = next(e for e in events if e["kind"] == "phase")
    phase["data"]["brief"]["private_memory"]["revision"] = 999
    monkeypatch.setattr(reopened, "events", lambda *a, **kw: events)
    with pytest.raises(ValueError, match="current private beliefs"):
        reopened.private_memories("first", verify=True)
    reopened.db.close()


def test_legacy_request_hash_remains_valid():
    brief = Brief(seed=1, year=1, round=0, max_rounds=3, state=State().public(),
                  beliefs={}, speaker="farmer")
    raw = brief.model_dump(exclude={"private_memory"})
    from sumer.protocol import Ballot, Plan
    ballot = Ballot(advisor="farmer", year=1, round=0, proposal=Plan(land_delta=0, feed=0, plant=0),
        speech="I agree.", run_id=brief.run_id, state_hash=brief.state_hash,
        reply_to=brief.message_id, request_hash=digest(raw))
    validate_reply(ballot, Brief.model_validate(raw))
