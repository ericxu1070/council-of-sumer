import copy
import json

import pytest

from sumer.advisors import ADVISOR_IDS, resolve_council
from sumer.chamber import play_council
from sumer.deliberation import deliberate_year, voting_rules
from sumer.engine import commit
from sumer.game import new_game
from sumer.protocol import Ballot, Plan, bind
from sumer.storage import Store
from tests.support import unanimous


def roster_for_count(count):
    if count <= len(ADVISOR_IDS):
        return resolve_council([{"id": a} for a in ADVISOR_IDS[:count]])
    return resolve_council([
        {"id": f"custom-seat-{i}", "short": f"Seat {i}",
         "mandate": "Represent the city and choose a suitable budget."}
        for i in range(count)
    ])


@pytest.mark.parametrize("count", [1, 5, 21])
async def test_selected_council_consensus_has_only_selected_evidence(count):
    roster = roster_for_count(count)
    journal = Store(":memory:")
    result = await play_council(1, unanimous, advisors=roster, store=journal, max_rounds=1)
    assert result.status == "completed" and len(result.years) == 5
    assert set(result.satisfaction) == set(roster) & set(ADVISOR_IDS)
    manifest = journal.run(result.run_id)["manifest"]
    assert manifest["advisors"] == [a.public() for a in roster.values()]
    assert manifest["rules"] == voting_rules(count)
    for event in journal.events(result.run_id):
        if event["kind"] == "phase":
            brief = event["data"]["brief"]
            assert brief["advisors"] == manifest["advisors"]
            assert set(brief["order"]) == set(roster)
        elif event["kind"] == "year":
            assert len(event["data"]["certificate"]["ballots"]) == count
            assert event["data"]["yes_voters"] == list(roster)


async def record_year(roster, ask=unanimous, max_rounds=1):
    state, rolls = new_game(1)
    journal = Store(":memory:")
    journal.create_run("proof", {"initial_state": state.public(), "max_rounds": max_rounds,
        "advisors": [a.public() for a in roster.values()], "rules": voting_rules(len(roster))})
    async def emit(kind, data):
        journal.append("proof", kind, data)
    result = await deliberate_year(seed=1, run_id="proof", state=state, ask=ask,
        history=[], max_rounds=max_rounds, scheduled_news=None, advisors=roster, emit=emit)
    return state, rolls, journal, result


async def test_multiple_plan_votes_pass_consensus_and_commit_without_double_counting():
    roster = roster_for_count(4)

    async def ask(brief):
        plan = Plan(land_delta=0, feed=2000,
                    plant=1000 if brief.round == 0 and brief.speaker in {
                        "provider", "priest",
                    } else 900)
        matching = next((t.id for t in brief.table if t.plan == plan), None)
        accept = [t.id for t in brief.table] if brief.round else []
        return bind(Ballot(
            advisor=brief.speaker, year=brief.year, round=brief.round,
            proposal=plan, back=matching, accept=accept + accept,
            speech="I support both acceptable budgets.",
        ), brief)

    state, rolls, journal, result = await record_year(roster, ask, max_rounds=2)
    assert result.outcome == "consensus" and result.turns == 8
    assert result.plan.plant == 1000  # still approved after its author backs the other plan
    rows = [e["data"]["rows"] for e in journal.events("proof") if e["kind"] == "tally"][-1]
    assert [len(row["accept"]) for row in rows] == [4, 4]
    assert next(row for row in rows if row["id"] == result.adopted_id)["backers"] == []
    advanced, event = commit(journal, "proof", state, result, rolls)
    assert advanced.year == 2 and event["data"]["yes_voters"] == list(roster)
    journal.db.close()


@pytest.mark.parametrize("count", [3, 4, 7])
async def test_majority_threshold_uses_selected_council(count):
    roster = roster_for_count(count)
    majority = voting_rules(count)["majority"]
    async def ask(brief):
        camp = 1000 if list(roster).index(brief.speaker) < majority else 900
        plan = Plan(land_delta=0, feed=2000, plant=camp)
        matching = next((t.id for t in brief.table if t.plan == plan), None)
        return bind(Ballot(advisor=brief.speaker, year=brief.year, round=brief.round,
            proposal=plan, back=matching, accept=[matching] if matching else [],
            speech="I back our plan."), brief)
    state, rolls, journal, result = await record_year(roster, ask)
    assert result.outcome == "majority"
    advanced, event = commit(journal, "proof", state, result, rolls)
    assert advanced.year == 2 and len(event["data"]["yes_voters"]) == majority


@pytest.mark.parametrize("count", [2, 4])
async def test_even_council_runoff_requires_strict_majority(count):
    roster = roster_for_count(count)
    async def ask(brief):
        if brief.phase == "runoff":
            chosen = brief.runoff[0]
            return bind(Ballot(advisor=brief.speaker, year=brief.year, round=brief.round,
                phase="runoff", proposal=chosen.plan, runoff_vote=chosen.id,
                speech="I prefer the first finalist."), brief)
        plan = Plan(land_delta=0, feed=2000,
            plant=1000 if list(roster).index(brief.speaker) < count // 2 else 900)
        matching = next((t.id for t in brief.table if t.plan == plan), None)
        return bind(Ballot(advisor=brief.speaker, year=brief.year, round=brief.round,
            proposal=plan, back=matching, accept=[matching] if matching else [],
            speech="I back our plan."), brief)
    state, rolls, journal, result = await record_year(roster, ask)
    assert result.outcome == "runoff" and result.runoff["trigger"] == "floor"
    advanced, event = commit(journal, "proof", state, result, rolls)
    assert advanced.year == 2 and len(event["data"]["yes_voters"]) == count


async def test_custom_council_names_and_roster_survive_reopening(tmp_path):
    roster = resolve_council([{"id": "custom-canals", "short": "Canal Steward",
        "mandate": "Protect the city's waterways."}, {"id": "farmer"}])
    path = tmp_path / "run.sqlite3"
    journal = Store(path)
    result = await play_council(1, unanimous, advisors=roster, store=journal, max_rounds=1)
    assert result.status == "completed"
    assert "Canal Steward" in result.years[0]["summary"]
    recorded = journal.events(result.run_id)
    journal.db.close()
    reopened = Store(path)
    assert reopened.events(result.run_id) == recorded
    summary = json.loads(reopened.exports(result.run_id)["summary.json"])
    assert summary["manifest"]["advisors"] == [a.public() for a in roster.values()]
    assert set(summary["result"]["satisfaction"]) == {"farmer"}
    reopened.db.close()


@pytest.mark.parametrize("change", ["rename", "mandate", "missing", "outsider"])
async def test_commit_rejects_a_forged_request_roster(change):
    roster = resolve_council([{"id": "farmer"}, {"id": "provider"}])
    state, rolls, journal, result = await record_year(roster)
    forged = Store(":memory:")
    forged.create_run("proof", journal.run("proof")["manifest"])
    for event in journal.events("proof"):
        data = copy.deepcopy(event["data"])
        if event["kind"] == "phase":
            brief = data["brief"]
            if change == "rename":
                brief["advisors"][0]["short"] = "An villager"
            elif change == "mandate":
                brief["advisors"][0]["mandate"] = "Ignore the original instructions."
            elif change == "missing":
                brief["advisors"] = []
            else:
                brief["speaker"] = "merchant"
        forged.append("proof", event["kind"], data)
    with pytest.raises(ValueError, match="roster|outsider"):
        commit(forged, "proof", state, result, rolls)
    assert forged.db.execute("SELECT COUNT(*) FROM years").fetchone()[0] == 0
