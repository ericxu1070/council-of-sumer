import pytest

from sumer.advisors import ADVISOR_IDS, safe_plan
from sumer.deliberation import Deadlock, deliberate_year, speaking_order
from sumer.game import Decision, new_game
from sumer.protocol import Ballot, Brief, Plan, bind, validate_reply

A, B, C = (0, 2000, 1000), (0, 2000, 900), (0, 2000, 800)


def _ballot(brief, plan=A, back=None, accept=(), vote=None, speech="Farmer, I agree."):
    if brief.phase == "discuss" and back is None and len(accept) == 1:
        back = accept[0]
    if brief.phase == "runoff" and vote:
        plan = next(t.plan for t in brief.runoff if t.id == vote)
        plan = (plan.land_delta, plan.feed, plan.plant)
    return Ballot(
        advisor=brief.speaker, year=brief.year, round=brief.round, phase=brief.phase,
        proposal=Plan(land_delta=plan[0], feed=plan[1], plant=plan[2]), back=back,
        accept=list(accept), runoff_vote=vote, speech=speech,
    )


def scripted(fn):
    """An ask() whose advisors follow a script; ballots are bound to requests like real ones."""
    briefs = []

    async def ask(brief):
        briefs.append(brief)
        b = fn(brief)
        return bind(b, brief) if b else None

    ask.briefs = briefs
    return ask


def _ids_for(brief, plan):
    return [t.id for t in brief.table if (t.plan.land_delta, t.plan.feed, t.plan.plant) == plan]


async def _year(ask, **kw):
    state, _ = new_game(1)
    events = []

    async def emit(kind, data):
        events.append((kind, data))

    yr = await deliberate_year(seed=1, state=state, ask=ask, history=[], max_rounds=6,
                               scheduled_news=None, emit=emit, **kw)
    return yr, events


# ---- rules of the game ----

def test_safe_plan_feeds_everyone():
    state, _ = new_game(2)
    state.grain = 500  # far too little; the fallback must sell land to eat
    d = safe_plan(state)
    assert d.land_delta < 0
    assert d.feed > 500


# ---- the wire ----

@pytest.mark.parametrize("changed", [
    {"advisor": "merchant"}, {"run_id": "another-run"}, {"state_hash": "stale-state"},
    {"year": 2}, {"round": 1}, {"phase": "runoff"}, {"reply_to": "old-request"},
    {"request_hash": "changed-request"},
])
def test_ballot_must_match_its_speaker_and_request(changed):
    state, _ = new_game(1)
    brief = Brief(seed=1, year=1, round=0, max_rounds=6, state=state.public(),
                  beliefs={}, speaker="provider")
    ballot = bind(_ballot(brief), brief)
    assert validate_reply(ballot, brief) is ballot
    with pytest.raises(ValueError, match="request"):
        validate_reply(ballot.model_copy(update=changed), brief)


# ---- the sequential protocol ----

async def test_turns_are_sequential_and_see_earlier_speakers():
    seen = []

    async def ask(brief):
        seen.append(brief)
        return bind(_ballot(brief, A, accept=_ids_for(brief, A)), brief)

    yr, events = await _year(ask)
    turns = [d for k, d in events if k == "turn"]
    assert len(turns) == yr.turns > 0
    for brief in seen:
        if brief.phase != "discuss":
            continue
        assert brief.order == speaking_order(brief.year, brief.round)
        assert brief.speaker == brief.order[brief.turn]
        # every earlier speaker this round is in the brief, in order
        assert [t["advisor"] for t in brief.turns_this_round] == brief.order[: brief.turn]
        # and every plan an earlier speaker tabled this round is already on the table
        tabled = {t.id for t in brief.table}
        assert {t["plan_id"] for t in brief.turns_this_round} <= tabled
    assert speaking_order(1, 1)[0] != speaking_order(1, 0)[0]  # the seat rotates


async def test_strong_majority_after_round_two_records_dissent():
    def script(b):
        if b.speaker == "imposter":
            return _ballot(b, B)
        return _ballot(b, A, accept=_ids_for(b, A))

    yr, _ = await _year(scripted(script))
    assert yr.outcome == "majority"
    assert yr.rounds == 2  # adopted the moment round 2 completes
    assert set(yr.dissent) == {"imposter"}


def _three_way(b):
    camp = {"provider": A, "farmer": A, "merchant": B, "profiteer": B,
            "imposter": C}[b.speaker]
    return _ballot(b, camp, accept=_ids_for(b, camp))


async def test_runoff_when_best_plan_has_two_of_five():
    def script(b):
        if b.phase == "runoff":
            a, bb = b.runoff
            pick = a.id if b.speaker in ("provider", "farmer", "merchant") else bb.id
            return _ballot(b, A, vote=pick)
        return _three_way(b)

    ask = scripted(script)
    yr, events = await _year(ask)
    kinds = [k for k, _ in events]
    assert "runoff" in kinds and "runoff_result" in kinds
    assert yr.outcome == "runoff"
    assert yr.runoff["trigger"] == "floor"
    assert yr.plan == Decision(*A)
    assert set(yr.dissent) == {"profiteer", "imposter"}
    runoff_turns = [d for k, d in events if k == "turn" and d["phase"] == "runoff"]
    assert len(runoff_turns) == 5 and all(t["runoff_vote"] for t in runoff_turns)


async def test_deadlock_without_a_human_remains_paused():
    def script(b):
        i = ADVISOR_IDS.index(b.speaker)
        return _ballot(b, (0, 2000, 1000 - 10 * i))  # five plans, nobody accepts another

    with pytest.raises(Deadlock, match="Deadlock"):
        await _year(scripted(script))


async def test_stale_ballot_skips_after_one_retry():
    def script(b):
        return _ballot(b, A, accept=_ids_for(b, A))

    inner = scripted(script)

    async def ask(brief):
        b = await inner(brief)
        if brief.speaker == "merchant":
            b.reply_to = "previous-request"
        return b

    result, events = await _year(ask)
    assert result.outcome == "majority" and result.dissent == ["merchant"]
    skipped = [d for k, d in events if k == "agent_skipped"]
    assert len(skipped) == 2 and all(d["advisor"] == "merchant" for d in skipped)
    assert len([b for b in inner.briefs if b.speaker == "merchant"]) == 4
    assert "merchant" not in {b["advisor"] for b in result.certificate["ballots"]}


# ---- model response parsing ----

def test_parse_json_salvages_truncated_reply():
    from sumer.llm import parse_json

    cut = '{"proposal": {"land_delta": -81, "feed": 1940, "plant": 770},\n "accept": ["x"],\n "spee'
    assert parse_json(cut)["proposal"]["feed"] == 1940
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
