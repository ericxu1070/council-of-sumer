"""Run-local personas retain grounded preferences and enforced red lines."""

import json
from dataclasses import replace

import pytest

from sumer import agent, llm
from sumer.advisors import (
    ADVISORS,
    Beliefs,
    CouncilSeat,
    Scorer,
    mentions,
    resolve_council,
    short,
)
from sumer.game import Decision, new_game
from sumer.protocol import Brief, validate_reply


CUSTOM = {
    "id": "custom-river",
    "short": "River Keeper",
    "mandate": "Keep every river family fed.",
    "profile": "farmer",
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
    [{**CUSTOM, "id": "BadId"}],
    [{**CUSTOM, "profile": "invented"}],
    [{**CUSTOM, "short": "  "}],
    [{**CUSTOM, "mandate": "  "}],
    [{**CUSTOM, "surprise": True}],
    [{**CUSTOM, "red_line": "No restrictions."}],
    [{"id": "farmer", "profile": "merchant"}],
])
def test_invalid_rosters_are_rejected(seats):
    with pytest.raises(ValueError):
        resolve_council(seats)


def test_public_roster_roundtrips_and_presets_remain_unchanged():
    original = [a.public() for a in ADVISORS.values()]
    roster = resolve_council([{"id": "builder"}, CUSTOM])
    public = [a.public() for a in roster.values()]
    assert [a.public() for a in resolve_council(public).values()] == public
    assert list(roster) == ["builder", "custom-river"]
    assert roster["custom-river"].red_line == ADVISORS["farmer"].red_line
    assert [a.public() for a in ADVISORS.values()] == original
    assert len(resolve_council()) == 7
    assert len(resolve_council([
        {**CUSTOM, "id": f"custom-{index}"} for index in range(21)
    ])) == 21


def test_subset_scorer_uses_only_seated_agents_and_custom_profile():
    state, _ = new_game(1)
    roster = resolve_council([CUSTOM])
    scorer = Scorer(state, Beliefs(), advisors=roster)
    preset_scorer = Scorer(state, Beliefs(), advisors=resolve_council([{"id": "farmer"}]))
    for plan in scorer.pool:
        assert scorer.norm("custom-river", plan) == preset_scorer.norm("farmer", plan)
        assert scorer.vetoes("custom-river", plan) == preset_scorer.vetoes("farmer", plan)
    assert scorer.vetoes("custom-river", Decision(0, 0, 500))
    assert set(scorer._range) == {"custom-river"}
    with pytest.raises(KeyError):
        scorer.norm("priest", scorer.pool[0])
    priest = resolve_council([{**CUSTOM, "profile": "priest"}])["custom-river"]
    assert priest.beliefs_for(Beliefs()).expected_yield == 1.5


def test_custom_name_mentions_do_not_import_unseated_aliases():
    roster = resolve_council([CUSTOM, {"id": "builder"}])
    assert short("custom-river", roster) == "River Keeper"
    assert mentions("River Keeper, Builder, Farmer, hear me.", advisors=roster) == [
        "custom-river", "builder"
    ]


async def test_single_custom_agent_can_decide_and_trace_without_other_seats():
    roster = resolve_council([CUSTOM])
    brief = brief_for(roster)
    ballot = await agent.decide("custom-river", brief)
    assert validate_reply(ballot, brief) is ballot
    assert not ballot.error
    assert "custom-river" == ballot.advisor
    assert 0 <= ballot.trace["chosen"]["group_score"] <= 1
    assert not ballot.trace["chosen"]["breaks_red_lines_of"]
    assert "river family" in ballot.speech


@pytest.mark.parametrize("crosses_red_line", [False, True])
async def test_custom_llm_prompt_roster_and_hard_policy_enforcement(monkeypatch, crosses_red_line):
    roster = resolve_council([CUSTOM, {"id": "builder"}, {"id": "merchant"}])
    brief = brief_for(roster)
    prompts = []
    monkeypatch.setattr(agent, "settings", replace(agent.settings, llm_mode="bayleaf"))

    async def reply(messages):
        prompts.extend(messages)
        return llm.Reply(json.dumps({
            "proposal": {"land_delta": 0, "feed": 0 if crosses_red_line else 2000, "plant": 1000},
            "accept": [], "speech": "Builder, keep the river families fed.",
            "thought": "The river families need food.",
        }), "test", False, 0.0, 0.0)

    monkeypatch.setattr(llm, "chat", reply)
    ballot = await agent.decide("custom-river", brief)
    system = prompts[0]["content"]
    assert "3 advisors govern" in system
    assert "all 3 accept" in system
    assert "2 of 3 accepts" in system
    assert CUSTOM["mandate"] in system
    assert "River Keeper" in system and 'High Priest' not in system
    if crosses_red_line:
        assert "red line" in ballot.error
        with pytest.raises(ValueError, match="red line"):
            validate_reply(ballot, brief)
    else:
        assert validate_reply(ballot, brief) is ballot
        assert not ballot.error and ballot.source == "llm"
        assert ballot.addressed == ["builder"]


def test_council_seat_is_strict():
    with pytest.raises(ValueError):
        CouncilSeat(id=1)
