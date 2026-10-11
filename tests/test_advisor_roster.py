"""Run-local personas are defined by names and free-form LLM mandates."""

import json

import pytest

from sumer import agent, llm
from sumer.advisors import (
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
    assert "River Keeper" in system
    assert validate_reply(ballot, brief) is ballot
    assert not ballot.error and ballot.proposal.feed == feed
    assert ballot.addressed == ["farmer"]
