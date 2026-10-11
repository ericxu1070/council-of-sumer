import os

os.environ["LLM_MODE"] = "mock"
os.environ["TRANSPORT"] = "inproc"

from sumer.advisors import ADVISOR_IDS, Beliefs, Scorer, safe_plan
from sumer.agent import clean, decide
from sumer.chamber import play_council, play_simple
from sumer.deliberation import deliberate_year
from sumer.game import Decision, advance, legalize, new_game, rating
from sumer.protocol import Ballot, Brief, Plan, sign, verify
from sumer.transport import inproc


def test_same_seed_same_dice():
    assert new_game(3)[1] == new_game(3)[1]
    assert new_game(3)[1] != new_game(4)[1]


def test_legalize_never_overspends():
    state, _ = new_game(1)
    d = legalize(state, Decision(10**6, 10**6, 10**6))
    cost = d.land_delta * state.land_price + d.feed + d.plant // 2
    assert cost <= state.grain
    assert d.plant <= state.population * 10


def test_full_feed_means_no_starvation():
    state, rolls = new_game(1)
    nxt = advance(state, Decision(0, state.population * 20, 500), rolls)
    assert nxt.last_report["starved"] == 0


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


def test_ballot_signature_detects_tampering():
    b = sign(Ballot(advisor="priest", year=1, round=0, proposal=Plan(land_delta=0, feed=1, plant=1)))
    assert verify(b)
    b.accept = ["farmer-y1-r0"]
    assert not verify(b)


async def test_trace_is_private_and_present():
    state, _ = new_game(1)
    brief = Brief(seed=1, year=1, round=0, max_rounds=5, state=state.public(),
                  beliefs=Beliefs().__dict__)
    b = await decide("priest", brief)
    assert b.trace["thought"] and "chosen" in b.trace
    assert b.trace["beliefs"]["pessimistic"]  # the Priest plans for a bad year


async def test_deadlock_asks_the_human():
    asked = {}

    async def human(options, reason):
        asked["reason"] = reason
        return options[0]["id"]

    state, _ = new_game(1)
    # With 2 rounds nobody has time to concede, so the council must escalate.
    yr = await deliberate_year(seed=1, state=state, gather=inproc.gather, history=[],
                               max_rounds=2, scheduled_news=None, ask_human=human)
    assert yr.outcome == "escalated"
    assert asked["reason"]
    assert yr.human_choice is not None


async def test_council_survives_and_beats_averaging():
    council = await play_council(1, inproc.gather)
    average = await play_simple(1, "average")
    assert not council.rating["impeached"]
    assert council.rating["score"] > average.rating["score"]
    assert set(council.satisfaction) == set(ADVISOR_IDS)
    assert rating(new_game(1)[0])["verdict"]


def test_clean_removes_dashes():
    assert "—" not in clean("We hold — always— firm")


def test_parse_json_salvages_truncated_reply():
    from sumer.llm import parse_json

    cut = '{"proposal": {"land_delta": -81, "feed": 1940, "plant": 770},\n "accept": ["x"],\n "spee'
    assert parse_json(cut)["proposal"]["feed"] == 1940
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
