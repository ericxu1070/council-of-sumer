"""How the council decides one year, with no king.

Protocol (public, so every advisor can check the clerk's tally):
  The seven advisors speak one at a time, in a fixed order that rotates by one
  seat every round. On its turn an advisor sees the table (every plan with its
  current support), what the earlier speakers said this round, and the rounds
  before. It then tables a proposal (or backs a plan already on the table),
  votes accept / veto on the tabled plans, and says a few words to the others.
  A new proposal is tabled at once, so later speakers in the same round can
  accept it. An advisor's latest votes stand until its next turn, and a plan
  nobody backs any more is withdrawn.

  News scheduled for the year arrives at the start of round 2, so the opening
  proposals and the reaction to the news are both visible. News a human sends
  arrives before the next turn.

  Stop rules, checked after every turn:
    CONSENSUS       a tabled plan is accepted by all seven.           -> adopt it
    STRONG MAJORITY once round 2 is complete, a plan with 5+ of 7
                    accepts and no red-line veto.                     -> adopt it,
                                                                         record dissent
    stalemate       a whole round with no proposal change and no news -> final rule
  Final rule (stalemate, or the last round ends):
    MAJORITY        the best plan has 4+ of 7 accepts, no veto, and no
                    tie.                                              -> adopt it,
                                                                         record dissent
    RUNOFF          the best plan has exactly 3 of 7, the top two tie,
                    or the leader carries a red-line veto: the top two
                    plans go head to head. Each advisor, in turn, votes
                    A or B (it must vote against a plan that breaks its
                    red line). 4+ of 7 votes for a plan nobody vetoed -> adopt it
    ESCALATE        deadlock (under 3 accepts), both finalists cross a
                    red line, or the runoff winner crosses one.       -> ask a human.
                    If nobody answers in time, use the pre-committed safe plan.

The clerk relays messages and applies this rule. It never ranks plans by its
own judgment.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sumer.advisors import (
    ADVISOR_IDS,
    N_ADVISORS,
    Beliefs,
    Scorer,
    apply_report,
    plain,
    safe_plan,
    short,
)
from sumer.game import Decision, State, legalize
from sumer.observability import context_headers, span
from sumer.protocol import (
    Ballot,
    Brief,
    Plan,
    TabledProposal,
    proposal_id,
    provision_identities,
    state_hash,
    uid,
    validate_reply,
    verify,
)

log = logging.getLogger(__name__)

Emit = Callable[[str, dict], Awaitable[None]]
# Sends a Brief to its one speaker and returns that advisor's Ballot (None if it never answered)
Ask = Callable[[Brief], Awaitable[Ballot | None]]
# Called on escalation with (options, reason); returns the chosen option id or None
AskHuman = Callable[[list[dict], str], Awaitable[str | None]]

STRONG_MAJORITY = 5  # of 7, adopted early once round 2 is complete
MAJORITY = 4  # of 7, adopted by the final rule
RUNOFF_FLOOR = 3  # of 7, the least support that earns a runoff
STRONG_MAJORITY_FROM_ROUND = 1  # 0-based: from the end of round 2


@dataclass
class YearResult:
    year: int
    outcome: str  # "consensus" | "majority" | "runoff" | "escalated"
    plan: Decision
    adopted_id: str
    rounds: int
    adopted_author: str = ""
    plain: str = ""
    dissent: list[str] = field(default_factory=list)
    reason: str = ""
    news: list[dict] = field(default_factory=list)
    opening: dict[str, Plan] = field(default_factory=dict)
    after_news: dict[str, Plan] = field(default_factory=dict)
    satisfaction: dict[str, float] = field(default_factory=dict)
    human_choice: str | None = None
    runoff: dict | None = None  # {"a", "b", "trigger", "votes", "count", "winner", "reason"}
    turns: int = 0
    certificate: dict | None = None
    human_resolution: dict | None = None
    summary: str = ""  # the narrator's plain-English line, set after the year is played

    def record(self) -> dict:
        return {
            "year": self.year,
            "outcome": self.outcome,
            "plan": self.plan.__dict__,
            "plain": self.plain,
            "adopted": self.adopted_id,
            "author": self.adopted_author,
            "rounds": self.rounds,
            "dissent": self.dissent,
            "reason": self.reason,
            "runoff_trigger": (self.runoff or {}).get("trigger"),
            "news": [n["text"] for n in self.news],
        }


class TechnicalFailure(Exception):
    pass


class Deadlock(Exception):
    pass


async def _noop_emit(kind: str, data: dict) -> None:
    return None


def speaking_order(year: int, rnd: int) -> list[str]:
    """Fixed seating; the first seat moves one place every round and every year."""
    k = (year - 1 + rnd) % N_ADVISORS
    return ADVISOR_IDS[k:] + ADVISOR_IDS[:k]


class Table:
    """The plans on the table, who backs each, and every advisor's standing votes."""

    def __init__(self, state: State):
        self.state = state
        self.plans: dict[str, TabledProposal] = {}
        self.current: dict[str, str] = {}  # advisor -> id of the plan it proposes or backs
        self.accepts: dict[str, set[str]] = {}
        self.vetoes: dict[str, set[str]] = {}

    def support(self, pid: str) -> tuple[list[str], list[str]]:
        veto = [a for a in ADVISOR_IDS if pid in self.vetoes.get(a, ())]
        acc = {a for a in ADVISOR_IDS if pid in self.accepts.get(a, ())}
        acc |= {a for a, p in self.current.items() if p == pid}
        acc -= set(veto)
        return sorted(acc, key=ADVISOR_IDS.index), veto

    def snapshot(self) -> list[TabledProposal]:
        out = []
        for t in self.plans.values():
            acc, veto = self.support(t.id)
            backers = [a for a in ADVISOR_IDS if self.current.get(a) == t.id]
            out.append(t.model_copy(update={"accept": acc, "veto": veto, "backers": backers}))
        return out

    def rows(self) -> list[dict]:
        return [
            {"id": t.id, "author": t.author, "plain": t.plain, "plan": t.plan.model_dump(),
             "accept": t.accept, "veto": t.veto, "backers": t.backers}
            for t in self.snapshot()
        ]

    def ranked(self) -> list[dict]:
        order = list(self.plans)
        return sorted(self.rows(), key=lambda r: (-len(r["accept"]), len(r["veto"]),
                                                  order.index(r["id"])))

    def place(self, who: str, b: Ballot, year: int, rnd: int) -> tuple[str, bool]:
        """Table or back the speaker's proposal. Returns (plan id, changed)."""
        prev = self.current.get(who)
        if b.back and b.back in self.plans:
            pid = b.back
        else:
            d = legalize(self.state, Decision(b.proposal.land_delta, b.proposal.feed,
                                              b.proposal.plant))
            p = Plan(land_delta=d.land_delta, feed=d.feed, plant=d.plant)
            pid = next((t.id for t in self.plans.values() if t.plan == p), None)
            if pid is None:
                pid = proposal_id(who, year, rnd)
                self.plans[pid] = TabledProposal(
                    id=pid, author=who, plan=p, speech=b.speech, plain=plain(d, self.state)
                )
        self.current[who] = pid
        if prev and prev != pid and prev not in self.current.values():
            del self.plans[prev]  # nobody backs it any more: withdrawn
        return pid, pid != prev

    def vote(self, who: str, b: Ballot, allowed: set[str]) -> None:
        self.vetoes[who] = {i for i in b.veto if i in allowed}
        self.accepts[who] = {i for i in b.accept if i in allowed} - self.vetoes[who]


async def deliberate_year(
    *,
    seed: int,
    state: State,
    ask: Ask,
    history: list[dict],
    max_rounds: int,
    scheduled_news: dict | None,
    injected_news: list[dict] | None = None,
    ask_human: AskHuman | None = None,
    emit: Emit = _noop_emit,
    run_id: str | None = None,
    peer_keys: dict[str, str] | None = None,
) -> YearResult:
    run_id = run_id or uid()
    peer_keys = peer_keys or provision_identities()
    transport_ask = ask
    accepted: dict[str, Ballot] = {}

    async def ask(brief):
        with span("council.turn", run_id=run_id, year=state.year, phase=brief.phase, peer=brief.speaker, cycle=brief.round):
            request = brief.model_copy(update={"run_id": run_id, "peer_keys": peer_keys})
            errors = {}
            for attempt in (1, 2):
                request = request.model_copy(update={"message_id": uid(), "errors": errors,
                                                     "traceparent": context_headers().get("traceparent", "")})
                await emit("phase", {"year": state.year, "round": request.round,
                                     "phase": request.phase, "attempt": attempt,
                                     "request_id": request.message_id, "brief": request.model_dump()})
                response = None
                try:
                    response = await asyncio.wait_for(transport_ask(request), timeout=120)
                    ballot = validate_reply(response, request)
                    await emit("receipt", {"year": state.year, "responses": [ballot.model_dump()]})
                    accepted[request.speaker] = ballot
                    return ballot
                except Exception as exc:  # noqa: BLE001 - retry transport/model/validation failures
                    from sumer.protocol import Delivery
                    reply = response.reply if isinstance(response, Delivery) else response
                    errors = {request.speaker: f"{type(exc).__name__}: {exc}"}
                    await emit("validation_error", {"year": state.year, "round": request.round,
                        "phase": request.phase, "peer": request.speaker, "attempt": attempt,
                        "request_id": request.message_id, "message": errors[request.speaker],
                        "responses": [reply.model_dump()] if isinstance(reply, Ballot) else []})
            raise TechnicalFailure(f"{request.speaker} failed after one retry: {errors}")

    beliefs = Beliefs()
    news: list[dict] = []
    table = Table(state)
    opening: dict[str, Plan] = {}
    after_news: dict[str, Plan] = {}
    injected_news = injected_news if injected_news is not None else []
    earlier: list[dict] = []
    last_turn: dict[str, dict] = {}
    n_turns = 0

    async def take_news(rnd: int, items: list[dict]) -> bool:
        nonlocal beliefs
        for n in items:
            news.append(n)
            beliefs = apply_report(beliefs, n)
            await emit("news", {"year": state.year, "round": rnd, **n})
        return bool(items)

    def finish(outcome, pid, rounds, dissent, reason, runoff=None) -> YearResult:
        result = _finish(state, beliefs, outcome, table.plans[pid], rounds, dissent, reason, news,
                         opening, after_news, runoff, n_turns)
        result.certificate = {"candidate_id": pid, "ballots": [accepted[a].model_dump() for a in ADVISOR_IDS]}
        return result

    rnd = 0
    for rnd in range(max_rounds):
        heard = await take_news(rnd, [scheduled_news] if rnd == 1 and scheduled_news else [])
        order = speaking_order(state.year, rnd)
        await emit("round", {"year": state.year, "round": rnd, "phase": "discuss",
                             "max_rounds": max_rounds, "order": order})
        this_round: list[dict] = []
        changed = False
        for i, who in enumerate(order):
            if injected_news:
                fresh = list(injected_news)
                injected_news.clear()
                heard |= await take_news(rnd, fresh)
            brief = Brief(
                seed=seed, year=state.year, round=rnd, max_rounds=max_rounds,
                state=state.public(), beliefs=beliefs.__dict__, speaker=who, phase="discuss",
                turn=i, order=order, news=news, table=table.snapshot(),
                turns_this_round=this_round, earlier_turns=earlier, history=history,
            )
            ballot = await ask(brief)
            if not _valid(ballot, brief):
                await emit("wire", {"year": state.year, "round": rnd, "advisor": who,
                                    "rejected": True,
                                    "note": "ballot missing or failed its signature check"})
                continue
            allowed = set(table.plans)
            prev = table.current.get(who)
            pid, moved = table.place(who, ballot, state.year, rnd)
            table.vote(who, ballot, allowed)
            await emit("candidates", {"year": state.year, "candidates": [t.model_dump() for t in table.plans.values()]})
            changed |= moved
            n_turns += 1
            t = table.plans[pid]
            turn = {
                "advisor": who, "round": rnd, "turn": i, "speech": ballot.speech,
                "plan_id": pid, "proposal": t.plan.model_dump(), "plain": t.plain,
                "backed": t.author != who, "new_plan": pid != prev and t.author == who,
                "accept": sorted(table.accepts[who]), "veto": sorted(table.vetoes[who]),
                "addressed": ballot.addressed,
            }
            this_round.append(turn)
            last_turn[who] = {**turn, "thought": ballot.trace.get("thought", "")}
            await emit("turn", {
                "year": state.year, "phase": "discuss", **turn, "runoff_vote": None,
                "thought": ballot.trace.get("thought", ""), "trace": ballot.trace,
                "source": ballot.source, "support": table.rows(),
            })

            # Stop rule 1: everyone accepts one plan.
            for row in table.rows():
                if len(row["accept"]) == N_ADVISORS:
                    await _tally(emit, state.year, rnd, table, partial=i < len(order) - 1)
                    return finish("consensus", row["id"], rnd + 1, [],
                                  f"All {N_ADVISORS} advisors accepted.")
            # Stop rule 2: a strong majority, once round 2 is complete.
            if rnd > STRONG_MAJORITY_FROM_ROUND or (
                rnd == STRONG_MAJORITY_FROM_ROUND and i == len(order) - 1
            ):
                strong = [r for r in table.ranked()
                          if len(r["accept"]) >= STRONG_MAJORITY and not r["veto"]]
                if strong:
                    best = strong[0]
                    await _tally(emit, state.year, rnd, table, partial=i < len(order) - 1)
                    dissent = [a for a in ADVISOR_IDS if a not in best["accept"]]
                    return finish(
                        "majority", best["id"], rnd + 1, dissent,
                        f"Strong majority: {len(best['accept'])} of {N_ADVISORS} accepted "
                        f"and nobody vetoed. Dissent recorded: {_names(dissent)}.",
                    )

        await _tally(emit, state.year, rnd, table)
        plans = {a: table.plans[p].plan for a, p in table.current.items()}
        if rnd == 0:
            opening = dict(plans)
        if news and not after_news and rnd >= 1:
            after_news = dict(plans)
        earlier += this_round
        if rnd >= 1 and not changed and not heard:
            break  # stalemate: nobody moved and nothing new was heard

    # ---- Final rule ----
    rounds = rnd + 1
    ranked = table.ranked()
    best = ranked[0] if ranked else None
    second = ranked[1] if len(ranked) > 1 else None
    tie = bool(best and second and len(second["accept"]) == len(best["accept"]))
    if best and len(best["accept"]) >= MAJORITY and not best["veto"] and not tie:
        dissent = [a for a in ADVISOR_IDS if a not in best["accept"]]
        return finish(
            "majority", best["id"], rounds, dissent,
            f"{len(best['accept'])} of {N_ADVISORS} accepted and nobody vetoed. "
            f"Dissent recorded: {_names(dissent)}.",
        )

    if best and second and len(best["accept"]) >= RUNOFF_FLOOR:
        if best["veto"] and second["veto"]:
            reason = (f"Both leading plans cross a red line (for {_names(best['veto'])}; "
                      f"for {_names(second['veto'])}). A vote must not override one.")
        else:
            trigger = ("three" if len(best["accept"]) == RUNOFF_FLOOR
                       else "tie" if tie else "veto")
            res = await _runoff(seed, state, beliefs, ask, emit, table, best, second, trigger,
                                rounds, max_rounds, news, history, last_turn)
            n_turns += len(res["votes"])
            if res["winner"]:
                losers = [a for a, v in res["votes"].items() if v != res["winner"]]
                return finish(
                    "runoff", res["winner"], rounds, losers,
                    f"Runoff: {res['count'][res['winner']]} of {N_ADVISORS} voted for "
                    f"{short(table.plans[res['winner']].author)}'s plan. "
                    f"Dissent recorded: {_names(losers)}.",
                    runoff=res,
                )
            reason = res["reason"]
    elif best and best["veto"]:
        reason = (f"The leading plan crosses a red line for {_names(best['veto'])} and there is "
                  "no second plan to vote on.")
    elif best:
        reason = f"Deadlock: the best plan has only {len(best['accept'])} of {N_ADVISORS} accepts."
    else:
        reason = "Deadlock: no plan was tabled."
    result = await _escalate(state, beliefs, emit, ask_human, ranked, reason, rounds, news,
                             opening, after_news, last_turn, n_turns, run_id=run_id)
    result.human_resolution = {"id": uid(), "run_id": run_id, "year": state.year,
                               "state_hash": state_hash(state), "candidate_id": result.adopted_id,
                               "source": "human", "human_assisted": True, "reason": reason}
    return result


async def _runoff(seed, state, beliefs, ask, emit, table: Table, a_row, b_row, trigger,
                  rounds, max_rounds, news, history, last_turn) -> dict:
    """Head to head between the top two plans. Every advisor votes once, in turn."""
    a, b = table.plans[a_row["id"]], table.plans[b_row["id"]]
    why = {
        "tie": "The top two plans are tied",
        "three": f"The best plan has only {RUNOFF_FLOOR} of {N_ADVISORS} accepts"
                 + (" and the top two are tied" if len(b_row["accept"]) == RUNOFF_FLOOR else ""),
        "veto": "The leading plan crosses a red line",
    }[trigger]
    pair = [a.model_copy(update={"accept": a_row["accept"], "veto": a_row["veto"]}),
            b.model_copy(update={"accept": b_row["accept"], "veto": b_row["veto"]})]

    def side(t: TabledProposal, row: dict) -> dict:
        return {"id": t.id, "author": t.author, "plain": t.plain, "plan": t.plan.model_dump(),
                "accept": row["accept"], "veto": row["veto"]}

    await emit("runoff", {
        "year": state.year, "trigger": trigger, "a": side(a, a_row), "b": side(b, b_row),
        "reason": f"{why}, so the two leading plans go to a runoff.",
    })
    order = speaking_order(state.year, rounds)
    await emit("round", {"year": state.year, "round": rounds, "phase": "runoff",
                         "max_rounds": max_rounds, "order": order})
    votes: dict[str, str] = {}
    vetoes: dict[str, list[str]] = {}
    said: list[dict] = []
    for i, who in enumerate(order):
        brief = Brief(
            seed=seed, year=state.year, round=rounds, max_rounds=max_rounds,
            state=state.public(), beliefs=beliefs.__dict__, speaker=who, phase="runoff",
            turn=i, order=order, news=news, table=pair, runoff=pair, turns_this_round=said,
            history=history,
        )
        ballot = await ask(brief)
        if not _valid(ballot, brief) or ballot.runoff_vote not in (a.id, b.id):
            await emit("wire", {"year": state.year, "round": rounds, "advisor": who,
                                "rejected": True, "note": "runoff ballot missing or invalid"})
            continue
        votes[who] = ballot.runoff_vote
        vetoes[who] = [v for v in ballot.veto if v in (a.id, b.id)]
        t = table.plans[ballot.runoff_vote]
        turn = {"advisor": who, "round": rounds, "turn": i, "speech": ballot.speech,
                "runoff_vote": ballot.runoff_vote, "plain": t.plain, "veto": vetoes[who],
                "addressed": ballot.addressed}
        said.append(turn)
        last_turn[who] = {**last_turn.get(who, {}), "speech": ballot.speech,
                          "thought": ballot.trace.get("thought", "")}
        await emit("turn", {
            "year": state.year, "phase": "runoff", **turn, "plan_id": None,
            "proposal": t.plan.model_dump(), "accept": [ballot.runoff_vote],
            "thought": ballot.trace.get("thought", ""), "trace": ballot.trace,
            "source": ballot.source,
        })
    count = {a.id: 0, b.id: 0}
    for v in votes.values():
        count[v] += 1
    lead = max(count, key=lambda k: (count[k], k == a.id))
    vetoed_by = [w for w, vs in vetoes.items() if lead in vs]
    winner, reason = None, ""
    if count[lead] >= MAJORITY and not vetoed_by:
        winner = lead
    elif vetoed_by:
        reason = (f"The runoff favored {short(table.plans[lead].author)}'s plan, but it crosses "
                  f"a red line for {_names(vetoed_by)}. A vote must not override it.")
    else:
        reason = f"The runoff produced no plan with {MAJORITY} of {N_ADVISORS} votes."
    res = {"a": a.id, "b": b.id, "trigger": trigger, "votes": votes, "count": count,
           "winner": winner, "reason": reason, "vetoes": vetoes,
           "accepts_before": {a.id: len(a_row["accept"]), b.id: len(b_row["accept"])}}
    await emit("runoff_result", {"year": state.year, "votes": votes, "winner": winner,
                                 "count": count, "reason": reason, "trigger": trigger})
    return res


async def _escalate(state, beliefs, emit, ask_human, ranked, reason, rounds, news, opening,
                    after_news, last_turn, n_turns, run_id="") -> YearResult:
    with span("council.human_resolution", run_id=run_id, year=state.year):
        safe = safe_plan(state)
        options = [
            {"id": r["id"], "author": r["author"], "plan": r["plan"], "plain": r["plain"],
             "accept": r["accept"], "veto": r["veto"]}
            for r in ranked[:2]
        ] + [{"id": "safe", "author": "fallback", "plan": safe.__dict__,
              "plain": plain(safe, state), "accept": [], "veto": [],
              "note": "Pre-committed safe fallback: feed everyone (selling up to 20% of land if "
                      "needed), then sow the most we can."}]
        positions = [
            {"advisor": a, "proposal": t.get("proposal"), "plain": t.get("plain", ""),
             "accept": t.get("accept", []), "veto": t.get("veto", []),
             "speech": t.get("speech", ""), "thought": t.get("thought", "")}
            for a, t in sorted(last_turn.items(), key=lambda kv: ADVISOR_IDS.index(kv[0]))
        ]
        await emit("escalate",
                   {"year": state.year, "reason": reason, "options": options, "positions": positions})
        choice = await ask_human(options, reason) if ask_human else None
        if choice is None:
            raise Deadlock(reason)
        chosen = next((o for o in options if o["id"] == choice), None)
        if chosen is None:
            raise TechnicalFailure("Human choice is not a latest valid option.")
        author = chosen["author"] if choice else "fallback"
        t = TabledProposal(id=chosen["id"], author=author, plan=Plan(**chosen["plan"]),
                           plain=chosen["plain"])
        res = _finish(state, beliefs, "escalated", t, rounds, [], reason, news, opening, after_news,
                      None, n_turns)
        res.human_choice = choice
        return res


async def _tally(emit, year, rnd, table: Table, partial: bool = False) -> None:
    await emit("tally", {"year": year, "round": rnd, "phase": "discuss", "partial": partial,
                         "rows": table.rows()})


def _names(ids: list[str]) -> str:
    return ", ".join(short(a) for a in ids) or "nobody"


def _valid(b: Ballot | None, brief: Brief) -> bool:
    ok = (
        b is not None
        and b.advisor == brief.speaker
        and b.year == brief.year
        and b.round == brief.round
        and b.phase == brief.phase
        and verify(b)
    )
    if not ok:
        log.warning("dropping missing or unverified ballot for %s", brief.speaker)
    return ok


def _finish(state, beliefs, outcome, t: TabledProposal, rounds, dissent, reason, news, opening,
            after_news, runoff, n_turns) -> YearResult:
    d = Decision(t.plan.land_delta, t.plan.feed, t.plan.plant)
    sc = Scorer(state, beliefs, extra=[d])
    return YearResult(
        year=state.year,
        outcome=outcome,
        plan=d,
        adopted_id=t.id,
        adopted_author=t.author,
        plain=t.plain or plain(d, state),
        rounds=rounds,
        dissent=dissent,
        reason=reason,
        news=news,
        opening=opening,
        after_news=after_news,
        satisfaction={a: round(sc.norm(a, d), 3) for a in ADVISOR_IDS},
        runoff=runoff,
        turns=n_turns,
    )
