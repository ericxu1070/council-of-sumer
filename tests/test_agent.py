import json
from dataclasses import replace

import pytest

from sumer import agent, llm
from sumer.advisors import ADVISORS, resolve_council
from sumer.game import State
from sumer.protocol import Brief, Plan, TabledProposal, validate_reply


def brief_for(advisor="farmer", **kw):
    return Brief(seed=1, year=1, round=0, max_rounds=6, state=State().public(),
                 beliefs={}, speaker=advisor, **kw)


@pytest.mark.parametrize("advisor,proposal", [
    ("farmer", {"land_delta": 0, "feed": 0, "plant": 500}),
    ("provider", {"land_delta": 0, "feed": 2000, "plant": 0}),
    ("profiteer", {"land_delta": -300, "feed": 2000, "plant": 500}),
    ("merchant", {"land_delta": 0, "feed": 2000, "plant": 0}),
    ("imposter", {"land_delta": 0, "feed": 1700, "plant": 1000}),
    ("provider", {"land_delta": 110, "feed": 0, "plant": 0}),
])
async def test_model_can_propose_plans_previously_blocked_by_red_lines(llm_reply, advisor, proposal):
    llm_reply["proposal"] = proposal
    brief = brief_for(advisor)
    ballot = await agent.decide(advisor, brief)
    assert validate_reply(ballot, brief) is ballot
    assert ballot.proposal.model_dump() == proposal
    assert ballot.source == "llm" and "veto" not in ballot.model_dump()


async def test_model_alone_selects_accepts_and_backed_plan(llm_reply):
    proposal = Plan(land_delta=0, feed=0, plant=500)
    tabled = TabledProposal(id="profiteer-plan", author="profiteer", plan=proposal)
    brief = brief_for(table=[tabled])
    llm_reply.update(back=tabled.id, accept=[tabled.id])
    ballot = await agent.decide("farmer", brief)
    assert validate_reply(ballot, brief) is ballot
    assert ballot.back == tabled.id and ballot.proposal == proposal
    assert ballot.accept == [tabled.id]
    llm_reply.update(back=None, accept=[])
    ballot = await agent.decide("farmer", brief)
    assert ballot.accept == []


async def test_prompt_contains_context_without_policy_guidance(monkeypatch, llm_reply):
    seen = []
    async def chat(messages):
        seen.extend(messages)
        return llm.Reply(json.dumps(llm_reply), "test", False, 0.0, 0.0)
    monkeypatch.setattr(llm, "chat", chat)
    await agent.decide("farmer", brief_for())
    prompt = json.dumps(seen)
    assert ADVISORS["farmer"].mandate in prompt and "city" in prompt
    for removed in ("suggested_plan", "your score", "threshold", "red line", "policy_vote"):
        assert removed not in prompt


async def test_runoff_choice_is_not_forced_by_an_old_red_line(llm_reply):
    a = TabledProposal(id="a", author="profiteer", plan=Plan(land_delta=0, feed=0, plant=500))
    b = TabledProposal(id="b", author="provider", plan=Plan(land_delta=0, feed=2000, plant=500))
    brief = brief_for(phase="runoff", table=[a, b], runoff=[a, b])
    llm_reply["vote"] = a.id
    ballot = await agent.decide("farmer", brief)
    assert validate_reply(ballot, brief) is ballot
    assert ballot.runoff_vote == a.id and ballot.proposal == a.plan


@pytest.mark.parametrize("changes", [
    {"proposal": {"land_delta": 0, "feed": 100000, "plant": 0}},
    {"proposal": {"land_delta": 0, "feed": 2000.5, "plant": 0}},
    {"accept": ["missing-plan"]},
    {"back": "missing-plan"},
])
async def test_invalid_model_choices_are_errors_not_replaced_by_a_policy(llm_reply, changes):
    llm_reply.update(changes)
    brief = brief_for()
    ballot = await agent.decide("farmer", brief)
    assert ballot.error and ballot.accept == []
    assert "chosen" not in ballot.trace
    with pytest.raises(ValueError):
        validate_reply(ballot, brief)


async def test_removed_mock_mode_cannot_generate_a_council_vote(monkeypatch):
    monkeypatch.setattr(agent, "settings", replace(agent.settings, llm_mode="mock"))
    with pytest.raises(llm.LLMUnavailable, match="policy mode was removed"):
        await agent.decide("farmer", brief_for())


async def test_unseated_speaker_cannot_receive_a_model_vote(llm_reply):
    roster = resolve_council([{"id": "merchant"}])
    brief = brief_for("farmer", advisors=[a.public() for a in roster.values()])
    ballot = await agent.decide("farmer", brief)
    assert "not seated" in ballot.error
    assert ballot.accept == [] and "chosen" not in ballot.trace
    with pytest.raises(ValueError, match="not seated"):
        validate_reply(ballot, brief)
