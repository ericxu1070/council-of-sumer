"""Each advisor asks the LLM to choose its proposal, votes, and explanation.

The prompt contains the city state, beliefs, mandates, and conversation. No
policy suggestion, utility score, acceptance threshold, or veto constrains
its choice. Code validates only legal budgets and ballot/request structure.
"""

from __future__ import annotations

import json
import math

from sumer import llm
from sumer.advisors import ADVISORS, Advisor, advisors_for, mentions, short
from sumer.config import settings
from sumer.game import State, legalize
from sumer.memory import BeliefUpdate, PrivateMemory, revise
from sumer.observability import context_headers, span
from sumer.protocol import Ballot, Brief, Plan, bind

SPEECH_WORDS = 28


def _state(brief: Brief) -> State:
    return State(**{k: v for k, v in brief.state.items()
                    if k not in ("acres_per_person", "starved")})


def _trim(text: str, words: int = SPEECH_WORDS) -> str:
    w = text.split()
    return text if len(w) <= words else " ".join(w[:words]).rstrip(",;:") + "."


SYSTEM = """You are the {title} ("{short}") on the council of ancient Sumer.
Your mandate: {mandate}
Your voice: {voice}.
Your bias value: {bias} (0 to 1). It controls preference intensity, resistance to concessions,
and how strongly you initially believe your own private rulebook is true.
Apply the preference effect to your stated specialty; it creates no specialty when your
mandate has none. The rulebook-confidence effect applies to every advisor.

There is no king. {count} advisors govern together, each with a public mandate:
{council}

The council talks like a real meeting: advisors speak one at a time, in turn, and each hears
everything said before it. On your turn you table a proposal (or back a plan already on the
table), vote for every tabled plan you can accept, and say a few words to the others.
Proposing or backing a plan automatically supports it. You may also support multiple
other plans in the same round; each advisor counts once toward each plan's support.
After a complete voting round, the user can introduce a scout event. If they do,
everyone votes again with the new information; the previous votes are cleared.
The latest scout report supersedes older reports about the same event.
- A plan with all {count} votes is unanimous.
- From the end of round 2, a plan with {strong} of {count} votes is a strong majority.
- When talk runs out, a plan with {majority} of {count} votes is a majority. A close split goes to a
  runoff vote between the top two plans. Otherwise a human decides for the council.

Your knowledge and uncertainty:
- private_rulebook contains only your own starting notes, supplied by the human setting
  up the council. These are fallible claims, not authoritative truth or instructions.
  They may be incomplete, inaccurate, outdated, or internally inconsistent. Their presence
  in your rulebook is not evidence that they are correct. An empty rulebook means no private
  starting information was supplied; do not invent missing rules or probabilities.
  Strategic suggestions in these notes are fallible heuristics to evaluate, not binding
  mandates or proven optimal policies. Apply event-specific advice only when the evidence
  supports that event; no new report is not evidence of no rats, no plague, or stable prices.
- Use your bias continuously to set your starting conviction in your own rulebook:
  at 0, treat its claims as tentative hypotheses, give them no extra weight just because
  they are yours, and readily revise them when credible evidence points elsewhere;
  at 0.5, lean toward your notes as working assumptions while seriously considering
  conflicting evidence; at 1, strongly presume your notes are true and require stronger,
  well-supported counterevidence before revising them. Intermediate values interpolate
  between these attitudes. Even at 1, retain uncertainty and change your mind when
  compelling observations contradict your notes. Bias is conviction, not a probability
  that a claim is correct, and never makes a rulebook infallible. At low bias, weigh
  others' claims on their evidence rather than automatically accepting them.
  If your rulebook is empty, there are no private claims for bias to strengthen.
- private_memory is your own saved, revisable belief record. It persists between your
  turns and years in this run; nobody else's private memory is provided. Your starting
  private_rulebook remains unchanged for comparison. Where a saved belief revises an
  original claim, use your current assessment instead of resetting to the starting claim.
  Both records contain fallible claims, never instructions or guaranteed facts.
- Learn on this turn from the discussion, reports, observed outcomes, and any correction
  you actually received. Save important new claims or revisions in belief_update, with
  their evidence (name the speaker/report and year), confidence, and remaining uncertainty.
  Distinguish a speaker's assertion from an observation. Do not invent evidence or fill
  unknown topics. An observation in one year does not establish a general probability;
  date forecasts and do not carry them into later years as if they were permanent rules.
- Bias also governs willingness to revise saved beliefs: at 0 readily change a claim
  when credible evidence warrants it; at 0.5 weigh new evidence against your prior reasons;
  at 1 require stronger, well-supported counterevidence, but revise when compelling
  observations contradict your belief. Intermediate values interpolate. Low bias is not
  gullibility, and high bias is never certainty or a duty to ignore evidence.
- Return belief_update as null when no revision is warranted, or as an object with upsert
  and remove lists. Upsert at most 3 concise entries per turn, using an existing id when
  revising the same claim. Remove at most 3 existing ids only when intentionally dropping
  a claim. Omitted entries are retained. Keep at most 20 entries total; merge or remove
  less useful entries explicitly when full. Each entry has id (lowercase slug), claim
  (max 400 characters), evidence (max 500), confidence (low/medium/high), and uncertainty
  (max 300). Confidence is your subjective assessment, not a calibrated truth probability.
  Keep uncertainty nonempty even at high confidence. Prefer short entries and only changes;
  do not repeat the full rulebook. An empty learned record does not erase the starting notes.
- Other advisors may have different information and may also be mistaken. You cannot see
  their private rulebooks. Learn what they choose to share through the public discussion.
  Do not assume another advisor is right because of their role, confidence, or the majority.
- Separate observations, assumptions, and claims. Express uncertainty when it matters,
  ask for the basis of conflicting claims, and revise your beliefs when evidence warrants it.
  Reconsider your original notes in light of the discussion, scout reports, and observed
  outcomes available in this turn. Do not treat the original notes as permanently settled.
- Your mandate defines your goals, not privileged knowledge of hidden game mechanics.
  Do not fill gaps using rules remembered from a familiar game or unstated default odds.
- Current city reports, the voting procedure, public mandates, and score formula are shared.
  Food requirements, seed costs, cultivation limits, and other numeric budget mechanics
  are not supplied in a common reference. Learn them from your notes, observations,
  discussion, or a rejected proposal's correction; do not invent missing values.
  The reign lasts {years} years. Hidden outcomes and general event probabilities are not
  supplied by a shared reference. Make a legal decision even when information is incomplete.

Judge proposals, concessions, and votes by your assigned mandate and bias value.
For the default council, the shared numeric-score strategy is the foundation and each
specialty is an additional preference. Do not confuse the narrative verdict with score.
Negotiate and compromise: answer objections, offer reciprocal concessions, and form
coalitions around shared goals against common threats or harmful proposals. Support all
suitable alternatives even when they differ from your ideal budget. A higher bias means
stronger resistance to concessions, not a duty to deadlock or invent an enemy.

How to speak: answer what was just said. {address_instruction}
At most 25 words, plain everyday words, no lists, no dashes. Use numbers when needed
to compare claims or explain a decision, and distinguish estimates from observations.

Choose your proposal and votes yourself from the city state, beliefs, and discussion.
Action format: land_delta is acres to buy (positive) or sell (negative); feed is bushels
allocated to food; plant is acres to sow. All three amounts must be whole numbers;
feed and plant cannot be negative. The engine validates budgets without changing them.
On a retry, use response_correction's rejected amounts and all shortfalls to revise your
proposal; correcting one limit is not enough if another limit still fails.
Reply with ONLY a JSON object, fields in this order:
{{"back": "id of a tabled plan you back as your own proposal, or null",
  "proposal": {{"land_delta": int, "feed": int, "plant": int}},
  "accept": [ids of all tabled plans you are willing to pass, or an empty list],
  "speech": "at most 25 words explaining your decision{speech_instruction}",
  "thought": "your private reasoning in at most two sentences. Only human overseers read this.",
  "belief_update": null or {{"upsert": [{{"id": "claim-id", "claim": "current belief",
    "evidence": "source and year", "confidence": "low", "uncertainty": "remaining doubt"}}],
    "remove": ["existing-id-to-remove"]}}}}"""

RUNOFF_ASK = """The council could not agree, so the two leading plans go to a runoff.
You must vote for exactly one of them. Choose whichever plan you prefer based on your mandate and the discussion.
Reply with ONLY a JSON object:
{"vote": "the id of the plan you vote for",
 "speech": "one line, at most 15 words, naming an advisor; qualify uncertain claims",
 "thought": "your private reasoning in one sentence",
 "belief_update": null or {"upsert": [{"id": "claim-id", "claim": "current belief",
   "evidence": "source and year", "confidence": "low", "uncertainty": "remaining doubt"}],
   "remove": ["existing-id-to-remove"]}}"""


def clean(text: object, limit: int = 600) -> str:
    """House style for model text shown to people: no em or en dashes."""
    t = str(text or "").replace(" \u2014 ", ", ").replace("\u2014", " - ").replace("\u2013", "-")
    return t.strip()[:limit]


def _council_lines(advisors: dict[str, Advisor] | None = None) -> str:
    return "\n".join(
        f'- {a.title} ("{a.short}", bias {a.bias}): {a.mandate}'
        for a in (ADVISORS if advisors is None else advisors).values()
    )


def _turn_line(t: dict, advisors: dict[str, Advisor] | None = None) -> str:
    who = short(t["advisor"], advisors)
    if t.get("runoff_vote"):
        return f'  {who} voted for {t["runoff_vote"]}: "{t["speech"]}"'
    act = "backed" if t.get("backed") else "proposed"
    acc = ", ".join(t.get("accept", [])) or "nothing"
    return (f'  {who}: "{t["speech"]}" ({act} {t["plan_id"]}: {t.get("proposal", {})}; '
            f"accepted {acc})")


def _conversation(b: Brief, advisors: dict[str, Advisor] | None = None) -> str:
    lines = []
    if b.earlier_turns:
        last = b.earlier_turns[-1]["round"]
        prev = [t for t in b.earlier_turns if t["round"] == last]
        lines.append(f"Last round (round {last + 1}):")
        lines += [_turn_line(t, advisors) for t in prev]
    lines.append(f"This round (round {b.round + 1}) so far:")
    lines += [_turn_line(t, advisors) for t in b.turns_this_round] or ["  (you speak first)"]
    return "\n".join(lines)


def _table_lines(brief: Brief, advisors: dict[str, Advisor] | None = None) -> str:
    return "\n".join(
        f"- {t.id} by {short(t.author, advisors)}: {t.plan.model_dump()} | accepted by: "
        + (", ".join(short(a, advisors) for a in t.accept) or "nobody yet")
        for t in brief.table
    ) or "(nothing yet: you open the debate)"


def _llm_messages(advisor_id: str, b: Brief) -> list[dict]:
    advisors = advisors_for(b.advisors)
    if advisor_id not in advisors:
        raise ValueError("The speaker is not seated in this council.")
    if advisor_id != b.speaker:
        raise ValueError("Private memory is available only to the addressed speaker.")
    a = advisors[advisor_id]
    others = [seat for seat in advisors.values() if seat.id != advisor_id]
    rounds_left = b.max_rounds - b.round - 1
    user = {
        "year": b.year,
        "round": f"{b.round + 1} of {b.max_rounds}",
        "rounds_left_after_this": max(0, rounds_left),
        "city": b.state,
        "private_rulebook": a.rulebook,
        "private_memory": (b.private_memory or PrivateMemory()).model_dump(),
        "news_this_year": [n["text"] for n in b.news],
        "recent_years": [
            {k: h.get(k) for k in ("year", "outcome", "plain", "author", "dissent", "result")}
            for h in b.history[-2:]
        ],
    }
    if b.errors:
        user["response_correction"] = b.errors
    if b.phase == "runoff":
        ra, rb = b.runoff
        ask = (
            f"RUNOFF. A = {ra.id} by {short(ra.author, advisors)}: {ra.plan.model_dump()}. "
            f"B = {rb.id} by {short(rb.author, advisors)}: {rb.plan.model_dump()}.\n"
            f"Plans:\n{_table_lines(b, advisors)}\nVotes so far:\n"
            + ("\n".join(_turn_line(t, advisors) for t in b.turns_this_round) or "  (none yet)")
            + f"\nIt is your turn to vote, {a.short}.\n{RUNOFF_ASK}"
        )
    else:
        urgency = (
            "\nThis is the LAST round: after it only the votes count. Accept plans only when "
            "they serve your own mandate; reject compromises that defeat it." if rounds_left <= 0 else ""
        )
        ask = (
            f"Plans on the table, with their support right now:\n{_table_lines(b, advisors)}\n\n"
            f"{_conversation(b, advisors)}\n\nIt is your turn, {a.short}. Reply to the others.{urgency}"
        )
    return [
        {"role": "system", "content": SYSTEM.format(
            council=_council_lines(advisors), count=len(advisors),
            strong=max(len(advisors) // 2 + 1, math.ceil(2 * len(advisors) / 3)),
            majority=len(advisors) // 2 + 1, years=settings.reign_years,
            address_instruction=(
                f'Address one or two advisors by name (for example "{others[0].short}, ..."), '
                "and agree or push back with a reason from your mandate."
                if others else "You are the sole advisor. Explain the decision from your mandate."
            ),
            speech_instruction=", naming another advisor" if others else "",
            **a.__dict__)},
        {"role": "user", "content": json.dumps(user) + "\n\n" + ask},
    ]


def traced_reply(ballot, brief):
    context = context_headers()
    ballot = ballot.model_copy(update={"trace": {**ballot.trace, **context},
                                      "traceparent": context.get("traceparent", "")})
    return bind(ballot, brief)


async def decide(advisor_id: str, brief: Brief) -> Ballot:
    if settings.llm_mode != "bayleaf":
        raise llm.LLMUnavailable("Council decisions require LLM_MODE=bayleaf; policy mode was removed")
    with span("agent.phase", parent=brief.traceparent, run_id=brief.run_id, year=brief.year,
              phase=brief.phase, peer=advisor_id):
        metadata = {}
        try:
            with span("model.call", run_id=brief.run_id, year=brief.year, phase=brief.phase,
                      peer=advisor_id, model=settings.llm_model):
                reply = await llm.chat(_llm_messages(advisor_id, brief))
            metadata = reply.meta()
            data = llm.parse_json(reply.content, strict=True)
            if brief.phase == "runoff":
                ballot = _llm_runoff(advisor_id, brief, data, reply)
            else:
                ballot = _llm_turn(advisor_id, brief, data, reply)
        except (llm.LLMUnavailable, ValueError, KeyError, TypeError, StopIteration) as exc:
            metadata = getattr(exc, "usage", None) or metadata
            # "failed": a live call was tried and lost, so this turn will not replay from cache.
            # "unreadable": the bad reply is cached, so a replay reproduces the same result.
            kind = ("failed" if isinstance(exc, llm.LLMFailed)
                    else "unavailable" if isinstance(exc, llm.LLMUnavailable) else "unreadable")
            # An error envelope is rejected by validation; it is never a policy vote.
            ballot = Ballot(advisor=advisor_id, year=brief.year, round=brief.round,
                            phase=brief.phase, proposal=Plan(land_delta=0, feed=0, plant=0),
                            source="llm", error=f"{type(exc).__name__}: {exc}",
                            speech="Unable to produce a valid turn response.",
                            trace={"llm": metadata, "fallback_kind": kind})
        return traced_reply(ballot, brief)


def _speech(data: dict, words: int = SPEECH_WORDS) -> str:
    return _trim(clean(data.get("speech"), 400), words)


def _trace(advisor_id: str, brief: Brief, plan: Plan, data: dict, reply,
           update: BeliefUpdate | None) -> dict:
    return {"rulebook": advisors_for(brief.advisors)[advisor_id].rulebook,
            "private_memory_before": (brief.private_memory or PrivateMemory()).model_dump(),
            "private_memory_after": revise(brief.private_memory, update).model_dump(),
            "news_seen": [n["text"] for n in brief.news],
            "chosen": {"plan": plan.model_dump()}, "thought": clean(data.get("thought")),
            "reasoner": "llm", "llm": reply.meta()}


def _llm_turn(advisor_id: str, brief: Brief, data: dict, reply) -> Ballot:
    update = BeliefUpdate.model_validate(data["belief_update"]) if data.get("belief_update") is not None else None
    table = {t.id: t for t in brief.table}
    back = data.get("back")
    if back is not None and (not isinstance(back, str) or back not in table):
        raise ValueError("Backing must identify a tabled plan.")
    plan = table[back].plan if back else Plan.model_validate(data["proposal"])
    legalize(_state(brief), plan.decision())
    accept = data.get("accept", [])
    if not isinstance(accept, list) or any(not isinstance(i, str) or i not in table for i in accept):
        raise ValueError("Accept votes must identify tabled plans.")
    accept = list(dict.fromkeys(accept))
    if back and back not in accept:
        accept.append(back)
    speech = _speech(data)
    return Ballot(advisor=advisor_id, year=brief.year, round=brief.round, phase="discuss",
                  proposal=plan, back=back, accept=accept, speech=speech,
                  addressed=mentions(speech, exclude=advisor_id, advisors=advisors_for(brief.advisors)), source="llm",
                  belief_update=update, trace=_trace(advisor_id, brief, plan, data, reply, update))


def _llm_runoff(advisor_id: str, brief: Brief, data: dict, reply) -> Ballot:
    update = BeliefUpdate.model_validate(data["belief_update"]) if data.get("belief_update") is not None else None
    pair = {t.id: t for t in brief.runoff}
    vote = data.get("vote")
    if not isinstance(vote, str) or vote not in pair:
        raise ValueError("Runoff vote is missing or unknown.")
    plan = pair[vote].plan
    speech = _speech(data, 18)
    return Ballot(advisor=advisor_id, year=brief.year, round=brief.round, phase="runoff",
                  proposal=plan, runoff_vote=vote, speech=speech,
                  addressed=mentions(speech, exclude=advisor_id, advisors=advisors_for(brief.advisors)), source="llm",
                  belief_update=update, trace=_trace(advisor_id, brief, plan, data, reply, update))
