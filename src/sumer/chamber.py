"""Durable whole-game execution and the existing offline comparison workflow."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from importlib.metadata import version

from sumer.advisors import ADVISOR_IDS, ADVISORS, Beliefs, Scorer
from sumer.baselines import SIMPLE, lone_llm
from sumer.config import CACHE_DIR, settings
from sumer.deliberation import (
    AskHuman,
    Deadlock,
    Emit,
    Gather,
    TechnicalFailure,
    YearResult,
    _noop_emit,
    deliberate_year,
)
from sumer.engine import commit
from sumer.game import RULE_VERSION, advance, new_game, rating
from sumer.observability import context_headers, flush, span
from sumer.protocol import provision_identities, uid
from sumer.storage import Store

DEFAULT_DB = os.getenv("SUMER_DB_PATH", str(CACHE_DIR / "runs.sqlite3"))


@dataclass
class GameResult:
    seed: int
    policy: str
    rating: dict
    run_id: str = ""
    status: str = "completed"
    human_assisted: bool = False
    years: list[dict] = field(default_factory=list)
    outcomes: dict[str, int] = field(default_factory=dict)
    mean_rounds: float = 0.0
    satisfaction: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return self.__dict__


async def play_council(
    seed: int, gather: Gather, *, max_rounds: int | None = None,
    ask_human: AskHuman | None = None, emit: Emit = _noop_emit,
    store: Store | None = None, run_id: str | None = None,
) -> GameResult:
    own_store = store is None
    store = store if store is not None else Store(DEFAULT_DB)
    run_id = run_id or uid()
    state, rolls = new_game(seed)
    history: list[dict] = []
    results: list[YearResult] = []
    keys = provision_identities()
    rounds = max_rounds if max_rounds is not None else settings.max_rounds
    store.create_run(run_id, {
        "rule_version": RULE_VERSION, "seed": seed, "initial_state": state.public(),
        "application_version": version("council-of-sumer"),
        "peer_keys": keys, "advisors": [a.__dict__ for a in ADVISORS.values()],
        "max_rounds": rounds, "transport": settings.transport,
        "llm_mode": settings.llm_mode, "model": settings.llm_model,
        "model_base_url": settings.llm_base_url, "model_timeout_s": settings.llm_timeout_s,
        "model_budget_usd": settings.llm_budget_usd, "model_cache": settings.llm_cache,
        "dependencies": {name: version(name) for name in (
            "agntcy-app-sdk", "ioa-observe-sdk", "cryptography", "pydantic")},
    })

    async def recorded_emit(kind: str, data: dict):
        if kind == "escalate":
            store.status(run_id, "deadlock")
        data = {**context_headers(), **data}
        record = store.append(run_id, kind, data)
        await emit(kind, {**data, "_sequence": record["sequence"], "_run_id": run_id})

    status = "running"
    cancelled = False
    try:
        await recorded_emit("game", {"seed": seed, "state": state.public(), "run_id": run_id})
        while not state.over:
            # Only current public facts and completed history enter this report/brief.
            await recorded_emit("public_report", {
                "year": state.year, "state": state.public(), "report": state.last_report})
            with span("council.year", run_id=run_id, year=state.year):
                yr = await deliberate_year(
                    run_id=run_id, state=state, gather=gather, history=history,
                    peer_keys=keys, max_rounds=rounds, ask_human=ask_human, emit=recorded_emit)
                state, event = commit(store, run_id, state, yr, rolls)
                results.append(yr)
                history.append(event["data"])
                # The engine transaction already persisted this event.
                if yr.human_resolution:
                    for resolution in store.events(run_id, after=event["sequence"] - 2):
                        if resolution["kind"] == "human_resolution":
                            await emit("human_resolution", {
                                **resolution["data"], "_sequence": resolution["sequence"],
                                "_run_id": run_id})
                await emit("year", {**event["data"], "_sequence": event["sequence"],
                                    "_run_id": run_id})
        status = state.status
    except Deadlock:
        status = "deadlock"
    except asyncio.CancelledError:
        cancelled = True
        status = "stopped"
        await recorded_emit("stopped", {"year": state.year, "message": "Run stopped by user."})
        store.status(run_id, "stopped")
    except Exception as exc:  # noqa: BLE001 - preserve the journal on any terminal engine failure
        status = "technical_failure"
        with span("council.technical_failure", run_id=run_id, year=state.year,
                  phase=exc.details.get("phase") if isinstance(exc, TechnicalFailure) else None):
            await recorded_emit("technical_failure", {
                "year": state.year, "message": f"{type(exc).__name__}: {exc}",
                **(exc.details if isinstance(exc, TechnicalFailure) else {})})
    finally:
        # Telemetry collection must not hide the game result or its original failure.
        try:
            flush()
            store.import_traces(os.getenv("SUMER_TRACE_PATH", str(CACHE_DIR / "otel-traces.jsonl")))
        except Exception as exc:  # noqa: BLE001 - retain the authoritative decision journal
            await recorded_emit("observability_error", {"year": state.year,
                                "message": f"Trace collection failed: {type(exc).__name__}: {exc}"})

    g = GameResult(seed=seed, policy="council", rating=rating(state), run_id=run_id,
                   status=status, human_assisted=store.run(run_id)["human_assisted"])
    g.years = [r.summary() for r in results]
    for r in results:
        g.outcomes[r.summary()["outcome"]] = g.outcomes.get(r.summary()["outcome"], 0) + 1
    g.mean_rounds = round(sum(r.rounds for r in results) / max(len(results), 1), 2)
    store.status(run_id, status, g.as_dict())
    await recorded_emit("end", g.as_dict())
    if own_store:
        store.db.close()
    if cancelled:
        raise asyncio.CancelledError
    return g


async def play_simple(seed: int, policy: str) -> GameResult:
    state, rolls = new_game(seed)
    sats: list[dict] = []
    while not state.over:
        beliefs = Beliefs()
        d = await lone_llm(state, beliefs, []) if policy == "lone_llm" else SIMPLE[policy](state, beliefs)
        sc = Scorer(state, beliefs, extra=[d])
        sats.append({a: sc.norm(a, d) for a in ADVISOR_IDS})
        state = advance(state, d, rolls)
    g = GameResult(seed=seed, policy=policy, rating=rating(state), status=state.status)
    g.satisfaction = {a: round(sum(s[a] for s in sats) / max(len(sats), 1), 3) for a in ADVISOR_IDS}
    return g
