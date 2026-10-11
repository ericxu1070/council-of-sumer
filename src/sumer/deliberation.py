"""Sequential council discussion with consensus, majority, runoff, or human resolution.

A run's advisors speak in a fixed order that rotates each round and year.
Consensus needs every seat. A strong majority (at least two thirds, rounded
up, and at least a strict majority) may adopt a plan after round 2. At the
negotiation limit or stalemate, a unique strict majority wins. A tie or
support one below a majority sends the leading two plans to a runoff,
which needs a strict majority. Otherwise a human resolves deadlock.
For the default five seats, these thresholds are 5 / 4 / 3 / 2.
Advisors choose proposals and accept votes. There are no vetoes.
The clerk counts votes without judging proposals.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from math import ceil

from sumer.advisors import (
    ADVISOR_IDS,
    N_ADVISORS,
    Advisor,
    Beliefs,
    Scorer,
    apply_report,
    plain,
    resolve_council,
    safe_plan,
    short,
)
from sumer.game import Decision, State, legalize
from sumer.observability import context_headers, span
from sumer.protocol import (
    Ballot,
    Brief,
    Delivery,
    Plan,
    TabledProposal,
    proposal_id,
    state_hash,
    uid,
    validate_reply,
)

log = logging.getLogger(__name__)

Emit = Callable[[str, dict], Awaitable[None]]
# Sends a Brief to its one speaker and returns that advisor's Ballot (None if it never answered)
Ask = Callable[[Brief], Awaitable[Ballot | Delivery | None]]
# Called on escalation with (options, reason); returns the chosen option id or None
AskHuman = Callable[[list[dict], str], Awaitable[str | None]]
AfterVote = Callable[[int, str, list[dict]], Awaitable[dict | None]]

MAJORITY = N_ADVISORS // 2 + 1  # default council, adopted by the final rule
STRONG_MAJORITY = max(MAJORITY, ceil(2 * N_ADVISORS / 3))
RUNOFF_FLOOR = max(1, MAJORITY - 1)
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


def voting_rules(count: int) -> dict[str, int]:
    """Public stop thresholds for a nonempty council."""
    if count < 1:
        raise ValueError("A council must have at least one advisor.")
    majority = count // 2 + 1
    return {"majority": majority, "strong_majority": max(majority, ceil(2 * count / 3)),
            "runoff_floor": max(1, majority - 1)}


def speaking_order(year: int, rnd: int, advisor_ids: list[str] | None = None) -> list[str]:
    """Fixed seating; the first seat moves one place every round and every year."""
    ids = list(ADVISOR_IDS if advisor_ids is None else advisor_ids)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("A council requires distinct advisor seats.")
    k = (year - 1 + rnd) % len(ids)
    return ids[k:] + ids[:k]


class Table:
    """The plans on the table, who backs each, and every advisor's standing votes."""

    def __init__(self, state: State, advisor_ids: list[str] | None = None):
        self.state = state
        self.advisor_ids = list(ADVISOR_IDS if advisor_ids is None else advisor_ids)
        speaking_order(1, 0, self.advisor_ids)
        self.plans: dict[str, TabledProposal] = {}
        self.current: dict[str, str] = {}  # advisor -> id of the plan it proposes or backs
        self.accepts: dict[str, set[str]] = {}
        self.cast: set[tuple[int, int, str]] = set()

    def begin_round(self) -> None:
        self.accepts.clear()
        self.plans = {pid: plan for pid, plan in self.plans.items()
                      if pid in self.current.values()}

    def support(self, pid: str) -> list[str]:
        acc = {a for a in self.advisor_ids if pid in self.accepts.get(a, ())}
        return sorted(acc, key=self.advisor_ids.index)

    def snapshot(self) -> list[TabledProposal]:
        out = []
        for t in self.plans.values():
            acc = self.support(t.id)
            backers = [a for a in self.advisor_ids if self.current.get(a) == t.id]
            out.append(t.model_copy(update={"accept": acc, "backers": backers}))
        return out

    def rows(self) -> list[dict]:
        return [
            {"id": t.id, "author": t.author, "plain": t.plain, "plan": t.plan.model_dump(),
             "accept": t.accept, "backers": t.backers}
            for t in self.snapshot()
        ]

    def ranked(self) -> list[dict]:
        order = list(self.plans)
        return sorted(self.rows(), key=lambda r: (-len(r["accept"]),
                                                  order.index(r["id"])))

    def place(self, who: str, b: Ballot, year: int, rnd: int) -> tuple[str, bool]:
        """Table or back the speaker's proposal. Returns (plan id, changed)."""
        self._check_voter(who)
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
        if (prev and prev != pid and prev not in self.current.values()
                and prev not in b.accept and not self.support(prev)):
            del self.plans[prev]  # nobody proposes, backs, or approves it any more
        return pid, pid != prev

    def vote(self, who: str, b: Ballot, allowed: set[str]) -> None:
        self._check_voter(who)
        key = (b.year, b.round, who)
        if key in self.cast:
            raise ValueError("Each agent may submit only one ballot per round.")
        self.cast.add(key)
        self.accepts[who] = (set(b.accept) & allowed) | {self.current[who]}


    def _check_voter(self, who: str) -> None:
        if who not in self.advisor_ids:
            raise ValueError("Voter is not a member of this council.")


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
    advisors: dict[str, Advisor] | None = None,
    after_vote: AfterVote | None = None,
) -> YearResult:
    advisors = resolve_council() if advisors is None else dict(advisors)
    advisor_ids = list(advisors)
    rules = voting_rules(len(advisor_ids))
    public_advisors = [a.public() for a in advisors.values()]
    n_advisors = len(advisor_ids)
    majority, strong_majority, runoff_floor = (
        rules[k] for k in ("majority", "strong_majority", "runoff_floor"))
    run_id = run_id or uid()
    transport_ask = ask
    accepted: dict[str, Ballot] = {}

    async def ask(brief):
        with span("council.turn", run_id=run_id, year=state.year, phase=brief.phase, peer=brief.speaker, cycle=brief.round):
            request = brief.model_copy(update={"run_id": run_id})
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
                    reply = response.reply if isinstance(response, Delivery) else response
                    errors = {request.speaker: f"{type(exc).__name__}: {exc}"}
                    await emit("validation_error", {"year": state.year, "round": request.round,
                        "phase": request.phase, "peer": request.speaker, "attempt": attempt,
                        "request_id": request.message_id, "message": errors[request.speaker],
                        "responses": [reply.model_dump()] if isinstance(reply, Ballot) else []})
            accepted.pop(request.speaker, None)
            await emit("agent_skipped", {"year": state.year, "round": request.round,
                "phase": request.phase, "advisor": request.speaker, "turn": request.turn,
                "request_id": request.message_id, "attempts": 2,
                "message": errors[request.speaker]})
            return None

    beliefs = Beliefs()
    news: list[dict] = []
    table = Table(state, advisor_ids)
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
        final_beliefs = Beliefs()
        for report in news:
            final_beliefs = apply_report(final_beliefs, report)
        result = _finish(state, final_beliefs, outcome, table.plans[pid], rounds, dissent, reason, news,
                         opening, after_news, runoff, n_turns, advisors)
        result.certificate = {"candidate_id": pid,
                              "ballots": [accepted[a].model_dump() for a in advisor_ids
                                          if a in accepted]}
        return result

    rnd = 0
    revote = False
    while rnd < max_rounds or revote:
        table.begin_round()
        heard = revote
        heard |= await take_news(rnd, [scheduled_news] if rnd == 1 and scheduled_news else [])
        order = speaking_order(state.year, rnd, advisor_ids)
        await emit("round", {"year": state.year, "round": rnd, "phase": "discuss",
                             "max_rounds": max_rounds, "order": order, "revote": revote})
        this_round: list[dict] = []
        changed = False
        for i, who in enumerate(order):
            if injected_news:
                fresh = list(injected_news)
                injected_news.clear()
                heard |= await take_news(rnd, fresh)
            brief = Brief(
                seed=seed, year=state.year, round=rnd, max_rounds=max_rounds,
                state=state.public(), beliefs=beliefs.__dict__, advisors=public_advisors, speaker=who, phase="discuss",
                turn=i, order=order, news=news, table=table.snapshot(),
                turns_this_round=this_round, earlier_turns=earlier, history=history,
            )
            ballot = await ask(brief)
            if not _valid(ballot, brief):
                await emit("wire", {"year": state.year, "round": rnd, "advisor": who,
                                    "rejected": True,
                                    "note": "ballot missing or failed turn validation"})
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
                "accept": sorted(table.accepts[who]),
                "addressed": ballot.addressed,
            }
            this_round.append(turn)
            last_turn[who] = {**turn, "thought": ballot.trace.get("thought", "")}
            await emit("turn", {
                "year": state.year, "phase": "discuss", **turn, "runoff_vote": None,
                "thought": ballot.trace.get("thought", ""), "trace": ballot.trace,
                "source": ballot.source, "support": table.rows(),
            })

            if after_vote is None:
                # Stop rule 1: everyone accepts one plan.
                for row in table.rows():
                    if len(row["accept"]) == n_advisors:
                        await _tally(emit, state.year, rnd, table, partial=i < len(order) - 1)
                        return finish("consensus", row["id"], rnd + 1, [],
                                      f"All {n_advisors} advisors accepted.")
                # Stop rule 2: a strong majority, once round 2 is complete.
                if rnd > STRONG_MAJORITY_FROM_ROUND or (
                    rnd == STRONG_MAJORITY_FROM_ROUND and i == len(order) - 1
                ):
                    strong = [r for r in table.ranked()
                              if len(r["accept"]) >= strong_majority]
                    if strong:
                        best = strong[0]
                        await _tally(emit, state.year, rnd, table, partial=i < len(order) - 1)
                        dissent = [a for a in advisor_ids if a not in best["accept"]]
                        return finish(
                            "majority", best["id"], rnd + 1, dissent,
                            f"Strong majority: {len(best['accept'])} of {n_advisors} accepted "
                            f"with dissent recorded: {_names(dissent, advisors)}.",
                        )
        await _tally(emit, state.year, rnd, table)
        plans = {a: table.plans[p].plan for a, p in table.current.items()}
        if rnd == 0:
            opening = dict(plans)
        if news and not after_news and rnd >= 1:
            after_news = dict(plans)
        earlier += this_round
        if after_vote is not None:
            report = await after_vote(rnd, "discuss", table.rows())
            if report is not None:
                await take_news(rnd + 1, [report])
                rnd += 1
                revote = True
                continue
            for row in table.rows():
                if len(row["accept"]) == n_advisors:
                    return finish("consensus", row["id"], rnd + 1, [],
                                  f"All {n_advisors} advisors accepted.")
        strong = [r for r in table.ranked() if len(r["accept"]) >= strong_majority]
        if rnd >= STRONG_MAJORITY_FROM_ROUND and strong:
            best = strong[0]
            return finish("majority", best["id"], rnd + 1,
                          [a for a in advisor_ids if a not in best["accept"]],
                          f"Strong majority: {len(best['accept'])} of {n_advisors} accepted.")
        if rnd >= max_rounds - 1 or rnd >= 1 and not changed and not heard:
            break  # negotiation ended; a scout can always request an extra re-vote
        rnd += 1
        revote = False

    # ---- Final rule ----
    rounds = rnd + 1
    ranked = table.ranked()
    best = ranked[0] if ranked else None
    second = ranked[1] if len(ranked) > 1 else None
    tie = bool(best and second and len(second["accept"]) == len(best["accept"]))
    if best and len(best["accept"]) >= majority and not tie:
        dissent = [a for a in advisor_ids if a not in best["accept"]]
        return finish(
            "majority", best["id"], rounds, dissent,
            f"{len(best['accept'])} of {n_advisors} accepted. "
            f"Dissent recorded: {_names(dissent, advisors)}.",
        )

    if best and second and len(best["accept"]) >= runoff_floor:
        trigger = (("three" if n_advisors == 7 else "floor")
                   if len(best["accept"]) == runoff_floor else "tie")
        res = await _runoff(seed, state, beliefs, ask, emit, table, best, second, trigger,
                            rounds, max_rounds, news, history, last_turn, advisors, after_vote)
        n_turns += res.get("turns", len(res["votes"]))
        if res["winner"]:
            losers = [a for a in advisor_ids if res["votes"].get(a) != res["winner"]]
            return finish(
                "runoff", res["winner"], rounds, losers,
                f"Runoff: {res['count'][res['winner']]} of {n_advisors} voted for "
                f"{short(table.plans[res['winner']].author, advisors)}'s plan. "
                f"Dissent recorded: {_names(losers, advisors)}.",
                runoff=res,
            )
        reason = res["reason"]
    elif best:
        reason = f"Deadlock: the best plan has only {len(best['accept'])} of {n_advisors} accepts."
    else:
        reason = "Deadlock: no plan was tabled."
    result = await _escalate(state, beliefs, emit, ask_human, ranked, reason, rounds, news,
                             opening, after_news, last_turn, n_turns, run_id=run_id, advisors=advisors)
    result.human_resolution = {"id": uid(), "run_id": run_id, "year": state.year,
                               "state_hash": state_hash(state), "candidate_id": result.adopted_id,
                               "source": "human", "human_assisted": True, "reason": reason}
    return result


async def _runoff(seed, state, beliefs, ask, emit, table: Table, a_row, b_row, trigger,
                  rounds, max_rounds, news, history, last_turn, advisors, after_vote=None) -> dict:
    """Head to head between the top two plans. Every advisor votes once, in turn."""
    advisor_ids = list(advisors)
    n_advisors = len(advisor_ids)
    rules = voting_rules(n_advisors)
    majority, runoff_floor = rules["majority"], rules["runoff_floor"]
    a, b = table.plans[a_row["id"]], table.plans[b_row["id"]]
    why = {
        "tie": "The top two plans are tied",
        "three": f"The best plan has only {runoff_floor} of {n_advisors} accepts"
                 + (" and the top two are tied" if len(b_row["accept"]) == runoff_floor else ""),
    }["three" if trigger == "floor" else trigger]
    pair = [a.model_copy(update={"accept": a_row["accept"]}),
            b.model_copy(update={"accept": b_row["accept"]})]

    def side(t: TabledProposal, row: dict) -> dict:
        return {"id": t.id, "author": t.author, "plain": t.plain, "plan": t.plan.model_dump(),
                "accept": row["accept"]}

    await emit("runoff", {
        "year": state.year, "trigger": trigger, "a": side(a, a_row), "b": side(b, b_row),
        "reason": f"{why}, so the two leading plans go to a runoff.",
    })
    cycles = 0
    n_turns = 0
    while True:
        order = speaking_order(state.year, rounds, advisor_ids)
        await emit("round", {"year": state.year, "round": rounds, "phase": "runoff",
                             "max_rounds": max_rounds, "order": order, "revote": cycles > 0})
        votes: dict[str, str] = {}
        said: list[dict] = []
        for i, who in enumerate(order):
            brief = Brief(
                seed=seed, year=state.year, round=rounds, max_rounds=max_rounds,
                state=state.public(), beliefs=beliefs.__dict__, advisors=[a.public() for a in advisors.values()], speaker=who, phase="runoff",
                turn=i, order=order, news=news, table=pair, runoff=pair, turns_this_round=said,
                history=history,
            )
            ballot = await ask(brief)
            if not _valid(ballot, brief) or ballot.runoff_vote not in (a.id, b.id):
                await emit("wire", {"year": state.year, "round": rounds, "advisor": who,
                                    "rejected": True, "note": "runoff ballot missing or invalid"})
                continue
            votes[who] = ballot.runoff_vote
            n_turns += 1
            t = table.plans[ballot.runoff_vote]
            turn = {"advisor": who, "round": rounds, "turn": i, "speech": ballot.speech,
                    "runoff_vote": ballot.runoff_vote, "plain": t.plain,
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
        winner, reason = None, ""
        if count[lead] >= majority:
            winner = lead
        else:
            reason = f"The runoff produced no plan with {majority} of {n_advisors} votes."
        res = {"a": a.id, "b": b.id, "trigger": trigger, "votes": votes, "count": count,
               "winner": winner, "reason": reason,
               "turns": n_turns, "round": rounds,
               "accepts_before": {a.id: len(a_row["accept"]), b.id: len(b_row["accept"])}}
        await emit("runoff_result", {"year": state.year, "votes": votes, "winner": winner,
                                     "count": count, "reason": reason, "trigger": trigger})
        if after_vote is None:
            return res
        rows = [{"id": t.id, "author": t.author, "plain": t.plain,
                 "accept": [who for who, vote in votes.items() if vote == t.id]}
                for t in pair]
        report = await after_vote(rounds, "runoff", rows)
        if report is None:
            return res
        news.append(report)
        beliefs = apply_report(beliefs, report)
        await emit("news", {"year": state.year, "round": rounds + 1, **report})
        cycles += 1
        rounds += 1



async def _escalate(state, beliefs, emit, ask_human, ranked, reason, rounds, news, opening,
                    after_news, last_turn, n_turns, run_id="", advisors=None) -> YearResult:
    with span("council.human_resolution", run_id=run_id, year=state.year):
        safe = safe_plan(state)
        options = [
            {"id": r["id"], "author": r["author"], "plan": r["plan"], "plain": r["plain"],
             "accept": r["accept"]}
            for r in ranked[:2]
        ] + [{"id": "safe", "author": "fallback", "plan": safe.__dict__,
              "plain": plain(safe, state), "accept": [],
              "note": "Pre-committed safe fallback: feed everyone (selling up to 20% of land if "
                      "needed), then sow the most we can."}]
        positions = [
            {"advisor": a, "proposal": t.get("proposal"), "plain": t.get("plain", ""),
             "accept": t.get("accept", []),
             "speech": t.get("speech", ""), "thought": t.get("thought", "")}
            for a, t in sorted(last_turn.items(), key=lambda kv: list(advisors).index(kv[0]))
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
                      None, n_turns, advisors)
        res.human_choice = choice
        return res


async def _tally(emit, year, rnd, table: Table, partial: bool = False) -> None:
    await emit("tally", {"year": year, "round": rnd, "phase": "discuss", "partial": partial,
                         "rows": table.rows()})


def _names(ids: list[str], advisors=None) -> str:
    return ", ".join(short(a, advisors) for a in ids) or "nobody"


def _valid(b: Ballot | None, brief: Brief) -> bool:
    ok = (
        b is not None
        and b.advisor == brief.speaker
        and b.year == brief.year
        and b.round == brief.round
        and b.phase == brief.phase
    )
    if not ok:
        log.warning("dropping missing or unverified ballot for %s", brief.speaker)
    return ok


def _finish(state, beliefs, outcome, t: TabledProposal, rounds, dissent, reason, news, opening,
            after_news, runoff, n_turns, advisors) -> YearResult:
    d = Decision(t.plan.land_delta, t.plan.feed, t.plan.plant)
    sc = Scorer(state, beliefs, extra=[d], advisors=advisors)
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
        satisfaction={a: round(sc.norm(a, d), 3) for a in advisors if a in ADVISOR_IDS},
        runoff=runoff,
        turns=n_turns,
    )
