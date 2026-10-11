import pytest

from sumer import agent
from sumer.advisors import resolve_council
from sumer.game import State
from sumer.protocol import Brief, Plan, TabledProposal, validate_reply


def brief_for(advisor="farmer", **kw):
    return Brief(seed=1, year=1, round=0, max_rounds=6, state=State().public(),
                 beliefs={}, speaker=advisor, **kw)


async def test_agent_preserves_the_models_legal_proposal(llm_reply):
    advisor = "provider"
    proposal = {"land_delta": 0, "feed": 0, "plant": 500}
    llm_reply["proposal"] = proposal
    brief = brief_for(advisor)
    ballot = await agent.decide(advisor, brief)
    assert validate_reply(ballot, brief) is ballot
    assert ballot.proposal.model_dump() == proposal
    assert ballot.source == "llm"


async def test_merchant_accepts_proposal_keys_with_nonbreaking_spaces(llm_reply):
    llm_reply["proposal"] = {"land_delta": 30, "feed\u00a0": 1800, "plant\u00a0": 0}
    brief = brief_for("merchant")
    ballot = await agent.decide("merchant", brief)
    assert validate_reply(ballot, brief) is ballot
    assert ballot.proposal.model_dump() == {"land_delta": 30, "feed": 1800, "plant": 0}
    assert ballot.source == "llm" and ballot.error is None


async def test_model_alone_selects_accepts_and_backed_plan(llm_reply):
    proposal = Plan(land_delta=0, feed=0, plant=500)
    tabled = TabledProposal(id="profiteer-plan", author="profiteer", plan=proposal)
    alternative = TabledProposal(id="provider-plan", author="provider",
                                plan=Plan(land_delta=0, feed=2000, plant=500))
    brief = brief_for(table=[tabled, alternative])
    llm_reply.update(back=tabled.id, accept=[tabled.id, alternative.id, tabled.id])
    ballot = await agent.decide("farmer", brief)
    assert validate_reply(ballot, brief) is ballot
    assert ballot.back == tabled.id and ballot.proposal == proposal
    assert ballot.accept == [tabled.id, alternative.id]
    llm_reply.update(back=None, accept=[])
    ballot = await agent.decide("farmer", brief)
    assert ballot.accept == []


async def test_agent_preserves_the_models_runoff_choice(llm_reply):
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
    {"proposal": {"land_delta": 0, "feed\u00a0": 100000, "plant\u00a0": 0}},
    {"proposal": {"land_delta": 0, "feed\u00a0": True, "plant\u00a0": 0}},
    {"proposal": {"land_delta": 0, "feed\u00a0": 2000, "plant\u00a0": 0, "extra ": 1}},
    {"proposal": {"land_delta": 0, "feed": 2000, "feed\u00a0": 1800, "plant": 0}},
    {"accept": ["missing-plan"]},
    {"back": "missing-plan"},
])
async def test_invalid_model_choices_are_rejected(llm_reply, changes):
    llm_reply.update(changes)
    brief = brief_for()
    ballot = await agent.decide("farmer", brief)
    assert ballot.error and ballot.accept == []
    assert "chosen" not in ballot.trace
    with pytest.raises(ValueError):
        validate_reply(ballot, brief)


async def test_unseated_speaker_cannot_receive_a_model_vote(llm_reply):
    roster = resolve_council([{"id": "merchant"}])
    brief = brief_for("farmer", advisors=[a.public() for a in roster.values()])
    ballot = await agent.decide("farmer", brief)
    assert "not seated" in ballot.error
    assert ballot.accept == [] and "chosen" not in ballot.trace
    with pytest.raises(ValueError, match="not seated"):
        validate_reply(ballot, brief)


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
    monkeypatch.setattr(llm, "_cache_path", lambda m, temperature=0.4: tmp_path / "reply.json")
    r = await llm.chat([{"role": "user", "content": "hi"}])
    assert r.content == '{"ok": 1}' and len(calls) == 3
    again = await llm.chat([{"role": "user", "content": "hi"}])
    assert again.cached and len(calls) == 3
