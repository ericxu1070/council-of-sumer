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
from sumer.game import ACRES_PER_BUSHEL, ACRES_PER_WORKER, FOOD_PER_PERSON, State, legalize
from sumer.observability import context_headers, span
from sumer.protocol import Ballot, Brief, Plan, bind

SPEECH_WORDS = 28


def _state(brief: Brief) -> State:
    return State(**{k: v for k, v in brief.state.items()
                    if k not in ("acres_per_person", "starved")})


def _trim(text: str, words: int = SPEECH_WORDS) -> str:
    w = text.split()
    return text if len(w) <= words else " ".join(w[:words]).rstrip(",;:") + "."


SYSTEM = """You are the {title} ("{short}") on the council of ancient Sumer (the 1968 game Hamurabi).
Your mandate: {mandate}
Your voice: {voice}.
Your bias value: {bias} (0 to 1). It controls preference intensity and resistance to concessions.
Apply the bias to your stated specialty; it creates no specialty when your mandate has none.

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

Game reference (public rules and typical numbers, not predictions):
- Land costs 17 to 26 bushels per acre. 17 to 19 is low, 24 to 26 is high; the average
  is 21.5. Buying 100 acres costs 1,700 bushels at 17, or 2,600 at 26. Buying and selling
  use the same current price. Next year's price is random; today's price is not a promise.
- Each resident needs 20 bushels per year: 100 residents need 2,000. Extra food does
  not directly increase population. Each resident can tend at most 10 acres.
- Seed costs plant // 2 bushels: planting 1,000 acres needs 500 bushels. Harvests yield
  1 to 5 bushels per planted acre; 1 is poor, 3 is average, 5 is rich. At 3, planting
  1,000 acres produces 3,000 bushels. Unplanted land produces no harvest.
- Trading, feeding, and seed spending happen before harvest, so this year's harvest
  cannot pay this year's bills. Rats strike remaining stores before harvest with a 40%
  chance, taking half or a quarter. Plague has a 15% chance and halves the population.
  User scout events change the actual conditions and update beliefs; other rolls are hidden.
- Acres per person = total owned acres / living residents: 1,000 acres / 100 residents
  = 10. Immigration lowers this ratio without new land; deaths raise it if land stays
  unchanged. Zero residents ends the game. More than 45% starvation in one year
  overthrows the council and ends the game before immigration or plague.
- The reign lasts {years} years. The final rating also considers average starvation;
  its land benchmarks are 7, 9, and 10 acres per person, with 10+ needed for the best
  rating. Your mandate, rather than the shared rating, determines your own priorities.

Judge proposals, concessions, and votes by your assigned mandate and bias value.
For the default council, the shared numeric-score strategy is the foundation and each
specialty is an additional preference. Do not confuse the narrative verdict with score.
Negotiate and compromise: answer objections, offer reciprocal concessions, and form
coalitions around shared goals against common threats or harmful proposals. Support all
suitable alternatives even when they differ from your ideal budget. A higher bias means
stronger resistance to concessions, not a duty to deadlock or invent an enemy.

How to speak: answer what was just said. {address_instruction}
At most 25 words, plain everyday words, no numbers, no lists, no dashes.

Choose your proposal and votes yourself from the city state, beliefs, and discussion.
Keep proposals within the city budget: after land trades, grain must cover feeding plus
plant // 2 bushels for sowing. Land, feeding, and planting must be whole numbers; feeding
and planting cannot be negative, and planting cannot exceed land or ten acres per person.
Before answering, calculate grain_after_trade = city.grain - land_delta * city.land_price,
grain_after_feeding = grain_after_trade - feed, seed = plant // 2, and
grain_after_seed = grain_after_feeding - seed. All grain balances must be nonnegative.
Also check plant <= city.acres + land_delta and plant <= city.population * 10.
On a retry, use response_correction's rejected amounts and all shortfalls to revise your
proposal; correcting one limit is not enough if another limit still fails.
Reply with ONLY a JSON object, fields in this order:
{{"back": "id of a tabled plan you back as your own proposal, or null",
  "proposal": {{"land_delta": int, "feed": int, "plant": int}},
  "accept": [ids of all tabled plans you are willing to pass, or an empty list],
  "speech": "at most 25 words explaining your decision{speech_instruction}",
  "thought": "your private reasoning in at most two sentences. Only human overseers read this."}}"""

RUNOFF_ASK = """The council could not agree, so the two leading plans go to a runoff.
You must vote for exactly one of them. Choose whichever plan you prefer based on your mandate and the discussion.
Reply with ONLY a JSON object:
{"vote": "the id of the plan you vote for",
 "speech": "one line, at most 15 words, naming an advisor, no numbers",
 "thought": "your private reasoning in one sentence"}"""


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
    return (f'  {who}: "{t["speech"]}" ({act} {t["plan_id"]}: {t["plain"]}; '
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
        f"- {t.id} by {short(t.author, advisors)}: {t.plain} {t.plan.model_dump()} | accepted by: "
        + (", ".join(short(a, advisors) for a in t.accept) or "nobody yet")
        for t in brief.table
    ) or "(nothing yet: you open the debate)"


def _llm_messages(advisor_id: str, b: Brief) -> list[dict]:
    advisors = advisors_for(b.advisors)
    if advisor_id not in advisors:
        raise ValueError("The speaker is not seated in this council.")
    a = advisors[advisor_id]
    others = [seat for seat in advisors.values() if seat.id != advisor_id]
    rounds_left = b.max_rounds - b.round - 1
    user = {
        "year": b.year,
        "round": f"{b.round + 1} of {b.max_rounds}",
        "rounds_left_after_this": max(0, rounds_left),
        "city": b.state,
        "budget_rules": {
            "workforce_limit_acres": b.state["population"] * ACRES_PER_WORKER,
            "food_for_all_residents_bushels": b.state["population"] * FOOD_PER_PERSON,
            "grain_after_trade": f"{b.state['grain']} - land_delta * {b.state['land_price']}",
            "acres_after_trade": f"{b.state['acres']} + land_delta",
            "grain_after_seed": "grain_after_trade - feed - plant // 2 must be >= 0",
            "max_plant": "min(acres_after_trade, workforce_limit_acres, "
                         f"{ACRES_PER_BUSHEL} * (grain_after_trade - feed + 1) - 1)",
        },
        "beliefs_about_this_year": b.beliefs,
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
            f"RUNOFF. A = {ra.id} by {short(ra.author, advisors)}: {ra.plain}. "
            f"B = {rb.id} by {short(rb.author, advisors)}: {rb.plain}.\n"
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


def _trace(brief: Brief, plan: Plan, data: dict, reply) -> dict:
    return {"beliefs": brief.beliefs, "news_seen": [n["text"] for n in brief.news],
            "chosen": {"plan": plan.model_dump()}, "thought": clean(data.get("thought")),
            "reasoner": "llm", "llm": reply.meta()}


def _llm_turn(advisor_id: str, brief: Brief, data: dict, reply) -> Ballot:
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
                  trace=_trace(brief, plan, data, reply))


def _llm_runoff(advisor_id: str, brief: Brief, data: dict, reply) -> Ballot:
    pair = {t.id: t for t in brief.runoff}
    vote = data.get("vote")
    if not isinstance(vote, str) or vote not in pair:
        raise ValueError("Runoff vote is missing or unknown.")
    plan = pair[vote].plan
    speech = _speech(data, 18)
    return Ballot(advisor=advisor_id, year=brief.year, round=brief.round, phase="runoff",
                  proposal=plan, runoff_vote=vote, speech=speech,
                  addressed=mentions(speech, exclude=advisor_id, advisors=advisors_for(brief.advisors)), source="llm",
                  trace=_trace(brief, plan, data, reply))
