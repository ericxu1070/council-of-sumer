import asyncio
from dataclasses import replace

import pytest
from fastapi import HTTPException

from sumer.advisors import resolve_council, safe_plan
from sumer.chamber import play_council
from sumer.game import State, roll_game
from sumer.protocol import Ballot, Plan, bind
from sumer.scouts import apply_scout
from sumer.storage import Store


@pytest.mark.parametrize("choice, index, field, value, belief", [
    ("plague", 0, "plague", True, "plague_chance"),
    ("rats", 0, "rat_divisor", 2, "rat_fraction"),
    ("good_harvest", 0, "yield_per_acre", 5, "expected_yield"),
    ("bad_harvest", 0, "yield_per_acre", 1, "expected_yield"),
    ("low_price", 1, "land_price", 17, "next_price"),
    ("high_price", 1, "land_price", 26, "next_price"),
])
def test_scout_changes_only_its_event_and_updates_public_beliefs(
    choice, index, field, value, belief,
):
    rolls = roll_game(1)
    original = list(rolls)
    report = apply_scout(rolls, 1, choice)
    expected = list(original)
    expected[index] = replace(original[index], **{field: value})
    assert rolls == expected
    assert report["kind"] == choice and report["injected"]
    assert belief in report["beliefs"]


def test_skipping_preserves_rolls_and_unknown_events_are_rejected():
    rolls = roll_game(1)
    original = list(rolls)
    assert apply_scout(rolls, 1, "skip") is None
    with pytest.raises(ValueError, match="listed scout"):
        apply_scout(rolls, 1, "unknown")
    assert rolls == original


@pytest.mark.parametrize("phase", ["discuss", "runoff"])
async def test_scout_revote_beyond_round_limit_can_commit_with_fresh_votes(phase):
    journal = Store(":memory:")
    roster = resolve_council([{"id": "provider"}, {"id": "farmer"}])
    briefs = []
    choices = []

    async def ask(brief):
        briefs.append(brief.model_copy(deep=True))
        if brief.phase == "runoff":
            finalist = brief.runoff[0]
            return bind(Ballot(
                advisor=brief.speaker, year=brief.year, round=brief.round,
                phase="runoff", proposal=finalist.plan, runoff_vote=finalist.id,
                speech="I back this budget.",
            ), brief)
        state = State(**{k: v for k, v in brief.state.items()
                         if k not in {"starved", "acres_per_person"}})
        budget = safe_plan(state).as_dict()
        if phase == "runoff" and brief.speaker == "farmer":
            budget["plant"] = max(0, budget["plant"] - 2)
        plan = Plan(**budget)
        back = next((t.id for t in brief.table if t.plan == plan), None)
        return bind(Ballot(
            advisor=brief.speaker, year=brief.year, round=brief.round,
            proposal=plan, back=back, accept=[back] if back else [],
            speech="I support this budget.",
        ), brief)

    async def scout(year, round_no, vote_phase, rows):
        choice = "good_harvest" if year == 1 and vote_phase == phase and not any(
            c["choice"] == "good_harvest" for c in choices
        ) else "skip"
        data = {"year": year, "round": round_no, "phase": vote_phase, "choice": choice}
        choices.append(data)
        assert sum(len(row["accept"]) for row in rows) == len(roster)
        journal.append("scout-proof", "scout_choice", data)
        return choice

    result = await play_council(
        1, ask, advisors=roster, store=journal, run_id="scout-proof",
        max_rounds=1, ask_scout=scout,
    )
    events = journal.events(result.run_id)
    assert result.status == "completed", [e for e in events if e["kind"] == "technical_failure"]
    revotes = [b for b in briefs if b.year == 1 and b.phase == phase and b.round > 0]
    if phase == "runoff":
        revotes = [b for b in revotes if b.round > 1]
    assert len(revotes) == len(roster)
    assert all(b.beliefs["expected_yield"] == 5 for b in revotes)
    if phase == "discuss":
        assert all(not t.accept for t in revotes[0].table)
    first_year = next(e["data"] for e in events if e["kind"] == "year")
    assert first_year["random_inputs"]["yield_per_acre"] == 5
    journal.db.close()


async def test_pending_scout_rejects_stale_invalid_and_paused_choices(monkeypatch):
    from sumer import web

    journal = Store(":memory:")
    journal.create_run("live-scout", {})
    monkeypatch.setattr(web, "store", journal)
    monkeypatch.setattr(web, "live", web.Live())
    web.live.run_id = "live-scout"
    pending = asyncio.create_task(web._ask_scout(1, 0, "discuss", []))
    web.live.task = pending
    try:
        await asyncio.sleep(0)
        request = web.live.scout_request
        assert (await web.meta())["scout_pending"] == request
        req = web.ScoutReq(run_id="live-scout", request_id=request["request_id"], choice="skip")
        for changed, status in [({"request_id": "stale"}, 409), ({"choice": "unknown"}, 422)]:
            with pytest.raises(HTTPException) as error:
                await web.scout(req.model_copy(update=changed))
            assert error.value.status_code == status
        await web.pause(web.PauseReq(run_id="live-scout", paused=True))
        with pytest.raises(HTTPException) as error:
            await web.scout(req)
        assert error.value.status_code == 409 and not pending.done()
        await web.pause(web.PauseReq(run_id="live-scout", paused=False))
        assert await web.scout(req) == {"ok": True}
        assert await asyncio.wait_for(pending, 1) == "skip"
        assert web.live.scout_pending is None and web.live.scout_request is None
        assert [e["kind"] for e in journal.events("live-scout")] == [
            "scout_request", "pause", "pause", "scout_choice",
        ]
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        journal.db.close()
