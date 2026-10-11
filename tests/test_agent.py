import json
from dataclasses import replace

import pytest

from sumer import agent, llm
from sumer.game import State
from sumer.protocol import Brief, Plan, TabledProposal, validate_reply


def brief_for(advisor="farmer", **kw):
    return Brief(seed=1, year=1, round=0, max_rounds=6, state=State().public(),
                 beliefs={}, speaker=advisor, **kw)


@pytest.mark.parametrize("advisor,proposal", [
    ("farmer", {"land_delta": 0, "feed": 0, "plant": 500}),
    ("priest", {"land_delta": 0, "feed": 2000, "plant": 0}),
    ("general", {"land_delta": -300, "feed": 2000, "plant": 500}),
    ("builder", {"land_delta": 0, "feed": 2000, "plant": 0}),
    ("healer", {"land_delta": 0, "feed": 1700, "plant": 1000}),
    ("diplomat", {"land_delta": 110, "feed": 0, "plant": 0}),
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
    tabled = TabledProposal(id="general-plan", author="general", plan=proposal)
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
    assert "Grow the population" in prompt and "city" in prompt
    for removed in ("suggested_plan", "your score", "threshold", "red line", "policy_vote"):
        assert removed not in prompt


async def test_runoff_choice_is_not_forced_by_an_old_red_line(llm_reply):
    a = TabledProposal(id="a", author="general", plan=Plan(land_delta=0, feed=0, plant=500))
    b = TabledProposal(id="b", author="priest", plan=Plan(land_delta=0, feed=2000, plant=500))
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


# ---- replay safety ----

async def test_llm_call_is_retried_then_cached(monkeypatch, tmp_path):
    from sumer import llm

    calls = []

    async def flaky(messages, temperature):
        calls.append(1)
        if len(calls) < 3:
            raise llm.LLMFailed("timeout")
        return llm.Reply('{"ok": 1}', "m", False, 0.1, 0.0)

    monkeypatch.setattr(llm, "_call", flaky)
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "spent_usd", lambda: 0.0)
    monkeypatch.setattr(llm, "BACKOFF_S", (0.0, 0.0))
    monkeypatch.setattr(llm, "_cache_path", lambda m: tmp_path / "reply.json")
    r = await llm.chat([{"role": "user", "content": "hi"}])
    assert r.content == '{"ok": 1}' and len(calls) == 3
    again = await llm.chat([{"role": "user", "content": "hi"}])
    assert again.cached and len(calls) == 3
