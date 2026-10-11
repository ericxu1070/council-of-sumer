import itertools
import os
from types import SimpleNamespace

os.environ["LLM_MODE"] = "mock"
os.environ["TRANSPORT"] = "inproc"

import pytest

from sumer.advisors import ADVISOR_IDS, Beliefs, Scorer, mentions, plain, safe_plan
from sumer.agent import clean, decide
from sumer.chamber import play_council, play_simple
from sumer.deliberation import Deadlock, TechnicalFailure, deliberate_year, speaking_order
from sumer.game import YEARS, Decision, advance, new_game, rating
from sumer.narrator import summarize
from sumer.protocol import Ballot, Brief, Plan, bind, validate_reply
from sumer.transport import inproc

A, B, C = (0, 2000, 1000), (0, 2000, 900), (0, 2000, 800)


def _ballot(brief, plan=A, back=None, accept=(), veto=(), vote=None, speech="Farmer, I agree."):
    if brief.phase == "runoff" and vote:
        plan = next(t.plan for t in brief.runoff if t.id == vote)
        plan = (plan.land_delta, plan.feed, plan.plant)
    return Ballot(
        advisor=brief.speaker, year=brief.year, round=brief.round, phase=brief.phase,
        proposal=Plan(land_delta=plan[0], feed=plan[1], plant=plan[2]), back=back,
        accept=list(accept), veto=list(veto), runoff_vote=vote, speech=speech,
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

def test_same_seed_same_dice():
    assert new_game(3)[1] == new_game(3)[1]
    assert new_game(3)[1] != new_game(4)[1]


def test_council_and_reign_size():
    assert len(ADVISOR_IDS) == 7
    assert {"builder", "healer", "diplomat"} <= set(ADVISOR_IDS)
    assert YEARS == 5


def test_safe_plan_feeds_everyone():
    state, _ = new_game(2)
    state.grain = 500  # far too little; the fallback must sell land to eat
    d = safe_plan(state)
    assert d.land_delta < 0
    assert d.feed > 500


def test_red_lines_veto():
    state, _ = new_game(1)
    sc = Scorer(state, Beliefs())
    assert sc.vetoes("farmer", Decision(0, 0, 500))  # everyone starves
    assert sc.vetoes("general", Decision(-300, 2000, 500))  # sells 30% of land
    assert not sc.vetoes("merchant", Decision(-300, 0, 0))  # no red line
    assert sc.vetoes("builder", Decision(0, 2000, 100))  # leaves most sowable land fallow
    assert sc.vetoes("healer", Decision(0, 1700, 1000))  # feeds 85% of what people need
    assert sc.vetoes("diplomat", Decision(110, 0, 0))  # buys 11% more land
    assert not sc.vetoes("diplomat", Decision(90, 0, 0))


def test_plain_descriptions():
    state, _ = new_game(1)
    assert plain(Decision(50, 2000, 900), state) == "Buy 50 acres, feed everyone, plant 900 acres"
    assert "feed 90% of people" in plain(Decision(0, 1800, 900), state)


def test_mentions_finds_names():
    said = "Builder, your canal plan starves the east. Physician, Envoy: hold firm."
    assert mentions(said, exclude="farmer") == ["builder", "healer", "diplomat"]
    assert mentions("Generally we agree", exclude="farmer") == []


# ---- the wire ----

@pytest.mark.parametrize("changed", [
    {"advisor": "merchant"}, {"run_id": "another-run"}, {"state_hash": "stale-state"},
    {"year": 2}, {"round": 1}, {"phase": "runoff"}, {"reply_to": "old-request"},
    {"request_hash": "changed-request"},
])
def test_ballot_must_match_its_speaker_and_request(changed):
    state, _ = new_game(1)
    brief = Brief(seed=1, year=1, round=0, max_rounds=6, state=state.public(),
                  beliefs={}, speaker="priest")
    ballot = bind(_ballot(brief), brief)
    assert validate_reply(ballot, brief) is ballot
    with pytest.raises(ValueError, match="request"):
        validate_reply(ballot.model_copy(update=changed), brief)


async def test_trace_is_private_and_present():
    state, _ = new_game(1)
    brief = Brief(seed=1, year=1, round=0, max_rounds=6, state=state.public(),
                  beliefs=Beliefs().__dict__, speaker="priest")
    b = await decide("priest", brief)
    assert b.trace["thought"] and "chosen" in b.trace
    assert b.trace["beliefs"]["pessimistic"]  # the Priest plans for a bad year
    assert len(b.speech.split()) <= 28


# ---- the sequential protocol ----

async def test_turns_are_sequential_and_see_earlier_speakers():
    seen = []

    async def ask(brief):
        seen.append(brief)
        return await inproc.ask(brief)

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


async def test_consensus_when_all_seven_back_one_plan():
    yr, _ = await _year(scripted(lambda b: _ballot(b, A, accept=_ids_for(b, A))))
    assert yr.outcome == "consensus"
    assert yr.rounds == 1 and yr.turns == 7
    assert yr.plan == Decision(*A)


async def test_strong_majority_after_round_three_records_dissent():
    def script(b):
        if b.speaker == "priest":  # keeps changing its plan, so the talk never stalls
            return _ballot(b, B if b.round % 2 else (0, 2000, 850))
        if b.speaker == "merchant":
            return _ballot(b, C)
        return _ballot(b, A, accept=_ids_for(b, A))

    yr, _ = await _year(scripted(script))
    assert yr.outcome == "majority"
    assert yr.rounds == 3  # adopted the moment round 3 completes
    assert set(yr.dissent) == {"priest", "merchant"}


def _three_way(b):
    camp = {"farmer": A, "priest": A, "general": A, "merchant": B, "builder": B,
            "healer": C, "diplomat": C}[b.speaker]
    return _ballot(b, camp, accept=_ids_for(b, camp))


async def test_runoff_when_best_plan_has_three_of_seven():
    def script(b):
        if b.phase == "runoff":
            a, bb = b.runoff
            pick = a.id if b.speaker in ("farmer", "priest", "general", "merchant") else bb.id
            return _ballot(b, A, vote=pick)
        return _three_way(b)

    ask = scripted(script)
    yr, events = await _year(ask)
    kinds = [k for k, _ in events]
    assert "runoff" in kinds and "runoff_result" in kinds
    assert yr.outcome == "runoff"
    assert yr.runoff["trigger"] == "three"
    assert yr.plan == Decision(*A)
    assert set(yr.dissent) == {"builder", "healer", "diplomat"}
    runoff_turns = [d for k, d in events if k == "turn" and d["phase"] == "runoff"]
    assert len(runoff_turns) == 7 and all(t["runoff_vote"] for t in runoff_turns)


async def test_runoff_winner_with_a_red_line_veto_escalates():
    asked = {}

    async def human(options, reason):
        asked["reason"], asked["options"] = reason, options
        return "safe"

    def script(b):
        if b.phase == "runoff":
            a, bb = b.runoff
            if b.speaker == "healer":  # A crosses the Physician's red line
                return _ballot(b, A, vote=bb.id, veto=[a.id])
            pick = a.id if b.speaker in ("farmer", "priest", "general", "merchant") else bb.id
            return _ballot(b, A, vote=pick)
        return _three_way(b)

    yr, events = await _year(scripted(script), ask_human=human)
    assert yr.outcome == "escalated"
    assert yr.human_choice == "safe"
    assert "red line" in asked["reason"]
    assert all("plain" in o for o in asked["options"])
    assert any(k == "escalate" for k, _ in events)


async def test_deadlock_without_a_human_remains_paused():
    def script(b):
        i = ADVISOR_IDS.index(b.speaker)
        return _ballot(b, (0, 2000, 1000 - 10 * i))  # seven plans, nobody accepts another

    with pytest.raises(Deadlock, match="Deadlock"):
        await _year(scripted(script))


async def test_stale_ballot_stops_after_one_retry():
    def script(b):
        return _ballot(b, A, accept=_ids_for(b, A))

    inner = scripted(script)

    async def ask(brief):
        b = await inner(brief)
        if brief.speaker == "merchant":
            b.reply_to = "previous-request"
        return b

    with pytest.raises(TechnicalFailure, match="merchant failed after one retry"):
        await _year(ask)


# ---- whole games ----

async def test_council_survives_and_beats_averaging():
    events = []

    async def emit(kind, data):
        events.append((kind, data))

    council = await play_council(1, inproc.ask, emit=emit)
    average = await play_simple(1, "average")
    assert not council.rating["impeached"]
    assert council.rating["score"] > average.rating["score"]
    assert set(council.satisfaction) == set(ADVISOR_IDS)
    assert len(council.years) == YEARS
    game = next(d for k, d in events if k == "game")
    assert game["years"] == YEARS and len(game["advisors"]) == 7
    assert {"id", "title", "short", "mandate", "red_line", "color", "emoji"} <= set(
        game["advisors"][0]
    )
    years = [d for k, d in events if k == "year"]
    assert all(y["summary"].startswith(f"Year {y['year']}.") for y in years)
    assert all("narration_url" in y and y["plain"] for y in years)
    assert rating(new_game(1)[0])["verdict"]


async def test_narrator_is_plain_prose():
    state, rolls = new_game(1)
    yr, _ = await _year(scripted(lambda b: _ballot(b, A, accept=_ids_for(b, A))))
    after = advance(state, yr.plan, rolls)
    text = summarize(yr, state, after)
    assert text.startswith("Year 1. ")
    assert "\u2014" not in text
    assert text.count(".") <= 4  # "Year 1." plus two or three short sentences
    assert len(text.split()) <= 32
    assert "Farmer" in text and str(after.population) in text


def test_clean_removes_dashes():
    assert "\u2014" not in clean("We hold \u2014 always\u2014 firm")


def test_parse_json_salvages_truncated_reply():
    from sumer.llm import parse_json

    cut = '{"proposal": {"land_delta": -81, "feed": 1940, "plant": 770},\n "accept": ["x"],\n "spee'
    assert parse_json(cut)["proposal"]["feed"] == 1940
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}


# ---- the bar: gradual, enforced concession ----

def test_bar_falls_round_by_round_to_the_floor():
    from sumer.agent import BAR_FLOOR, BAR_START, bar_schedule

    for rounds in (4, 5, 6):
        bars = bar_schedule(rounds)
        assert bars[0] == BAR_START and bars[-1] == BAR_FLOOR
        assert all(a > b for a, b in itertools.pairwise(bars))


async def test_policy_never_accepts_below_its_bar():
    turns = []

    async def emit(kind, data):
        if kind == "turn" and data["phase"] == "discuss":
            turns.append(data)

    state, _ = new_game(1)
    await deliberate_year(seed=1, state=state, ask=inproc.ask, history=[], max_rounds=5,
                          scheduled_news=None, emit=emit)
    assert turns
    for t in turns:
        scores = {e["id"]: e for e in t["trace"]["evaluations"]}
        for pid in t["accept"]:
            if pid in scores:  # plans tabled before this turn
                assert scores[pid]["my_score"] >= t["trace"]["threshold"]
                assert not scores[pid]["breaks_my_red_line"]
        assert t["trace"]["chosen"]["my_score"] >= t["trace"]["threshold"]


def _llm_case(rnd):
    """A brief with two tabled plans: one the Priest loves, one it hates."""
    from sumer.agent import Mind
    from sumer.protocol import TabledProposal

    state, _ = new_game(1)
    need = state.population * 20
    plans = {"keep": Plan(land_delta=0, feed=need, plant=300),
             "spend": Plan(land_delta=0, feed=need, plant=1000)}
    table = [TabledProposal(id=k, author="farmer", plan=p) for k, p in plans.items()]
    brief = Brief(seed=1, year=1, round=rnd, max_rounds=5, state=state.public(),
                  beliefs=Beliefs().__dict__, speaker="priest", table=table)
    m = Mind("priest", brief)
    return m, brief


def test_llm_accepts_below_the_bar_are_dropped():
    from sumer.agent import _llm_turn
    from sumer.llm import Reply

    m, _ = _llm_case(0)
    evals = m.evaluate(m.value(m.best()))
    low = [e["id"] for e in evals if not e["clears_my_bar"]]
    assert low, "the setup needs a plan below the Priest's opening bar"
    data = {"back": low[0], "proposal": {"land_delta": 0, "feed": 0, "plant": 0},
            "accept": ["keep", "spend"], "speech": "Farmer, fine.", "thought": "ok"}
    b = _llm_turn(m, evals, data, Reply("{}", "m", True, 0, 0))
    assert b.back is None and not set(b.accept) & set(low)
    assert m.acceptable(Decision(b.proposal.land_delta, b.proposal.feed, b.proposal.plant))
    assert "override" in b.trace


async def test_a_round_where_votes_move_is_not_a_stalemate():
    def script(b):
        i = ADVISOR_IDS.index(b.speaker)
        plan = (0, 2000, 1000 - 10 * i)  # seven fixed plans
        acc = _ids_for(b, (0, 2000, 990)) if b.speaker == "farmer" and b.round % 2 else []
        return _ballot(b, plan, accept=acc)

    async def human(options, reason):
        return "safe"

    yr, _ = await _year(scripted(script), ask_human=human)
    assert yr.rounds == 6  # the Farmer's vote moves every round, so the talk runs to the end
    assert yr.outcome == "escalated"


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


async def test_failed_llm_call_is_flagged_not_silent(monkeypatch):
    from sumer import agent, llm

    async def down(messages, temperature=0.4):
        raise llm.LLMFailed("timeout")

    monkeypatch.setattr(agent, "settings", SimpleNamespace(llm_mode="bayleaf", llm_model="m"))
    monkeypatch.setattr(llm, "chat", down)
    state, _ = new_game(1)
    brief = Brief(seed=1, year=1, round=0, max_rounds=5, state=state.public(),
                  beliefs=Beliefs().__dict__, speaker="general")
    b = await decide("general", brief)
    # Recorded as a technical failure, never a silent policy turn, and flagged as unreplayable.
    assert b.source == "llm" and b.error and b.trace["fallback_kind"] == "failed"


async def test_one_second_choice_counts():
    def script(b):
        i = ADVISOR_IDS.index(b.speaker)
        plan = (0, 2000, 1000 - 10 * i)
        return _ballot(b, plan, accept=[t.id for t in b.table])  # tries to accept everything

    _, events = await _year(scripted(script))
    for tally in (d for k, d in events if k == "tally"):
        # each advisor counts for its own plan plus one second choice: 14 endorsements at most
        assert sum(len(r["accept"]) for r in tally["rows"]) <= 2 * len(ADVISOR_IDS)
