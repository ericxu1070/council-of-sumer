import copy

import pytest

from sumer.chamber import play_council
from sumer.deliberation import Deadlock, deliberate_year
from sumer.engine import commit
from sumer.game import State, roll_game
from sumer.protocol import Ballot, Plan, bind
from sumer.storage import Store
from tests.support import unanimous


async def recorded_year(ask, *, max_rounds=3, after_vote=None):
    state = State()
    store = Store(":memory:")
    store.create_run("skips", {"initial_state": state.public(), "max_rounds": max_rounds,
                              "scout_mode": "after_vote" if after_vote else "scheduled"})
    async def emit(kind, data):
        store.append("skips", kind, data)
    if after_vote:
        scout = after_vote
        async def after_vote(round_no, phase, rows):
            report = await scout(round_no, phase, rows)
            await emit("scout_choice", {"year": 1, "round": round_no, "phase": phase,
                                       "choice": "skip" if report is None else "harvest"})
            return report
    result = await deliberate_year(seed=1, run_id="skips", state=state, ask=ask, history=[],
        max_rounds=max_rounds, scheduled_news=None, emit=emit, after_vote=after_vote)
    return state, store, result


@pytest.mark.parametrize("failed", ["provider", "villager"])
@pytest.mark.parametrize("failure", ["missing", "exception", "stale", "budget"])
async def test_skipped_first_or_last_turn_still_commits_majority(failed, failure):
    seen = []
    async def ask(brief):
        seen.append(brief)
        ballot = await unanimous(brief)
        if brief.speaker == failed:
            if failure == "missing":
                return None
            if failure == "exception":
                raise RuntimeError("Advisor offline")
            if failure == "stale":
                return ballot.model_copy(update={"reply_to": "old-request"})
            return ballot.model_copy(update={"proposal": Plan(land_delta=0, feed=2800, plant=1000)})
        return ballot
    state, store, result = await recorded_year(ask)
    assert result.outcome == "majority" and result.rounds == 2
    assert result.dissent == [failed]
    assert len(result.certificate["ballots"]) == 4
    assert result.turns == 8
    skipped = [e for e in store.events("skips") if e["kind"] == "agent_skipped"]
    assert len(skipped) == 2
    failed_briefs = [b for b in seen if b.speaker == failed]
    assert len(failed_briefs) == 4 and failed_briefs[1].errors and failed_briefs[3].errors
    next_state, event = commit(store, "skips", state, result, roll_game(1))
    assert next_state.year == 2 and failed not in event["data"]["yes_voters"]
    assert "agent_skipped" in store.exports("skips")["report.md"]


async def test_successful_later_turn_restores_advisor_vote():
    async def ask(brief):
        if brief.speaker == "provider" and brief.round == 0:
            return None
        return await unanimous(brief)
    state, store, result = await recorded_year(ask)
    assert result.outcome == "consensus" and len(result.certificate["ballots"]) == 5
    commit(store, "skips", state, result, roll_game(1))


async def test_whole_game_continues_when_one_advisor_is_offline():
    async def ask(brief):
        if brief.speaker == "farmer":
            raise RuntimeError("Advisor offline")
        return await unanimous(brief)
    store = Store(":memory:")
    result = await play_council(1, ask, store=store)
    assert result.status == "completed" and len(result.years) > 1
    assert all("farmer" in year["dissent"] for year in result.years)
    events = store.events(result.run_id)
    assert any(e["kind"] == "agent_skipped" for e in events)
    assert not any(e["kind"] == "technical_failure" for e in events)


async def test_skip_discards_previous_round_ballot_in_certificate():
    async def ask(brief):
        if brief.speaker == "farmer" and brief.round == 1:
            return None
        ballot = await unanimous(brief)
        if brief.speaker == "villager" and brief.round == 0:
            return bind(ballot.model_copy(update={"back": None, "accept": [],
                "proposal": Plan(land_delta=0, feed=0, plant=0)}), brief)
        return ballot
    state, store, result = await recorded_year(ask)
    assert result.outcome == "majority" and result.dissent == ["farmer"]
    assert "farmer" not in {b["advisor"] for b in result.certificate["ballots"]}
    commit(store, "skips", state, result, roll_game(1))


async def test_skips_cannot_lower_majority_threshold():
    async def ask(brief):
        return None if brief.speaker in ("farmer", "merchant", "priest") else await unanimous(brief)
    with pytest.raises(Deadlock, match="only 2 of 5"):
        await recorded_year(ask)


async def runoff_ask(brief):
    if brief.phase == "runoff":
        if brief.speaker == "merchant":
            return None
        finalist = brief.runoff[0] if brief.speaker != "villager" else brief.runoff[1]
        return bind(Ballot(advisor=brief.speaker, year=brief.year, round=brief.round,
            phase="runoff", proposal=finalist.plan, runoff_vote=finalist.id,
            speech="I support this plan."), brief)
    # Two plans split 2-2; merchant abstains. The runoff still needs 3 of 5.
    if brief.speaker == "merchant":
        return None
    plan = Plan(land_delta=0, feed=2000, plant=1000 if brief.speaker in
                ("provider", "farmer") else 900)
    existing = next((t.id for t in brief.table if t.plan == plan), None)
    return bind(Ballot(advisor=brief.speaker, year=brief.year, round=brief.round,
        proposal=plan, back=existing, accept=[existing] if existing else [],
        speech="I support this plan."), brief)


@pytest.mark.parametrize("interactive", [False, True])
async def test_runoff_with_skips_and_optional_scout_revote_commits(interactive):
    async def scout(round_no, phase, rows):
        if phase == "runoff" and round_no == 1:
            return {"kind": "harvest", "text": "A poor harvest is expected.",
                    "beliefs": {"expected_yield": 1}}
        return None
    state, store, result = await recorded_year(runoff_ask, max_rounds=1,
                                             after_vote=scout if interactive else None)
    assert result.outcome == "runoff" and "merchant" not in result.runoff["votes"]
    assert result.turns == (12 if interactive else 8)
    commit(store, "skips", state, result, roll_game(1))


async def test_unrecorded_skip_cannot_replace_missing_ballot():
    async def ask(brief):
        return None if brief.speaker == "provider" else await unanimous(brief)
    state, store, result = await recorded_year(ask)
    tampered = Store(":memory:")
    tampered.create_run("skips", store.run("skips")["manifest"])
    for event in store.events("skips"):
        if event["kind"] != "agent_skipped":
            tampered.append("skips", event["kind"], event["data"])
    with pytest.raises(ValueError, match="complete sequential"):
        commit(tampered, "skips", state, result, roll_game(1))
    bad = copy.deepcopy(result)
    bad.certificate["ballots"].pop()
    with pytest.raises(ValueError, match="unchanged latest ballot"):
        commit(store, "skips", state, bad, roll_game(1))


async def test_skip_requires_both_failed_attempts():
    async def ask(brief):
        return None if brief.speaker == "provider" else await unanimous(brief)
    state, store, result = await recorded_year(ask)
    tampered = Store(":memory:")
    tampered.create_run("skips", store.run("skips")["manifest"])
    for event in store.events("skips"):
        if event["kind"] == "validation_error" and event["data"]["attempt"] == 1:
            continue
        tampered.append("skips", event["kind"], event["data"])
    with pytest.raises(ValueError, match="two recorded failed attempts"):
        commit(tampered, "skips", state, result, roll_game(1))
