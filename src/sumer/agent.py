"""One advisor's mind: read a Brief, return a signed Ballot.

Two reasoning paths share the same hard rules:
- policy: a time-dependent concession strategy over the advisor's own utility
  (deterministic, offline, used for benchmarks and as the LLM fallback)
- llm: the model reads the debate, sees its own scores for every plan, and
  chooses what to propose, what to accept, and what to say

Red lines are enforced in code for both paths. An LLM cannot talk itself
past its own advisor's red line.
"""

from __future__ import annotations

import json
import logging

from sumer import llm
from sumer.config import settings
from sumer.advisors import ADVISOR_IDS, ADVISORS, Beliefs, Scorer
from sumer.game import Decision, State, legalize
from sumer.protocol import Ballot, Brief, Plan, sign

log = logging.getLogger(__name__)


def _state(brief: Brief) -> State:
    s = dict(brief.state)
    s.pop("acres_per_person", None)
    return State(**s)


def _plan(d: Decision) -> Plan:
    return Plan(land_delta=d.land_delta, feed=d.feed, plant=d.plant)


def _decision(p: Plan) -> Decision:
    return Decision(p.land_delta, p.feed, p.plant)


def concession_weight(rnd: int, max_rounds: int) -> float:
    """How much an advisor weighs the others' mandates. Starts selfish."""
    return 0.0 if max_rounds <= 1 else 0.75 * rnd / (max_rounds - 1)


def aspiration(rnd: int) -> float:
    """The minimum own-score an advisor will accept. Falls each round."""
    return max(0.4, 0.9 - 0.15 * rnd)


def policy_ballot(advisor_id: str, brief: Brief) -> Ballot:
    state = _state(brief)
    beliefs = Beliefs(**brief.beliefs)
    tabled = [legalize(state, _decision(t.plan)) for t in brief.table]
    sc = Scorer(state, beliefs, extra=tabled)
    others = [a for a in ADVISOR_IDS if a != advisor_id]
    w = concession_weight(brief.round, brief.max_rounds)

    # Recorded dissent carries forward: last year's outvoted advisors get
    # double weight in everyone else's concessions this year.
    last_dissent = set(brief.history[-1].get("dissent", [])) if brief.history else set()
    weights = {o: 2.0 if o in last_dissent else 1.0 for o in others}

    def value(d: Decision) -> float:
        own = sc.norm(advisor_id, d)
        group = sum(weights[o] * sc.norm(o, d) for o in others) / sum(weights.values())
        penalty = 1.0 if sc.vetoes(advisor_id, d) else 0.0
        # Red lines are public, so a conceding advisor steers clear of them too.
        others_vetoes = sum(sc.vetoes(o, d) for o in others)
        return (1 - w) * own + w * group - penalty - w * 0.5 * others_vetoes

    best = max(sc.pool, key=value)
    accept, veto = [], []
    for t, d in zip(brief.table, tabled):
        if sc.vetoes(advisor_id, d):
            veto.append(t.id)
        elif sc.norm(advisor_id, d) >= aspiration(brief.round):
            accept.append(t.id)

    return Ballot(
        advisor=advisor_id,
        year=brief.year,
        round=brief.round,
        proposal=_plan(best),
        accept=accept,
        veto=veto,
        speech=template_speech(advisor_id, best, state, beliefs, brief, accept, veto),
        source="policy",
    )


def template_speech(
    advisor_id: str,
    d: Decision,
    state: State,
    beliefs: Beliefs,
    brief: Brief,
    accept: list[str],
    veto: list[str],
) -> str:
    trade = (
        f"buy {d.land_delta} acres"
        if d.land_delta > 0
        else f"sell {-d.land_delta} acres"
        if d.land_delta < 0
        else "hold our land"
    )
    plan = f"{trade}, release {d.feed} bushels as food, and sow {d.plant} acres"
    lead = {
        "farmer": "Every family must eat.",
        "priest": "The gods favor a full granary.",
        "general": "Land is strength.",
        "merchant": f"Land trades at {state.land_price} today.",
    }[advisor_id]
    news = f" Given the news, {brief.news[-1]['text'].split('.')[0].lower()}." if brief.news else ""
    stance = ""
    if veto:
        stance = f" I will not accept {', '.join(veto)}. It crosses my red line."
    elif accept:
        stance = f" I can live with {', '.join(accept)}."
    return f"{lead}{news} I propose we {plan}.{stance}"


SYSTEM = """You are {title} on the council of ancient Sumer (the 1968 game Hamurabi).
Your mandate: {mandate}
Your red line: {red_line}
Your voice: {voice}.

There is no king. Four advisors (Voice of the Farmers, High Priest, General, Chief
Merchant) govern together. Each round every advisor proposes a plan and votes on the
plans already on the table. A plan is adopted when ALL advisors accept it. At the
final round, a plan accepted by 3 of 4 is adopted with the dissent recorded, unless
someone vetoes it on a red line. Otherwise the decision goes to a human.

Rules: people eat 20 bushels each per year. Sowing costs 1 bushel per 2 acres and
each person can tend 10 acres. Land trades at the stated price. If more than 45% of
people starve, the council is overthrown.

Argue for your mandate, but you are judged on whether the city thrives. Concede when
the numbers say a compromise is good enough; hold firm when your red line is at stake.
Reply with ONLY a JSON object:
{{"proposal": {{"land_delta": int, "feed": int, "plant": int}},
  "accept": [ids of tabled plans you accept],
  "speech": "1 to 3 sentences, in character, addressed to the other advisors"}}"""


def _llm_messages(advisor_id: str, brief: Brief, sc: Scorer, policy: Ballot) -> list[dict]:
    a = ADVISORS[advisor_id]
    state = _state(brief)
    lines = []
    for t in brief.table:
        d = legalize(state, _decision(t.plan))
        lines.append(
            f"- {t.id} by {t.author}: {t.plan.model_dump()} | your score "
            f"{sc.norm(advisor_id, d):.2f}/1 | breaks your red line: {sc.vetoes(advisor_id, d)}"
            f' | said: "{t.speech}"'
        )
    user = {
        "year": brief.year,
        "round": f"{brief.round + 1} of {brief.max_rounds}",
        "city": brief.state,
        "beliefs_about_this_year": brief.beliefs,
        "news_this_year": [n["text"] for n in brief.news],
        "recent_years": brief.history[-3:],
        "hint_your_compromise_plan": policy.proposal.model_dump(),
        "hint_its_score_for_you": round(sc.norm(advisor_id, _decision(policy.proposal)), 2),
    }
    return [
        {"role": "system", "content": SYSTEM.format(**a.__dict__)},
        {
            "role": "user",
            "content": json.dumps(user)
            + "\nPlans on the table:\n"
            + ("\n".join(lines) if lines else "(none yet, this is the opening round)"),
        },
    ]


async def decide(advisor_id: str, brief: Brief) -> Ballot:
    policy = policy_ballot(advisor_id, brief)
    if settings.llm_mode != "bayleaf":
        return sign(policy)

    state = _state(brief)
    beliefs = Beliefs(**brief.beliefs)
    tabled = {t.id: legalize(state, _decision(t.plan)) for t in brief.table}
    sc = Scorer(state, beliefs, extra=list(tabled.values()))
    try:
        reply = llm.parse_json(await llm.chat(_llm_messages(advisor_id, brief, sc, policy)))
        p = reply["proposal"]
        d = legalize(state, Decision(int(p["land_delta"]), int(p["feed"]), int(p["plant"])))
        veto = [i for i, td in tabled.items() if sc.vetoes(advisor_id, td)]
        accept = [i for i in reply.get("accept", []) if i in tabled and i not in veto]
        ballot = Ballot(
            advisor=advisor_id,
            year=brief.year,
            round=brief.round,
            proposal=_plan(d),
            accept=accept,
            veto=veto,
            speech=str(reply.get("speech", ""))[:600],
            source="llm",
        )
    except llm.LLMUnavailable:
        ballot = policy
    except (ValueError, KeyError, TypeError) as e:
        log.warning("%s: bad LLM reply, using policy: %s", advisor_id, e)
        ballot = policy
    return sign(ballot)
