"""Run-local personas are defined by names and free-form LLM mandates."""

import json

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
    [{**CUSTOM, "id": "BadId"}],
    [{**CUSTOM, "short": "  "}],
    [{**CUSTOM, "mandate": "  "}],
    [{**CUSTOM, "surprise": True}],
    [{**CUSTOM, "red_line": "No restrictions."}],
    [{**CUSTOM, "profile": "farmer"}],
])
def test_invalid_rosters_are_rejected(seats):
    with pytest.raises(ValueError):
        resolve_council(seats)


def test_custom_agent_requires_only_name_and_mandate_with_generic_style():
    advisor = resolve_council([CUSTOM])["custom-river"]
    assert advisor.short == "River Keeper" and advisor.title == "River Keeper"
    assert advisor.mandate == CUSTOM["mandate"]
    assert advisor.voice == "clear, thoughtful, speaks from the public mandate"
    assert advisor.color == "#607d8b" and advisor.emoji == "🗣️"
    assert not {"profile", "red_line"} & advisor.public().keys()


def test_public_roster_roundtrips_and_presets_remain_unchanged():
    original = [a.public() for a in ADVISORS.values()]
    roster = resolve_council([{"id": "builder"}, CUSTOM])
    public = [a.public() for a in roster.values()]
    assert [a.public() for a in resolve_council(public).values()] == public
    assert list(roster) == ["builder", "custom-river"]
    assert [a.public() for a in ADVISORS.values()] == original
    assert len(resolve_council()) == 7
    assert len(resolve_council([
        {**CUSTOM, "id": f"custom-{index}"} for index in range(21)
    ])) == 21


def test_diagnostic_scorer_supports_custom_ids_without_a_preset_profile():
    state, _ = new_game(1)
    roster = resolve_council([CUSTOM])
    scorer = Scorer(state, Beliefs(), advisors=roster)
    plan = Decision(0, 2000, 1000)
    forecast = scorer.forecast("custom-river", plan)
    assert roster["custom-river"].utility(forecast, state) == forecast.wealth
    assert 0 <= scorer.norm("custom-river", plan) <= 1
    assert set(scorer._range) == {"custom-river"}
    with pytest.raises(KeyError):
        scorer.norm("priest", plan)


def test_custom_name_mentions_do_not_import_unseated_aliases():
    roster = resolve_council([CUSTOM, {"id": "builder"}])
    assert short("custom-river", roster) == "River Keeper"
    assert mentions("River Keeper, Builder, Farmer, hear me.", advisors=roster) == [
        "custom-river", "builder"
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


async def test_single_custom_agent_decides_only_from_the_model(llm_reply):
    roster = resolve_council([CUSTOM])
    brief = brief_for(roster)
    llm_reply["speech"] = "I support the river and its boat builders."
    ballot = await agent.decide("custom-river", brief)
    assert validate_reply(ballot, brief) is ballot
    assert not ballot.error and ballot.source == "llm"
    assert ballot.advisor == "custom-river"
    assert ballot.trace["chosen"] == {"plan": llm_reply["proposal"]}
    assert not {"group_score", "my_score", "breaks_red_lines_of"} & ballot.trace["chosen"].keys()
    prompt = agent._llm_messages("custom-river", brief)[0]["content"]
    assert "sole advisor" in prompt and "all 1 accept" in prompt


@pytest.mark.parametrize("feed", [0, 2000])
async def test_custom_llm_prompt_and_votes_do_not_use_deterministic_preferences(monkeypatch, feed):
    roster = resolve_council([CUSTOM, {"id": "builder"}, {"id": "merchant"}])
    brief = brief_for(roster)
    prompts = []

    async def reply(messages):
        prompts.extend(messages)
        return llm.Reply(json.dumps({
            "proposal": {"land_delta": 0, "feed": feed, "plant": 1000},
            "accept": [], "speech": "Builder, keep the river open for our boats.",
            "thought": "The river and the boat builders guide my decision.",
        }), "test", False, 0.0, 0.0)

    monkeypatch.setattr(llm, "chat", reply)
    ballot = await agent.decide("custom-river", brief)
    system = prompts[0]["content"]
    assert "3 advisors govern" in system and "all 3 accept" in system
    assert "2 of 3 accepts" in system
    assert CUSTOM["mandate"] in system
    assert "River Keeper" in system and "High Priest" not in system
    for removed in ("suggested_plan", "your score", "threshold", "red line", "policy_vote"):
        assert removed not in json.dumps(prompts)
    assert validate_reply(ballot, brief) is ballot
    assert not ballot.error and ballot.proposal.feed == feed
    assert ballot.addressed == ["builder"]


def test_council_seat_is_strict():
    with pytest.raises(ValueError):
        CouncilSeat(id=1)
