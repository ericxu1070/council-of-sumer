"""How the council decides one year, with no king.

Protocol (public, so every advisor can check the clerk's tally):
  Round 0      every advisor tables an opening proposal.
  Round 1..N-1 every advisor votes accept / veto on the tabled proposals and
               tables a revised one. News that arrives mid-debate is shown
               from round 1 on, so its effect on proposals is visible.
  Stop rules, checked after each vote:
    CONSENSUS  a tabled plan is accepted by all four advisors.  -> adopt it
    stalemate  nobody changed their proposal and no news arrived. -> go to final
  Final rule (last round or stalemate):
    MAJORITY   best plan has 3 of 4 accepts and no red-line veto.
               -> adopt it and RECORD the dissent (we keep the disagreement)
    ESCALATE   otherwise (deadlock, or a red-line veto on the leading plan).
               -> ask a human. If no human answers in time, use the
                  pre-committed safe plan (no trade, feed all, plant max).

The clerk relays messages and applies this rule. It never ranks plans by its
own judgment.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sumer.advisors import ADVISOR_IDS, Beliefs, Scorer, apply_report, safe_plan
from sumer.game import Decision, State
from sumer.protocol import Ballot, Brief, Plan, TabledProposal, proposal_id, verify

log = logging.getLogger(__name__)

Emit = Callable[[str, dict], Awaitable[None]]
Gather = Callable[[Brief], Awaitable[list[Ballot]]]
# Called on escalation with (options, reason); returns the chosen option id or None
AskHuman = Callable[[list[dict], str], Awaitable[str | None]]

SUPERMAJORITY = 3


@dataclass
class YearResult:
    year: int
    outcome: str  # "consensus" | "majority" | "escalated"
    plan: Decision
    adopted_id: str
    rounds: int
    dissent: list[str] = field(default_factory=list)
    reason: str = ""
    news: list[dict] = field(default_factory=list)
    opening: dict[str, Plan] = field(default_factory=dict)
    after_news: dict[str, Plan] = field(default_factory=dict)
    satisfaction: dict[str, float] = field(default_factory=dict)
    human_choice: str | None = None

    def summary(self) -> dict:
        return {
            "year": self.year,
            "outcome": self.outcome,
            "plan": self.plan.__dict__,
            "adopted": self.adopted_id,
            "rounds": self.rounds,
            "dissent": self.dissent,
            "reason": self.reason,
            "news": [n["text"] for n in self.news],
        }


async def _noop_emit(kind: str, data: dict) -> None:
    return None


def tally(table: list[TabledProposal], ballots: list[Ballot]) -> list[dict]:
    rows = []
    for t in table:
        acc = sorted(b.advisor for b in ballots if t.id in b.accept)
        vet = sorted(b.advisor for b in ballots if t.id in b.veto)
        rows.append({"id": t.id, "author": t.author, "accept": acc, "veto": vet})
    return rows


async def deliberate_year(
    *,
    seed: int,
    state: State,
    gather: Gather,
    history: list[dict],
    max_rounds: int,
    scheduled_news: dict | None,
    injected_news: list[dict] | None = None,
    ask_human: AskHuman | None = None,
    emit: Emit = _noop_emit,
) -> YearResult:
    beliefs = Beliefs()
    news: list[dict] = []
    table: list[TabledProposal] = []
    prev_plans: dict[str, Plan] = {}
    opening: dict[str, Plan] = {}
    after_news: dict[str, Plan] = {}
    injected_news = injected_news if injected_news is not None else []
    rows: list[dict] = []
    last_ballots: list[Ballot] = []

    for rnd in range(max_rounds):
        fresh = []
        if rnd == 1 and scheduled_news:
            fresh.append(scheduled_news)
        while injected_news:
            fresh.append(injected_news.pop(0))
        for n in fresh:
            news.append(n)
            beliefs = apply_report(beliefs, n)
            await emit("news", {"year": state.year, "round": rnd, **n})

        brief = Brief(
            seed=seed,
            year=state.year,
            round=rnd,
            max_rounds=max_rounds,
            state=state.public(),
            beliefs=beliefs.__dict__,
            news=news,
            table=table,
            history=history,
        )
        await emit("round", {"year": state.year, "round": rnd, "table": [t.id for t in table]})
        received = await gather(brief)
        ballots = [b for b in received if _valid(b, brief)]
        ballots.sort(key=lambda b: ADVISOR_IDS.index(b.advisor))
        last_ballots = ballots
        rows = tally(table, ballots)
        await emit(
            "ballots",
            {
                "year": state.year,
                "round": rnd,
                "ballots": [b.model_dump(exclude={"sig"}) for b in ballots],
                "tally": rows,
                "verified": len(ballots),
                "rejected": len(received) - len(ballots),
                "missing": [a for a in ADVISOR_IDS if a not in {b.advisor for b in ballots}],
            },
        )

        # Stop rule 1: unanimous acceptance of a tabled plan.
        for row in rows:
            if len(row["accept"]) == len(ADVISOR_IDS) and not row["veto"]:
                t = next(t for t in table if t.id == row["id"])
                return _finish(
                    state, beliefs, "consensus", t, rnd + 1, [], "All four advisors accepted.",
                    news, opening, after_news,
                )

        plans = {b.advisor: b.proposal for b in ballots}
        if rnd == 0:
            opening = dict(plans)
        if news and not after_news and rnd >= 1:
            after_news = dict(plans)

        stalemate = rnd >= 2 and plans == prev_plans and not fresh
        if rnd == max_rounds - 1 or stalemate:
            break
        prev_plans = plans
        table = [
            TabledProposal(
                id=proposal_id(b.advisor, state.year, rnd),
                author=b.advisor,
                plan=b.proposal,
                speech=b.speech,
            )
            for b in ballots
        ]

    # Final rule.
    ranked = sorted(rows, key=lambda r: (-len(r["accept"]), len(r["veto"]), r["id"]))
    if ranked and len(ranked[0]["accept"]) >= SUPERMAJORITY and not ranked[0]["veto"]:
        best = ranked[0]
        t = next(t for t in table if t.id == best["id"])
        dissent = [a for a in ADVISOR_IDS if a not in best["accept"]]
        return _finish(
            state, beliefs, "majority", t, rnd + 1, dissent,
            f"{len(best['accept'])} of 4 accepted. Dissent recorded: {', '.join(dissent)}.",
            news, opening, after_news,
        )

    if ranked and ranked[0]["veto"]:
        reason = (
            f"Leading plan {ranked[0]['id']} crosses a red line for "
            f"{', '.join(ranked[0]['veto'])}. A majority must not override it."
        )
    else:
        reason = "Deadlock: no plan reached 3 of 4 accepts."
    safe = safe_plan(state)
    options = [
        {"id": r["id"], "plan": next(t for t in table if t.id == r["id"]).plan.model_dump(),
         "accept": r["accept"], "veto": r["veto"]}
        for r in ranked[:2]
    ] + [{"id": "safe", "plan": safe.__dict__, "accept": [], "veto": [],
          "note": "Pre-committed safe fallback: feed everyone (selling up to 20% of land if needed), then sow the most we can."}]
    positions = [
        {
            "advisor": b.advisor,
            "proposal": b.proposal.model_dump(),
            "accept": b.accept,
            "veto": b.veto,
            "speech": b.speech,
            "thought": b.trace.get("thought", ""),
        }
        for b in last_ballots
    ]
    await emit(
        "escalate",
        {"year": state.year, "reason": reason, "options": options, "positions": positions},
    )
    choice = await ask_human(options, reason) if ask_human else None
    chosen = next((o for o in options if o["id"] == choice), options[-1])
    t = TabledProposal(id=chosen["id"], author="human" if choice else "fallback",
                       plan=Plan(**chosen["plan"]))
    res = _finish(state, beliefs, "escalated", t, rnd + 1, [], reason, news, opening, after_news)
    res.human_choice = choice
    return res


def _valid(b: Ballot, brief: Brief) -> bool:
    ok = b.advisor in ADVISOR_IDS and b.year == brief.year and b.round == brief.round and verify(b)
    if not ok:
        log.warning("dropping unverified ballot from %s", b.advisor)
    return ok


def _finish(state, beliefs, outcome, t, rounds, dissent, reason, news, opening, after_news):
    d = Decision(t.plan.land_delta, t.plan.feed, t.plan.plant)
    sc = Scorer(state, beliefs, extra=[d])
    return YearResult(
        year=state.year,
        outcome=outcome,
        plan=d,
        adopted_id=t.id,
        rounds=rounds,
        dissent=dissent,
        reason=reason,
        news=news,
        opening=opening,
        after_news=after_news,
        satisfaction={a: round(sc.norm(a, d), 3) for a in ADVISOR_IDS},
    )
