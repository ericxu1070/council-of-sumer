"""Recheck the recorded turn ledger and manifest roster and stop rules before advancing once."""
from __future__ import annotations

import json
from dataclasses import asdict

from sumer.advisors import resolve_council
from sumer.deliberation import (
    Table,
    YearResult,
    speaking_order,
    voting_rules,
)
from sumer.game import State, YearRolls, advance, legalize
from sumer.observability import context_headers, span
from sumer.protocol import Ballot, Brief, state_hash, validate_reply
from sumer.storage import Store


def commit(store: Store, run_id: str, state: State, result: YearResult,
           rolls: list[YearRolls]) -> tuple[State, dict]:
    with span("engine.commit", run_id=run_id, year=state.year, candidate_id=result.adopted_id):
        with span("certificate.verify", run_id=run_id, year=state.year, candidate_id=result.adopted_id):
            manifest = store.run(run_id)["manifest"]
            advisors = resolve_council(manifest.get("advisors"))
            advisor_ids = list(advisors)
            n_advisors = len(advisors)
            rules = voting_rules(n_advisors)
            if manifest.get("rules", rules) != rules:
                raise ValueError("Manifest voting rules do not match its council roster.")
            if manifest.get("advisor_ids", advisor_ids) != advisor_ids:
                raise ValueError("Manifest advisor ids do not match its council roster.")
            public_advisors = [a.public() for a in advisors.values()]
            legacy_roster = "advisor_ids" not in manifest and "rules" not in manifest
            majority, strong_majority, runoff_floor = (
                rules[k] for k in ("majority", "strong_majority", "runoff_floor"))
            if (result.certificate is None) == (result.human_resolution is None):
                raise ValueError("Exactly one certificate or human resolution is required.")
            events = store.events(run_id, year=state.year)
            for event in events:
                if event["kind"] != "phase":
                    continue
                brief = Brief.model_validate(event["data"]["brief"])
                if brief.advisors != public_advisors and not (legacy_roster and not brief.advisors):
                    raise ValueError("Recorded request changes the manifest council roster.")
                if brief.speaker not in advisors or not 0 <= brief.turn < n_advisors:
                    raise ValueError("Recorded request has an outsider voter or invalid turn.")
                if brief.max_rounds != manifest["max_rounds"]:
                    raise ValueError("Recorded request changes the manifest negotiation limit.")
            if result.human_resolution:
                resolution = result.human_resolution
                escalations = [e["data"] for e in events if e["kind"] == "escalate"]
                option = next((o for o in escalations[-1]["options"] if o["id"] == result.adopted_id), None) if escalations else None
                if option is None or option["plan"] != result.plan.as_dict():
                    raise ValueError("Human selection must identify a latest valid deadlock option.")
                if (resolution["run_id"], resolution["year"], resolution["state_hash"],
                    resolution["candidate_id"], resolution["source"]) != (
                    run_id, state.year, state_hash(state), result.adopted_id, "human"):
                    raise ValueError("Human resolution has a wrong run/state/candidate binding.")
                if result.outcome != "escalated":
                    raise ValueError("Human selection is not an autonomous council outcome.")
                yes = []
            else:
                table = Table(state, advisor_ids)
                requests = {e["data"]["request_id"]: e["data"]["brief"] for e in events if e["kind"] == "phase"}
                latest = {}
                last_discussion = None
                moves = {}
                runoff_votes = {}
                pair = None
                for event in events:
                    if event["kind"] != "receipt":
                        continue
                    for data in event["data"]["responses"]:
                        ballot = Ballot.model_validate(data)
                        raw = requests.get(ballot.reply_to)
                        if raw is None:
                            raise ValueError("Certificate answers an unrecorded request.")
                        brief = Brief.model_validate(raw)
                        if (brief.run_id, brief.state) != (run_id, state.public()):
                            raise ValueError("Recorded request is not bound to this run/state.")
                        validate_reply(ballot, brief)
                        row = store.db.execute("SELECT data FROM ballots WHERE run_id=? AND id=?", (run_id, ballot.message_id)).fetchone()
                        if row is None or json.loads(row["data"]) != data:
                            raise ValueError("Certificate contains an unrecorded or changed ballot.")
                        order = speaking_order(state.year, brief.round, advisor_ids)
                        if brief.order != order or brief.speaker != order[brief.turn]:
                            raise ValueError("Recorded request has a wrong speaking order.")
                        latest[ballot.advisor] = data
                        if brief.phase == "discuss":
                            if runoff_votes:
                                raise ValueError("Discussion cannot resume after a frozen runoff.")
                            expected = (0, 0) if last_discussion is None else (
                                (last_discussion.round + 1, 0) if last_discussion.turn == n_advisors - 1
                                else (last_discussion.round, last_discussion.turn + 1))
                            if (brief.round, brief.turn) != expected or brief.table != table.snapshot():
                                raise ValueError("Discussion is not a complete sequential table history.")
                            allowed = set(table.plans)
                            _, moved = table.place(ballot.advisor, ballot, state.year, brief.round)
                            table.vote(ballot.advisor, ballot, allowed)
                            moves[brief.round] = moves.get(brief.round, False) or moved
                            last_discussion = brief
                        elif brief.phase == "runoff":
                            if last_discussion is None or last_discussion.turn != n_advisors - 1:
                                raise ValueError("Runoff follows an incomplete discussion round.")
                            ranked = table.ranked()
                            if len(ranked) < 2 or len(ranked[0]["accept"]) < runoff_floor:
                                raise ValueError("Runoff does not meet the public entry rule.")
                            expected_pair = [r["id"] for r in ranked[:2]]
                            lead, second = ranked[:2]
                            tied = len(lead["accept"]) == len(second["accept"])
                            if len(lead["accept"]) != runoff_floor and not tied:
                                raise ValueError("Runoff does not meet its tie or runoff-floor trigger.")
                            news_in_round = any(e["kind"] == "news" and e["data"].get("round") == last_discussion.round for e in events)
                            if not (last_discussion.round == manifest["max_rounds"] - 1 or last_discussion.round >= 1 and not moves[last_discussion.round] and not news_in_round):
                                raise ValueError("Runoff cannot preempt the negotiation limit or stalemate.")
                            if brief.table != brief.runoff or any(t.model_dump(exclude={"accept", "backers"}) != table.plans[t.id].model_dump(exclude={"accept", "backers"}) for t in brief.runoff if t.id in table.plans):
                                raise ValueError("Runoff changes a frozen finalist budget.")
                            if [t.id for t in brief.runoff] != expected_pair or brief.round != last_discussion.round + 1:
                                raise ValueError("Runoff changes the frozen finalists.")
                            if brief.turn != len(runoff_votes) or ballot.advisor in runoff_votes:
                                raise ValueError("Runoff is not a complete sequential ballot.")
                            pair = expected_pair
                            runoff_votes[ballot.advisor] = ballot.runoff_vote
                if set(latest) != set(advisor_ids) or result.certificate != {
                    "candidate_id": result.adopted_id, "ballots": [latest[a] for a in advisor_ids]}:
                    raise ValueError("Certificate requires the unchanged latest ballot from "
                                     "all council advisors.")
                row = next((r for r in table.rows() if r["id"] == result.adopted_id), None)
                recorded = store.db.execute("SELECT data FROM proposals WHERE run_id=? AND id=?", (run_id, result.adopted_id)).fetchone()
                if row is None or recorded is None or json.loads(recorded["data"])["plan"] != result.plan.as_dict() or row["plan"] != result.plan.as_dict():
                    raise ValueError("Candidate is not an immutable recorded proposal.")
                if result.adopted_author != row["author"]:
                    raise ValueError("Candidate author does not match the recorded proposal.")
                yes = row["accept"]
                if result.outcome == "runoff":
                    yes = [a for a, vote in runoff_votes.items() if vote == result.adopted_id]
                    if set(runoff_votes) != set(advisor_ids) or result.adopted_id not in (pair or []) or len(yes) < majority:
                        raise ValueError("Runoff winner lacks a complete council of verified ballots or a majority.")
                elif result.outcome == "consensus":
                    if len(yes) != n_advisors or runoff_votes:
                        raise ValueError("Consensus does not have all verified council supporters.")
                elif result.outcome == "majority":
                    ranked = table.ranked()
                    unique = len(ranked) == 1 or len(ranked[0]["accept"]) > len(ranked[1]["accept"])
                    strong = len(yes) >= strong_majority and last_discussion.round >= 1 and (
                        last_discussion.round > 1 or last_discussion.turn == n_advisors - 1)
                    news_in_round = any(e["kind"] == "news" and e["data"].get("round") == last_discussion.round for e in events)
                    final = last_discussion.turn == n_advisors - 1 and (
                        last_discussion.round == manifest["max_rounds"] - 1 or
                        last_discussion.round >= 1 and not moves[last_discussion.round] and not news_in_round)
                    if runoff_votes or ranked[0]["id"] != row["id"] or not (
                        strong or final and unique and len(yes) >= majority):
                        raise ValueError("Majority does not meet the public stop rule.")
                else:
                    raise ValueError("Unknown autonomous council outcome.")
                if result.rounds != last_discussion.round + 1:
                    raise ValueError("Result round count does not match the recorded history.")
                if result.outcome == "runoff" and (result.runoff["votes"] != runoff_votes or result.runoff["winner"] != result.adopted_id):
                    raise ValueError("Recorded runoff result differs from the recorded votes.")
                if result.dissent != [a for a in advisor_ids if a not in yes]:
                    raise ValueError("Certificate dissent does not match the recorded votes.")
        with span("budget.validate", run_id=run_id, year=state.year, candidate_id=result.adopted_id):
            legalize(state, result.plan)
        next_state = advance(state, result.plan, rolls)
        data = {**result.record(), "summary": result.summary, "start_state": state.public(),
                "state": next_state.public(), "report": next_state.last_report,
                "opening": {a: p.model_dump() for a, p in result.opening.items()},
                "after_news": {a: p.model_dump() for a, p in result.after_news.items()},
                "satisfaction": result.satisfaction, "runoff": result.runoff,
                "human_choice": result.human_choice, "news_items": result.news,
                "narration_url": None, "yes_voters": yes, "certificate": result.certificate,
                "human_resolution": result.human_resolution,
                "random_inputs": asdict(rolls[state.year - 1]), "next_market_quote": next_state.land_price, **context_headers()}
        return next_state, store.commit_year(run_id, data)
