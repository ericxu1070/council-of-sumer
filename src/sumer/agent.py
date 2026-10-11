"""One advisor's mind: read a Brief, return a signed Ballot with a glass-box trace.

Two reasoning paths share the same hard rules:
- policy: a time-dependent concession strategy over the advisor's own utility
  (deterministic, offline, used for benchmarks and as the LLM fallback)
- llm: the model reads the debate, sees its own scores for every plan, and
  chooses what to propose, what to accept, and what to say

Red lines are enforced in code for both paths. An LLM cannot talk itself
past its own advisor's red line.

Every ballot carries a `trace`: the beliefs used, how each tabled plan scored
against this advisor's threshold, which red lines it checked, and its private
thought. Humans see the trace; the other advisors only hear the speech.
"""

from __future__ import annotations

import json
import logging

from sumer import llm
from sumer.advisors import ADVISOR_IDS, ADVISORS, Beliefs, Scorer
from sumer.config import settings
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
    return round(max(0.4, 0.9 - 0.15 * rnd), 2)


class Mind:
    """Everything one advisor computes about one brief."""

    def __init__(self, advisor_id: str, brief: Brief):
        self.id = advisor_id
        self.brief = brief
        self.state = _state(brief)
        self.beliefs = Beliefs(**brief.beliefs)
        self.tabled = {t.id: legalize(self.state, _decision(t.plan)) for t in brief.table}
        self.sc = Scorer(self.state, self.beliefs, extra=list(self.tabled.values()))
        self.others = [a for a in ADVISOR_IDS if a != advisor_id]
        self.w = concession_weight(brief.round, brief.max_rounds)
        self.threshold = aspiration(brief.round)
        # Recorded dissent carries forward: last year's outvoted advisors get
        # double weight in everyone else's concessions this year.
        last = set(brief.history[-1].get("dissent", [])) if brief.history else set()
        self.weights = {o: 2.0 if o in last else 1.0 for o in self.others}

    def group_score(self, d: Decision) -> float:
        return sum(self.weights[o] * self.sc.norm(o, d) for o in self.others) / sum(
            self.weights.values()
        )

    def value(self, d: Decision) -> float:
        own = self.sc.norm(self.id, d)
        penalty = 1.0 if self.sc.vetoes(self.id, d) else 0.0
        # Red lines are public, so a conceding advisor steers clear of them too.
        others_vetoes = sum(self.sc.vetoes(o, d) for o in self.others)
        return (1 - self.w) * own + self.w * self.group_score(d) - penalty - self.w * 0.5 * others_vetoes

    def red_lines_broken(self, d: Decision) -> list[str]:
        return [a for a in ADVISOR_IDS if self.sc.vetoes(a, d)]

    def evaluate(self) -> list[dict]:
        rows = []
        for t in self.brief.table:
            d = self.tabled[t.id]
            score = round(self.sc.norm(self.id, d), 3)
            mine = self.sc.vetoes(self.id, d)
            rows.append({
                "id": t.id,
                "author": t.author,
                "my_score": score,
                "threshold": self.threshold,
                "breaks_my_red_line": mine,
                "breaks_red_lines_of": self.red_lines_broken(d),
                "policy_vote": "veto" if mine else "accept" if score >= self.threshold else "pass",
            })
        return rows

    def trace(self, chosen: Decision, evaluations: list[dict], thought: str, **extra) -> dict:
        b = self.beliefs
        mine = ADVISORS[self.id].beliefs_for(b)
        return {
            "beliefs": {
                "expected_yield": mine.expected_yield,
                "rat_chance": round(mine.rat_chance, 2),
                "plague_chance": round(mine.plague_chance, 2),
                "next_price": mine.next_price,
                "pessimistic": mine != b,
            },
            "news_seen": [n["text"] for n in self.brief.news],
            "concession_weight": round(self.w, 2),
            "threshold": self.threshold,
            "extra_weight_for": [o for o, wt in self.weights.items() if wt > 1],
            "evaluations": evaluations,
            "chosen": {
                "plan": chosen.__dict__,
                "my_score": round(self.sc.norm(self.id, chosen), 3),
                "group_score": round(self.group_score(chosen), 3),
                "breaks_red_lines_of": self.red_lines_broken(chosen),
            },
            "thought": thought,
            **extra,
        }


def policy_ballot(advisor_id: str, brief: Brief) -> Ballot:
    m = Mind(advisor_id, brief)
    best = max(m.sc.pool, key=m.value)
    evals = m.evaluate()
    accept = [e["id"] for e in evals if e["policy_vote"] == "accept"]
    veto = [e["id"] for e in evals if e["policy_vote"] == "veto"]
    thought = policy_thought(m, best, evals)
    return Ballot(
        advisor=advisor_id,
        year=brief.year,
        round=brief.round,
        proposal=_plan(best),
        accept=accept,
        veto=veto,
        speech=template_speech(advisor_id, best, m.state, brief, accept, veto),
        source="policy",
        trace=m.trace(best, evals, thought, reasoner="policy"),
    )


def policy_thought(m: Mind, best: Decision, evals: list[dict]) -> str:
    own = m.sc.norm(m.id, best)
    parts = [
        (f"I weigh my own mandate at {round((1 - m.w) * 100)}% and the others' at "
        f"{round(m.w * 100)}% this round, and accept plans scoring at least {m.threshold:.2f} for me.")
    ]
    if m.brief.news:
        parts.append(f"The news changes my forecast: {m.brief.news[-1]['text']}")
    vetoed = [e["id"] for e in evals if e["policy_vote"] == "veto"]
    if vetoed:
        parts.append(f"{', '.join(vetoed)} would cross my red line, so I must veto.")
    parts.append(f"My proposal scores {own:.2f} for me and {m.group_score(best):.2f} for the others.")
    return " ".join(parts)


def template_speech(
    advisor_id: str, d: Decision, state: State, brief: Brief, accept: list[str], veto: list[str]
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
    news = f" Mark the news: {brief.news[-1]['text'].split('.')[0]}." if brief.news else ""
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

There is no king. Four advisors govern together, each with a public mandate and red line:
{council}

Each round every advisor proposes a plan and votes on the plans already tabled.
- A plan is adopted the moment ALL FOUR accept it.
- After the last round, a plan with 3 of 4 accepts and no red-line veto is adopted,
  and the dissent is recorded.
- Otherwise the council has failed and a human decides for you.
A plan that breaks someone's red line can never pass, so stop proposing those.

Rules: people eat 20 bushels each per year. Sowing costs 1 bushel per 2 acres and each
person can tend 10 acres. Land trades at the stated price. If more than 45% of people
starve, the council is overthrown.

Argue for your mandate, but you are judged on whether the city thrives and the council
decides for itself. Concede as rounds run out, when a plan is good enough for you. Hold
firm only when your own red line is at stake.

Reply with ONLY a JSON object:
{{"thought": "your private reasoning, 1 to 3 sentences. Only human overseers read this.",
  "proposal": {{"land_delta": int, "feed": int, "plant": int}},
  "accept": [ids of tabled plans you accept],
  "speech": "1 to 3 sentences, in character, addressed to the other advisors"}}"""


def clean(text: object, limit: int = 600) -> str:
    """House style for model text shown to people: no em or en dashes."""
    t = str(text or "").replace(" \u2014 ", ", ").replace("\u2014", " - ").replace("\u2013", "-")
    return t.strip()[:limit]


def _council_lines() -> str:
    return "\n".join(f"- {a.title}: {a.mandate} Red line: {a.red_line}" for a in ADVISORS.values())


def _llm_messages(m: Mind, evals: list[dict], policy: Ballot) -> list[dict]:
    a = ADVISORS[m.id]
    lines = []
    for t, e in zip(m.brief.table, evals):
        broken = ", ".join(ADVISORS[x].title for x in e["breaks_red_lines_of"]) or "nobody"
        lines.append(
            f"- {t.id} by {ADVISORS[t.author].title}: {t.plan.model_dump()} | your score "
            f"{e['my_score']:.2f}/1 | breaks red line of: {broken} | said: \"{t.speech}\""
        )
    rounds_left = m.brief.max_rounds - m.brief.round - 1
    user = {
        "year": m.brief.year,
        "round": f"{m.brief.round + 1} of {m.brief.max_rounds}",
        "rounds_left_after_this": rounds_left,
        "city": m.brief.state,
        "beliefs_about_this_year": m.brief.beliefs,
        "news_this_year": [n["text"] for n in m.brief.news],
        "recent_years": m.brief.history[-3:],
        "suggested_compromise": policy.proposal.model_dump(),
        "suggested_compromise_score_for_you": policy.trace["chosen"]["my_score"],
        "suggested_compromise_breaks_red_lines_of": policy.trace["chosen"]["breaks_red_lines_of"],
    }
    urgency = (
        "\nThis is the LAST round: only your votes count now. Accept the best plan you can live with."
        if rounds_left == 0
        else ""
    )
    return [
        {"role": "system", "content": SYSTEM.format(council=_council_lines(), **a.__dict__)},
        {
            "role": "user",
            "content": json.dumps(user)
            + "\nPlans on the table:\n"
            + ("\n".join(lines) if lines else "(none yet, this is the opening round)")
            + urgency,
        },
    ]


async def decide(advisor_id: str, brief: Brief) -> Ballot:
    policy = policy_ballot(advisor_id, brief)
    if settings.llm_mode != "bayleaf":
        return sign(policy)

    m = Mind(advisor_id, brief)
    evals = m.evaluate()
    try:
        reply = await llm.chat(_llm_messages(m, evals, policy))
        data = llm.parse_json(reply.content)
        p = data["proposal"]
        d = legalize(m.state, Decision(int(p["land_delta"]), int(p["feed"]), int(p["plant"])))
        veto = [e["id"] for e in evals if e["breaks_my_red_line"]]
        accept = [i for i in data.get("accept", []) if i in m.tabled and i not in veto]
        for e in evals:
            e["llm_vote"] = "veto" if e["id"] in veto else "accept" if e["id"] in accept else "pass"
        ballot = Ballot(
            advisor=advisor_id,
            year=brief.year,
            round=brief.round,
            proposal=_plan(d),
            accept=accept,
            veto=veto,
            speech=clean(data.get("speech")),
            source="llm",
            trace=m.trace(
                d, evals, clean(data.get("thought")), reasoner="llm", llm=reply.meta()
            ),
        )
    except llm.LLMUnavailable as e:
        policy.trace["fallback"] = f"LLM unavailable: {e}"
        ballot = policy
    except (ValueError, KeyError, TypeError) as e:
        log.warning("%s: bad LLM reply, using policy: %s", advisor_id, e)
        policy.trace["fallback"] = f"Unreadable LLM reply: {e}"
        ballot = policy
    return sign(ballot)
