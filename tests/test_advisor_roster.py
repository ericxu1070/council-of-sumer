"""Run-local personas are defined by names and free-form LLM mandates."""

import json

import pytest

from sumer import agent, llm
from sumer.advisors import (
    ADVISORS,
    COMPROMISE_GUIDANCE,
    Beliefs,
    mentions,
    resolve_council,
    short,
)
from sumer.game import new_game
from sumer.protocol import Brief, validate_reply

CUSTOM = {
    "id": "custom-river",
    "short": "River Keeper",
    "mandate": "Protect the river and represent the boat builders.",
}


def brief_for(roster, speaker="custom-river", **updates):
    state, _ = new_game(1)
    return Brief(seed=1, year=1, round=0, max_rounds=3, state=state.public(),
                 beliefs=Beliefs().__dict__, speaker=speaker,
                 advisors=[a.public() for a in roster.values()], **updates)


@pytest.mark.parametrize("seats", [
    [],
    [{"id": "farmer"}] * 22,
    [{"id": "farmer"}, {"id": "farmer"}],
    [{"id": "custom-x"}],
    [{**CUSTOM, "id": "../../farmer"}],
    [{**CUSTOM, "mandate": "  "}],
    [{**CUSTOM, "surprise": True}],
])
def test_invalid_rosters_are_rejected(seats):
    with pytest.raises(ValueError):
        resolve_council(seats)


def test_custom_name_mentions_do_not_import_unseated_aliases():
    roster = resolve_council([CUSTOM, {"id": "farmer"}])
    assert short("custom-river", roster) == "River Keeper"
    assert mentions("River Keeper, Farmer, Provider, hear me.", advisors=roster) == [
        "custom-river", "farmer"
    ]


def test_duplicate_display_names_are_numbered_and_addressed_independently():
    roster = resolve_council([{"id": "farmer"}, {
        "id": "custom-farmer", "short": "Farmer", "mandate": "Represent the farmers."
    }])
    assert [a.short for a in roster.values()] == ["Farmer1", "Farmer2"]
    assert mentions("Farmer2, I agree.", advisors=roster) == ["custom-farmer"]
    assert [a.public() for a in resolve_council([
        a.public() for a in roster.values()
    ]).values()] == [a.public() for a in roster.values()]


@pytest.mark.parametrize("feed", [0, 2000])
async def test_custom_mandate_reaches_model_and_model_selects_budget(monkeypatch, feed):
    roster = resolve_council([CUSTOM, {"id": "farmer"}, {"id": "merchant"}])
    brief = brief_for(roster)
    prompts = []

    async def reply(messages):
        prompts.extend(messages)
        return llm.Reply(json.dumps({
            "proposal": {"land_delta": 0, "feed": feed, "plant": 1000},
            "accept": [], "speech": "Farmer, keep the river open for our boats.",
            "thought": "The river and the boat builders guide my decision.",
        }), "test", False, 0.0, 0.0)

    monkeypatch.setattr(llm, "chat", reply)
    ballot = await agent.decide("custom-river", brief)
    system = prompts[0]["content"]
    assert CUSTOM["mandate"] in system
    assert COMPROMISE_GUIDANCE in system
    assert all(COMPROMISE_GUIDANCE in a.mandate for a in ADVISORS.values())
    assert "River Keeper" in system
    assert validate_reply(ballot, brief) is ballot
    assert not ballot.error and ballot.proposal.feed == feed
    assert ballot.addressed == ["farmer"]


@pytest.mark.parametrize("bias", [-0.01, 1.01, float("nan"), float("inf"), "0.5", True])
def test_invalid_bias_rejected(bias):
    with pytest.raises(ValueError):
        resolve_council([{"id": "provider", "bias": bias}])


def test_bias_defaults_overrides_and_roster_roundtrip():
    assert list(ADVISORS) == ["provider", "farmer", "merchant", "priest", "villager"]
    assert ADVISORS["villager"].bias == 0
    assert all(a.bias == 0.25 for k, a in ADVISORS.items() if k != "villager")
    roster = resolve_council([
        {"id": "provider", "bias": 0}, {"id": "priest", "bias": 1},
        {**CUSTOM, "bias": 0.6},
        {"id": "custom-priest", **{k: v for k, v in ADVISORS["priest"].public().items()
                                   if k != "id"}},
    ])
    assert [a.bias for a in roster.values()] == [0, 1, 0.6, 0.25]
    public = [a.public() for a in roster.values()]
    assert [a.public() for a in resolve_council(public).values()] == public
    assert ADVISORS["provider"].bias == 0.25


@pytest.mark.parametrize("phase", ["turn", "runoff"])
def test_shared_score_mandate_and_bias_reach_every_voting_phase(phase):
    from sumer.advisors import VILLAGER_MANDATE
    from sumer.protocol import Plan, TabledProposal

    roster = resolve_council([{"id": "priest", "bias": 0.8}, {"id": "villager"}])
    a = TabledProposal(id="a", author="priest", plan=Plan(land_delta=0, feed=2000, plant=500))
    b = TabledProposal(id="b", author="villager", plan=Plan(land_delta=0, feed=1900, plant=500))
    brief = brief_for(roster, speaker="priest", phase=phase,
                      table=[a, b], runoff=[a, b] if phase == "runoff" else [])
    system = agent._llm_messages("priest", brief)[0]["content"]
    assert VILLAGER_MANDATE in system
    assert "Your bias value: 0.8" in system
    assert '"Villager", bias 0.0' in system
    assert "emergencies" in system
    assert "common enemy" in system
    assert all(VILLAGER_MANDATE in a.mandate for a in ADVISORS.values())
    assert ADVISORS["villager"].mandate == f"{VILLAGER_MANDATE} {COMPROMISE_GUIDANCE}"


def test_unbiased_diagnostic_matches_game_score_and_overthrow_penalty():
    from dataclasses import replace

    from sumer.advisors import forecast
    from sumer.game import Decision, State, rating

    state = State(starved_total=7)
    beliefs = Beliefs(expected_yield=3, rat_chance=0, plague_chance=0)
    for feed in (2000, 1100, 1080):  # exactly 45% is allowed; 46% is an overthrow
        f = forecast(state, Decision(0, feed, 500), beliefs)
        final = State(population=f.population, grain=f.grain, acres=f.acres,
                      starved_total=state.starved_total + f.starved,
                      impeached=f.starved_frac > 0.45)
        for advisor in ADVISORS.values():
            assert round(replace(advisor, bias=0).utility(f, state), 1) == rating(final)["score"]
