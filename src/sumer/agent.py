"""One advisor's mind: read a Brief, return a signed Ballot with a glass-box trace.

The council talks in turns, so a Brief is addressed to one speaker. It shows
the table with each plan's current support, what the earlier speakers said
this round, and earlier rounds. The speaker answers to the others by name.

Two reasoning paths share the same hard rules:
- policy: a time-dependent concession strategy over the advisor's own utility
  (deterministic, offline, explicitly selected for mock mode and benchmarks)
- llm: the model reads the conversation, sees its own scores for every plan,
  and chooses what to propose, what to accept, and what to say

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
from sumer.advisors import (
    ADVISOR_IDS,
    ADVISORS,
    N_ADVISORS,
    Beliefs,
    Scorer,
    mentions,
    plain_words,
    short,
)
from sumer.config import settings
from sumer.game import Decision, State, legalize
from sumer.protocol import Ballot, Brief, Plan, TabledProposal, bind, sign

log = logging.getLogger(__name__)

SPEECH_WORDS = 28  # a hard cap a little above the 25 words we ask for


def _state(brief: Brief) -> State:
    s = dict(brief.state)
    for k in ("acres_per_person", "starved"):
        s.pop(k, None)
    return State(**s)


def _plan(d: Decision) -> Plan:
    return Plan(land_delta=d.land_delta, feed=d.feed, plant=d.plant)


def _decision(p: Plan) -> Decision:
    return Decision(p.land_delta, p.feed, p.plant)


def progress(brief: Brief) -> float:
    """How far the debate has run, in rounds (a turn is a seventh of a round)."""
    return brief.round + brief.turn / N_ADVISORS


# Policy tuning. Chosen so mandates really clash (vetoes, dissent, the odd
# runoff) while the council still decides most years on its own.
MAX_CONCESSION = 0.6  # how far an advisor ends up weighing the others' mandates
ASPIRATION_START, ASPIRATION_DROP, ASPIRATION_FLOOR = 0.9, 0.15, 0.4
TOLERANCE_START, TOLERANCE_GROWTH = 0.0, 0.02
VALUE_ACCEPT_FROM = 2.0  # rounds in, an advisor also accepts plans near its concession optimum
MAX_ACCEPTS = 2  # an advisor endorses at most this many plans per turn, its favorites first


def tolerance(p: float) -> float:
    """How close to its own best a plan must be for this advisor to back it."""
    return round(TOLERANCE_START + TOLERANCE_GROWTH * p, 3)


def concession_weight(p: float, max_rounds: int) -> float:
    """How much an advisor weighs the others' mandates. Starts selfish and
    reaches its full weight two rounds before the end, leaving time to agree."""
    if max_rounds <= 1:
        return 0.0
    return round(MAX_CONCESSION * min(1.0, p / max(1, max_rounds - 2)), 3)


def aspiration(p: float) -> float:
    """The minimum own-score an advisor will accept. Falls as the debate runs."""
    return round(max(ASPIRATION_FLOOR, ASPIRATION_START - ASPIRATION_DROP * p), 3)


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
        p = progress(brief) if brief.phase == "discuss" else float(brief.max_rounds)
        self.p = p
        self.w = concession_weight(p, brief.max_rounds)
        self.threshold = aspiration(p)
        # Recorded dissent carries forward: last year's outvoted advisors get
        # double weight in everyone else's concessions this year.
        last = set(brief.history[-1].get("dissent", [])) if brief.history else set()
        self.weights = {o: 2.0 if o in last else 1.0 for o in self.others}
        # Near-best plans this advisor will back or accept, widening as time runs out.
        self.tolerance = tolerance(p)

    def group_score(self, d: Decision) -> float:
        return sum(self.weights[o] * self.sc.norm(o, d) for o in self.others) / sum(
            self.weights.values()
        )

    def value(self, d: Decision) -> float:
        own = self.sc.norm(self.id, d)
        penalty = 1.0 if self.sc.vetoes(self.id, d) else 0.0
        # Red lines are public, so a conceding advisor steers clear of them too.
        others_vetoes = sum(self.sc.vetoes(o, d) for o in self.others)
        return (
            (1 - self.w) * own + self.w * self.group_score(d) - penalty
            - self.w * 0.5 * others_vetoes
        )

    def red_lines_broken(self, d: Decision) -> list[str]:
        return [a for a in ADVISOR_IDS if self.sc.vetoes(a, d)]

    def best(self) -> Decision:
        return max(self.sc.pool, key=self.value)

    def evaluate(self, best_value: float) -> list[dict]:
        rows = []
        for t in self.brief.table:
            d = self.tabled[t.id]
            score = round(self.sc.norm(self.id, d), 3)
            value = round(self.value(d), 3)
            mine = self.sc.vetoes(self.id, d)
            good = score >= self.threshold or (
                self.p >= VALUE_ACCEPT_FROM and value >= best_value - self.tolerance
            )
            rows.append({
                "id": t.id,
                "author": t.author,
                "support": len(t.accept),
                "my_score": score,
                "my_value": value,
                "threshold": self.threshold,
                "breaks_my_red_line": mine,
                "breaks_red_lines_of": self.red_lines_broken(d),
                "policy_vote": "veto" if mine else "accept" if good else "pass",
            })
        # Endorse, do not rubber-stamp: keep only the favorite few.
        liked = sorted((r for r in rows if r["policy_vote"] == "accept"),
                       key=lambda r: -r["my_value"])
        for r in liked[MAX_ACCEPTS:]:
            r["policy_vote"] = "pass"
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


# ---------------------------------------------------------------- policy mind


def policy_ballot(advisor_id: str, brief: Brief) -> Ballot:
    if brief.phase == "runoff":
        return policy_runoff(advisor_id, brief)
    m = Mind(advisor_id, brief)
    best = m.best()
    bv = m.value(best)
    evals = m.evaluate(bv)
    accept = [e["id"] for e in evals if e["policy_vote"] == "accept"]
    veto = [e["id"] for e in evals if e["policy_vote"] == "veto"]
    # Back a plan already on the table when it is nearly as good as my own
    # best, preferring the one with the most support. That is how a council
    # converges instead of tabling seven near-identical plans.
    near = [
        (t, e) for t, e in zip(brief.table, evals)
        if e["policy_vote"] != "veto" and e["my_value"] >= bv - m.tolerance
    ]
    back: TabledProposal | None = None
    chosen = best
    if near:
        back, _ = max(near, key=lambda te: (te[1]["support"], te[1]["my_value"]))
        chosen = m.tabled[back.id]
        if back.id not in accept:
            accept.append(back.id)
    thought = policy_thought(m, chosen, evals, back)
    speech = template_speech(m, chosen, back, accept, veto)
    return Ballot(
        advisor=advisor_id,
        year=brief.year,
        round=brief.round,
        phase="discuss",
        proposal=_plan(chosen),
        back=back.id if back else None,
        accept=accept,
        veto=veto,
        speech=speech,
        addressed=mentions(speech, exclude=advisor_id),
        source="policy",
        trace=m.trace(chosen, evals, thought, reasoner="policy"),
    )


def runoff_choice(m: Mind) -> tuple[str, list[str], bool]:
    """(plan id I vote for, finalists that break my red line, forced by a red line)."""
    a, b = m.brief.runoff
    da, db = m.tabled[a.id], m.tabled[b.id]
    va, vb = m.sc.vetoes(m.id, da), m.sc.vetoes(m.id, db)
    veto = [t.id for t, v in ((a, va), (b, vb)) if v]
    if va != vb:
        return (b.id if va else a.id), veto, True
    return (a.id if m.value(da) >= m.value(db) else b.id), veto, False


def policy_runoff(advisor_id: str, brief: Brief) -> Ballot:
    m = Mind(advisor_id, brief)
    vote, veto, forced = runoff_choice(m)
    t = next(t for t in brief.runoff if t.id == vote)
    other = next(t for t in brief.runoff if t.id != vote)
    d = m.tabled[vote]
    evals = m.evaluate(m.value(m.best()))
    if t.author == advisor_id:
        speech = f"{short(other.author)}, I stand by my own plan. {LEAD[advisor_id]}"
    elif forced:
        speech = (f"{short(t.author)}, you have my vote. "
                  f"The {short(other.author)}'s plan {RED_SHORT[advisor_id]}.")
    else:
        speech = f"{short(t.author)}, I vote for your plan. It is the better bargain for all of us."
    thought = (
        f"Runoff: {vote} scores {m.value(d):.2f} for me with the others weighed in"
        + (", and the other plan crosses my red line." if forced else ".")
    )
    return Ballot(
        advisor=advisor_id,
        year=brief.year,
        round=brief.round,
        phase="runoff",
        proposal=_plan(d),
        veto=veto,
        runoff_vote=vote,
        speech=_trim(speech),
        addressed=mentions(speech, exclude=advisor_id),
        source="policy",
        trace=m.trace(d, evals, thought, reasoner="policy", forced_by_red_line=forced),
    )


def policy_thought(m: Mind, chosen: Decision, evals: list[dict], back) -> str:
    parts = []
    if m.brief.news:
        parts.append(f"News: {m.brief.news[-1]['text'].split('.')[0]}.")
    vetoed = [short(e["author"]) for e in evals if e["policy_vote"] == "veto"]
    if vetoed:
        parts.append(f"Must veto plans from {', '.join(vetoed)}: they cross my red line.")
    if back and back.author != m.id:
        parts.append(f"{short(back.author)}'s plan is close enough to my best, so I back it.")
    parts.append(
        f"It scores {m.sc.norm(m.id, chosen):.2f} for me, {m.group_score(chosen):.2f} for the others."
    )
    return " ".join(parts)


LEAD = {
    "farmer": "Every family must eat.",
    "priest": "The gods favor a full granary.",
    "general": "Land is strength.",
    "merchant": "Everything has a price.",
    "builder": "Water is wasted on idle fields.",
    "healer": "A fed city is a healthy city.",
    "diplomat": "Our neighbors are watching.",
}
RED_SHORT = {
    "farmer": "starves our families",
    "priest": "empties the granary before a bad year",
    "general": "sells off our land",
    "merchant": "costs us dearly",
    "builder": "leaves half our fields fallow",
    "healer": "starves the sick and the old",
    "diplomat": "grabs land and invites war with Lagash",
}
COMPLAINT = {
    "farmer": "leaves families hungry",
    "priest": "trusts the harvest too much",
    "general": "gives up ground we need",
    "merchant": "wastes good grain",
    "builder": "leaves good fields dry",
    "healer": "skimps on food for the sick and the stores",
    "diplomat": "will anger our neighbors",
}


def _trim(text: str, words: int = SPEECH_WORDS) -> str:
    w = text.split()
    return text if len(w) <= words else " ".join(w[:words]).rstrip(",;:") + "."


def _fit(sentences: list[str], words: int = 25) -> str:
    """Join whole sentences while they fit in the word budget (the first always goes in)."""
    out: list[str] = []
    for s in sentences:
        if out and len(" ".join([*out, s]).split()) > words:
            continue
        out.append(s)
    return _trim(" ".join(out))


def template_speech(
    m: Mind, chosen: Decision, back: TabledProposal | None, accept: list[str], veto: list[str]
) -> str:
    """A short reply that names another advisor and reacts to what it just said."""
    me = m.id
    authors = {t.id: t.author for t in m.brief.table}
    target, pid = None, None
    for t in reversed(m.brief.turns_this_round):
        if t["advisor"] != me:
            target, pid = t["advisor"], t["plan_id"]
            break
    if target is None and m.brief.table:
        lead = max(m.brief.table, key=lambda t: len(t.accept))
        if lead.author != me:
            target, pid = lead.author, lead.id
    parts = []
    if target:
        name = short(target)
        owner = authors.get(pid, target)
        whose = "your plan" if owner == target else f"the {short(owner)}'s plan"
        if owner == me:
            parts.append(f"{name}, thank you for standing with me.")
        elif pid in veto:
            parts.append(f"{name}, {whose} {RED_SHORT[me]}. I cannot allow it.")
        elif back and pid == back.id:
            parts.append(f"{name}, I agree. I back {whose} too." if owner != target
                         else f"{name}, I back your plan.")
        elif pid in accept:
            parts.append(f"{name}, I can live with {whose}.")
        else:
            parts.append(f"{name}, {whose} {COMPLAINT[me]}.")
    if back and back.author != me:
        if pid != back.id:
            parts.append(f"I back the {short(back.author)}'s plan instead.")
    else:
        parts.append(f"I say we {plain_words(chosen, m.state)}.")
    parts.append(LEAD[me])
    return _fit(parts)


# ---------------------------------------------------------------- LLM mind

SYSTEM = """You are the {title} ("{short}") on the council of ancient Sumer (the 1968 game Hamurabi).
Your mandate: {mandate}
Your red line: {red_line}
Your voice: {voice}.

There is no king. Seven advisors govern together, each with a public mandate and red line:
{council}

The council talks like a real meeting: advisors speak one at a time, in turn, and each hears
everything said before it. On your turn you table a proposal (or back a plan already on the
table), accept the tabled plans you can live with, and say a few words to the others.
- A plan passes the moment all seven accept it.
- From the end of round 2, a plan with 5 of 7 accepts and no red-line veto passes.
- When talk runs out, a plan with 4 of 7 accepts and no veto passes. A close split goes to a
  runoff vote between the top two plans. Otherwise a human decides for the council.
A plan that breaks someone's red line can never pass, so do not propose those.

Rules: people eat 20 bushels each per year. Sowing costs 1 bushel per 2 acres and each
person can tend 10 acres. Land trades at the stated price. If more than 45% of people
starve, the council is overthrown.

Argue for your mandate, but you are judged on whether the city thrives and the council
decides for itself. Concede as rounds run out, when a plan is good enough for you. Hold
firm only when your own red line is at stake. Backing someone else's plan is a strong move.

How to speak: answer what was just said. Address one or two advisors by name (for example
"Builder, ..."), and agree or push back with a reason from your mandate. At most 25 words,
plain everyday words, no numbers, no lists, no dashes.

Your scores for each plan are already computed for you. Do not do arithmetic in your reply.
Reply with ONLY a JSON object, fields in this order:
{{"back": "id of a tabled plan you back as your own proposal, or null",
  "proposal": {{"land_delta": int, "feed": int, "plant": int}},
  "accept": [ids of tabled plans you accept],
  "speech": "at most 25 words to the other advisors, naming at least one of them",
  "thought": "your private reasoning in at most two sentences. Only human overseers read this."}}"""

RUNOFF_ASK = """The council could not agree, so the two leading plans go to a runoff.
You must vote for exactly one of them. If a plan breaks your red line you must vote for the other.
Reply with ONLY a JSON object:
{"vote": "the id of the plan you vote for",
 "speech": "one line, at most 15 words, naming an advisor, no numbers",
 "thought": "your private reasoning in one sentence"}"""


def clean(text: object, limit: int = 600) -> str:
    """House style for model text shown to people: no em or en dashes."""
    t = str(text or "").replace(" \u2014 ", ", ").replace("\u2014", " - ").replace("\u2013", "-")
    return t.strip()[:limit]


def _council_lines() -> str:
    return "\n".join(
        f'- {a.title} ("{a.short}"): {a.mandate} Red line: {a.red_line}'
        for a in ADVISORS.values()
    )


def _turn_line(t: dict) -> str:
    who = short(t["advisor"])
    if t.get("runoff_vote"):
        return f'  {who} voted for {t["runoff_vote"]}: "{t["speech"]}"'
    act = "backed" if t.get("backed") else "proposed"
    acc = ", ".join(t.get("accept", [])) or "nothing"
    veto = f"; vetoed {', '.join(t['veto'])}" if t.get("veto") else ""
    return (f'  {who}: "{t["speech"]}" ({act} {t["plan_id"]}: {t["plain"]}; '
            f"accepted {acc}{veto})")


def _conversation(m: Mind) -> str:
    b = m.brief
    lines = []
    if b.earlier_turns:
        last = b.earlier_turns[-1]["round"]
        prev = [t for t in b.earlier_turns if t["round"] == last]
        lines.append(f"Last round (round {last + 1}):")
        lines += [_turn_line(t) for t in prev]
    lines.append(f"This round (round {b.round + 1}) so far:")
    lines += [_turn_line(t) for t in b.turns_this_round] or ["  (you speak first)"]
    return "\n".join(lines)


def _table_lines(m: Mind, evals: list[dict]) -> str:
    lines = []
    for t, e in zip(m.brief.table, evals):
        broken = ", ".join(short(x) for x in e["breaks_red_lines_of"]) or "nobody"
        support = ", ".join(short(x) for x in t.accept) or "nobody yet"
        lines.append(
            f"- {t.id} by {short(t.author)}: {t.plain} {t.plan.model_dump()} | accepted by: "
            f"{support} | your score {e['my_score']:.2f}/1 | breaks red line of: {broken}"
        )
    return "\n".join(lines) or "(nothing yet: you open the debate)"


def _llm_messages(m: Mind, evals: list[dict], policy: Ballot) -> list[dict]:
    a = ADVISORS[m.id]
    b = m.brief
    rounds_left = b.max_rounds - b.round - 1
    user = {
        "year": b.year,
        "round": f"{b.round + 1} of {b.max_rounds}",
        "rounds_left_after_this": rounds_left,
        "city": b.state,
        "beliefs_about_this_year": b.beliefs,
        "news_this_year": [n["text"] for n in b.news],
        "recent_years": [
            {k: h.get(k) for k in ("year", "outcome", "plain", "author", "dissent", "result")}
            for h in b.history[-2:]
        ],
        "suggested_plan": policy.proposal.model_dump(),
        "suggested_plan_backs": policy.back,
        "suggested_plan_score_for_you": policy.trace["chosen"]["my_score"],
    }
    if b.errors:
        user["response_correction"] = b.errors
    if b.phase == "runoff":
        ra, rb = b.runoff
        ask = (
            f"RUNOFF. A = {ra.id} by {short(ra.author)}: {ra.plain}. "
            f"B = {rb.id} by {short(rb.author)}: {rb.plain}.\n"
            f"Plans:\n{_table_lines(m, evals)}\nVotes so far:\n"
            + ("\n".join(_turn_line(t) for t in b.turns_this_round) or "  (none yet)")
            + f"\nIt is your turn to vote, {a.short}.\n{RUNOFF_ASK}"
        )
    else:
        urgency = (
            "\nThis is the LAST round: after it only the votes count. Accept the best plan you "
            "can live with."
            if rounds_left == 0 else ""
        )
        ask = (
            f"Plans on the table, with their support right now:\n{_table_lines(m, evals)}\n\n"
            f"{_conversation(m)}\n\nIt is your turn, {a.short}. Reply to the others.{urgency}"
        )
    return [
        {"role": "system",
         "content": SYSTEM.format(council=_council_lines(), **{**a.__dict__, "short": a.short})},
        {"role": "user", "content": json.dumps(user) + "\n\n" + ask},
    ]


async def decide(advisor_id: str, brief: Brief) -> Ballot:
    policy = policy_ballot(advisor_id, brief)
    if settings.llm_mode == "mock":
        return sign(bind(policy, brief))
    if settings.llm_mode != "bayleaf":
        raise llm.LLMUnavailable(f"Unknown LLM_MODE: {settings.llm_mode}")
    m = Mind(advisor_id, brief)
    evals = m.evaluate(m.value(m.best()))
    metadata = {}
    try:
        reply = await llm.chat(_llm_messages(m, evals, policy))
        metadata = reply.meta()
        data = llm.parse_json(reply.content, strict=True)
        ballot = (_llm_runoff if brief.phase == "runoff" else _llm_turn)(m, evals, data, reply)
    except (llm.LLMUnavailable, ValueError, KeyError, TypeError, StopIteration) as exc:
        metadata = getattr(exc, "usage", None) or metadata
        ballot = policy.model_copy(update={"source": "llm", "error": f"{type(exc).__name__}: {exc}",
                                          "speech": "Unable to produce a valid turn response.",
                                          "trace": {"llm": metadata}})
    return sign(bind(ballot, brief))


def _speech(data: dict, words: int = SPEECH_WORDS) -> str:
    return _trim(clean(data.get("speech"), 400), words)


def _llm_turn(m: Mind, evals: list[dict], data: dict, reply) -> Ballot:
    veto = [e["id"] for e in evals if e["breaks_my_red_line"]]
    back = data.get("back")
    back = back if isinstance(back, str) and back in m.tabled and back not in veto else None
    note = None
    if back:
        d = m.tabled[back]
    else:
        p = data["proposal"]
        d = legalize(m.state, Plan.model_validate(p).decision())
        if m.sc.vetoes(m.id, d):  # the model proposed past its own red line
            raise ValueError("Proposal crosses this advisor's red line.")
    accept = [i for i in data.get("accept", []) if i in m.tabled and i not in veto]
    if back and back not in accept:
        accept.append(back)
    for e in evals:
        e["llm_vote"] = "veto" if e["id"] in veto else "accept" if e["id"] in accept else "pass"
    speech = _speech(data)
    extra = {"reasoner": "llm", "llm": reply.meta()}
    if note:
        extra["override"] = note
    return Ballot(
        advisor=m.id,
        year=m.brief.year,
        round=m.brief.round,
        phase="discuss",
        proposal=_plan(d),
        back=back,
        accept=accept,
        veto=veto,
        speech=speech,
        addressed=mentions(speech, exclude=m.id),
        source="llm",
        trace=m.trace(d, evals, clean(data.get("thought")), **extra),
    )


def _llm_runoff(m: Mind, evals: list[dict], data: dict, reply) -> Ballot:
    ids = [t.id for t in m.brief.runoff]
    _forced_vote, veto, forced = runoff_choice(m)
    vote = data.get("vote")
    if vote not in ids or vote in veto:
        raise ValueError("Runoff vote is missing, unknown, or vetoed.")
    d = m.tabled[vote]
    speech = _speech(data, 18)
    return Ballot(
        advisor=m.id,
        year=m.brief.year,
        round=m.brief.round,
        phase="runoff",
        proposal=_plan(d),
        veto=veto,
        runoff_vote=vote,
        speech=speech,
        addressed=mentions(speech, exclude=m.id),
        source="llm",
        trace=m.trace(d, evals, clean(data.get("thought")), reasoner="llm", llm=reply.meta(),
                      forced_by_red_line=forced),
    )
