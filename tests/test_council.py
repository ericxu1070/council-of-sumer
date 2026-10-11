import os

os.environ["LLM_MODE"] = "mock"
os.environ["TRANSPORT"] = "inproc"

from sumer.advisors import ADVISOR_IDS, Beliefs, Scorer, mentions, plain, safe_plan
from sumer.agent import clean, decide
from sumer.chamber import play_council, play_simple
from sumer.deliberation import deliberate_year, speaking_order
from sumer.game import YEARS, Decision, advance, new_game, rating
from sumer.narrator import summarize
from sumer.protocol import Ballot, Brief, Plan, sign, verify
from sumer.transport import inproc

A, B, C = (0, 2000, 1000), (0, 2000, 900), (0, 2000, 800)


def _ballot(brief, plan=A, back=None, accept=(), veto=(), vote=None, speech="Farmer, I agree."):
    return Ballot(
        advisor=brief.speaker, year=brief.year, round=brief.round, phase=brief.phase,
        proposal=Plan(land_delta=plan[0], feed=plan[1], plant=plan[2]), back=back,
        accept=list(accept), veto=list(veto), runoff_vote=vote, speech=speech,
    )


def scripted(fn):
    """An ask() whose advisors follow a script; ballots are signed like real ones."""
    briefs = []

    async def ask(brief):
        briefs.append(brief)
        b = fn(brief)
        return sign(b) if b else None

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

def test_ballot_signature_detects_tampering():
    b = sign(Ballot(advisor="priest", year=1, round=0, proposal=Plan(land_delta=0, feed=1, plant=1)))
    assert verify(b)
    b.accept = ["farmer-y1-r0"]
    assert not verify(b)


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


async def test_strong_majority_after_round_two_records_dissent():
    def script(b):
        if b.speaker == "priest":
            return _ballot(b, B)
        if b.speaker == "merchant":
            return _ballot(b, C)
        return _ballot(b, A, accept=_ids_for(b, A))

    yr, _ = await _year(scripted(script))
    assert yr.outcome == "majority"
    assert yr.rounds == 2  # adopted the moment round 2 completes
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


async def test_deadlock_escalates_with_safe_fallback():
    def script(b):
        i = ADVISOR_IDS.index(b.speaker)
        return _ballot(b, (0, 2000, 1000 - 10 * i))  # seven plans, nobody accepts another

    yr, _ = await _year(scripted(script))  # no human: falls back to the safe plan
    assert yr.outcome == "escalated"
    assert yr.human_choice is None
    assert "Deadlock" in yr.reason


async def test_forged_ballot_is_dropped():
    def script(b):
        return _ballot(b, A, accept=_ids_for(b, A))

    inner = scripted(script)

    async def ask(brief):
        b = await inner(brief)
        if brief.speaker == "merchant":
            b.accept = []  # altered after signing
        return b

    yr, events = await _year(ask)
    rejected = [d for k, d in events if k == "wire" and d.get("rejected")]
    assert rejected and all(d["advisor"] == "merchant" for d in rejected)
    assert all(d["advisor"] != "merchant" for k, d in events if k == "turn")
    assert yr.outcome == "majority"  # 6 of 7 is a strong majority; the forger is outvoted
    assert yr.dissent == ["merchant"]


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
    assert text.count(".") <= 3  # "Year 1." plus at most two sentences
    assert "Farmer" in text and str(after.population) in text


def test_clean_removes_dashes():
    assert "\u2014" not in clean("We hold \u2014 always\u2014 firm")


def test_parse_json_salvages_truncated_reply():
    from sumer.llm import parse_json

    cut = '{"proposal": {"land_delta": -81, "feed": 1940, "plant": 770},\n "accept": ["x"],\n "spee'
    assert parse_json(cut)["proposal"]["feed"] == 1940
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
