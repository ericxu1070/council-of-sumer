import json
import time

import pytest
from pydantic import ValidationError

from sumer import agent, llm
from sumer.advisors import ADVISORS, Beliefs, CouncilSeat, resolve_council
from sumer.game import State
from sumer.protocol import Brief, Plan, TabledProposal, validate_reply
from sumer.rulebooks import EXAMPLE_RULEBOOKS
from tests.test_council_setup_api import chamber  # noqa: F401


def brief_for(seats, speaker="farmer", phase="discuss", **kwargs):
    roster = resolve_council(seats)
    table = [TabledProposal(id=pid, author=aid,
                            plan=Plan(land_delta=0, feed=2000, plant=1000))
             for pid, aid in (("P1", "farmer"), ("P2", "merchant"))]
    return Brief(seed=1, year=1, round=0, max_rounds=3, state=State().public(),
                 beliefs=Beliefs().__dict__, speaker=speaker, phase=phase,
                 advisors=[a.public() for a in roster.values()], table=table,
                 runoff=table if phase == "runoff" else [], **kwargs)


def test_no_rulebooks_are_initialized_and_roundtrip_preserves_independent_notes():
    assert all(a.rulebook == "" for a in ADVISORS.values())
    seats = [{"id": "farmer", "rulebook": 'First private claim.\n\n{"uncertain": true}'},
             {"id": "custom-farmer", "short": "Farmer", "mandate": "Represent farms.",
              "rulebook": "A different private claim."}, {"id": "merchant"}]
    roster = resolve_council(seats)
    assert [a.short for a in roster.values()] == ["Farmer1", "Farmer2", "Merchant"]
    assert [a.rulebook for a in roster.values()] == [s.get("rulebook", "") for s in seats]
    saved = [a.public() for a in roster.values()]
    assert [a.public() for a in resolve_council(saved).values()] == saved
    assert ADVISORS["farmer"].rulebook == ""


@pytest.mark.parametrize("invalid", [None, {}, [], 123, "x" * 8001])
def test_invalid_rulebook_input_is_rejected(invalid):
    with pytest.raises(ValidationError):
        CouncilSeat.model_validate({"id": "farmer", "rulebook": invalid})


def test_rulebook_limit_blank_and_internal_formatting():
    assert CouncilSeat(id="farmer", rulebook="x" * 8000).rulebook == "x" * 8000
    assert CouncilSeat(id="farmer", rulebook=" \n ").rulebook == ""
    assert CouncilSeat(id="farmer", rulebook="  First.\n\nSecond.  ").rulebook == "First.\n\nSecond."


@pytest.mark.parametrize("phase", ["discuss", "runoff"])
def test_full_prompts_keep_notes_private_and_do_not_restore_hidden_reference(phase):
    seats = [{"id": "farmer", "rulebook": "FARMER_ONLY_CLAIM"},
             {"id": "merchant", "rulebook": "MERCHANT_ONLY_CLAIM"}]
    for speaker, own, other in (("farmer", "FARMER_ONLY_CLAIM", "MERCHANT_ONLY_CLAIM"),
                                ("merchant", "MERCHANT_ONLY_CLAIM", "FARMER_ONLY_CLAIM")):
        brief = brief_for(seats, speaker, phase)
        messages = agent._llm_messages(speaker, brief)
        full = json.dumps(messages)
        system = messages[0]["content"]
        user = json.loads(messages[1]["content"].split("\n\n")[0])
        assert user["private_rulebook"] == own.strip()
        assert own not in system and other not in full
        assert "fallible claims" in system and "revise your beliefs" in system
        assert "not evidence that they are correct" in system
        assert "Do not fill gaps" in system
        for hidden in ("45%", "15%", "40%", "17 to 26", "1 to 5 bushels",
                       "expected_yield", "rat_chance", "plague_chance", "next_price"):
            assert hidden not in full
        assert "beliefs_about_this_year" not in user
        assert "budget_rules" not in user
        for hidden in ("plant // 2", "population * 10", "ten acres per person", "20 bushels"):
            assert hidden not in system
        assert user["city"] == brief.state


def test_blank_book_stays_unknown_and_new_evidence_remains_available():
    news = [{"text": "The field report revises yesterday's claim.",
             "beliefs": {"expected_yield": 5}}]
    turn = {"advisor": "merchant", "speech": "Farmer, my earlier claim was mistaken.",
            "plan_id": "P2", "plain": "Feed the city.", "accept": []}
    brief = brief_for([{"id": "farmer"}, {"id": "merchant", "rulebook": "PRIVATE_SENTINEL"}],
                      news=news, turns_this_round=[turn])
    messages = agent._llm_messages("farmer", brief)
    user = json.loads(messages[1]["content"].split("\n\n")[0])
    assert user["private_rulebook"] == ""
    assert user["news_this_year"] == [news[0]["text"]]
    assert turn["speech"] in messages[1]["content"]
    assert "PRIVATE_SENTINEL" not in json.dumps(messages)
    assert "empty rulebook means no private" in messages[0]["content"]


@pytest.mark.parametrize("phase", ["discuss", "runoff"])
@pytest.mark.parametrize("bias", [0.0, 0.5, 1.0])
async def test_model_receives_book_and_trace_records_actual_context(monkeypatch, phase, bias):
    brief = brief_for([{"id": "farmer", "bias": bias, "rulebook": "Owner's uncertain notes."},
                       {"id": "merchant", "bias": 1 - bias,
                        "rulebook": "Other private notes."}], phase=phase)
    seen = []

    async def reply(messages):
        seen.extend(messages)
        return llm.Reply(json.dumps({"proposal": {"land_delta": 0, "feed": 2000, "plant": 1000},
                                    "vote": "P1", "speech": "Merchant, that claim may be wrong.",
                                    "thought": "I should reconsider my notes."}),
                         "test", False, 0, 0)

    monkeypatch.setattr(llm, "chat", reply)
    ballot = await agent.decide("farmer", brief)
    validate_reply(ballot, brief)
    assert f"Your bias value: {bias}" in seen[0]["content"]
    assert "Owner's uncertain notes." in seen[1]["content"]
    assert "Other private notes." not in json.dumps(seen)
    assert ballot.trace["rulebook"] == "Owner's uncertain notes."
    assert "beliefs" not in ballot.trace


def test_api_validates_and_persists_rulebooks(chamber):  # noqa: F811
    client, journal, _ = chamber
    bad = client.post("/api/game", json={"advisors": [{"id": "farmer", "rulebook": "x" * 8001}]})
    assert bad.status_code == 422 and journal.runs() == []
    seats = [{"id": "farmer", "rulebook": "User chooses these notes.\nNothing added."},
             {"id": "merchant", "rulebook": ""}]
    response = client.post("/api/game", json={"seed": 1, "max_rounds": 2, "advisors": seats})
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        saved = client.get(f"/api/runs/{run_id}").json()
        if saved["status"] != "running":
            break
        time.sleep(0.01)
    assert saved["status"] != "running"
    assert [a["rulebook"] for a in saved["manifest"]["advisors"]] == [s["rulebook"] for s in seats]
    client.post("/api/stop")


def test_examples_are_opt_in_and_available_to_the_setup(chamber):  # noqa: F811
    client, _, _ = chamber
    meta = client.get("/api/meta").json()
    assert meta["example_rulebooks"] == EXAMPLE_RULEBOOKS
    assert all(a["rulebook"] == "" for a in meta["advisors"])
    assert all(a.rulebook == "" for a in resolve_council().values())


@pytest.mark.parametrize("phase", ["discuss", "runoff"])
def test_loaded_examples_share_only_the_speakers_estimates(phase):
    seats = [{"id": who, "rulebook": book} for who, book in EXAMPLE_RULEBOOKS.items()]
    for who, own in EXAMPLE_RULEBOOKS.items():
        messages = agent._llm_messages(who, brief_for(seats, speaker=who, phase=phase))
        user = json.loads(messages[1]["content"].split("\n\n")[0])
        assert user["private_rulebook"] == own.strip()
        assert "budget_rules" not in user
        assert "fallible heuristics" in messages[0]["content"]
        for other, text in EXAMPLE_RULEBOOKS.items():
            if other != who:
                assert text not in user.values()
                assert text.splitlines()[0] not in messages[1]["content"]


def test_unobserved_budget_results_are_not_disclosed_in_plan_descriptions():
    b = brief_for([{"id": "farmer"}, {"id": "merchant"}])
    for t in b.table:
        t.plain = "HIDDEN_FOOD_REQUIREMENT_DESCRIPTION"
    b.turns_this_round = [{"advisor": "merchant", "speech": "I suggest this budget.",
        "plan_id": "P2", "plain": "HIDDEN_FOOD_REQUIREMENT_DESCRIPTION", "accept": [],
        "proposal": b.table[0].plan.model_dump()}]
    assert "HIDDEN_FOOD_REQUIREMENT_DESCRIPTION" not in json.dumps(agent._llm_messages("farmer", b))
    b.phase = "runoff"
    b.runoff = b.table
    b.turns_this_round = []
    assert "HIDDEN_FOOD_REQUIREMENT_DESCRIPTION" not in json.dumps(agent._llm_messages("farmer", b))
