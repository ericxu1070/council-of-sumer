"""The existing seeded council-versus-baselines workflow under the revised rules."""
from __future__ import annotations

import json
import statistics as st
import time
from datetime import UTC, datetime

from sumer.advisors import ADVISOR_IDS, Beliefs, Scorer
from sumer.baselines import SIMPLE
from sumer.chamber import play_council, play_simple
from sumer.config import RESULTS_DIR, settings
from sumer.game import RULE_VERSION, State
from sumer.storage import Store
from sumer.transport import inproc

LABELS = {
    "council": "Council (4 agents, negotiated)",
    "lone_ruler": "Lone ruler (equal weights, no debate)",
    "lone_llm": "Lone LLM (one prompt, all goals)",
    "average": "Average of selfish openings",
    **{f"dictator_{a}": f"{a.title()} alone" for a in ADVISOR_IDS},
}


def _row(policy: str, games: list) -> dict:
    finished = [g for g in games if g.status in ("completed", "overthrown", "depopulated")]
    sats = {a: st.mean(g.satisfaction.get(a, 0) for g in games) for a in ADVISOR_IDS}
    return {
        "key": policy, "policy": LABELS[policy],
        "score": round(st.mean(g.rating["score"] for g in finished), 1) if finished else None,
        "completed": f"{sum(g.status == 'completed' for g in games)}/{len(games)}",
        "deadlocks": sum(g.status == "deadlock" for g in games),
        "technical_failures": sum(g.status == "technical_failure" for g in games),
        "starved": round(st.mean(g.rating["starved_total"] for g in games), 1),
        "population": round(st.mean(g.rating["population"] for g in games), 1),
        "fairness": round(min(sats.values()), 3), "satisfaction": sats,
        **{k: sum(g.outcomes.get(k, 0) for g in games)
           for k in ("consensus", "majority", "human_resolution")},
    }


async def run(n_seeds: int, transport: str = "inproc") -> dict:
    if n_seeds < 1:
        raise ValueError("At least one seed is required.")
    if transport == "slim":
        from sumer.transport.slim import SlimCouncil
        council = SlimCouncil()
        await council.connect()
        gather = council.gather
    else:
        gather = inproc.gather

    t0 = time.monotonic()
    games: dict[str, list] = {"council": []}
    for seed in range(1, n_seeds + 1):
        journal = Store(":memory:")
        game = await play_council(seed, gather, store=journal)
        # Post-run benchmark accounting never feeds scores back into live agents.
        years = [e["data"] for e in journal.events(game.run_id) if e["kind"] == "year"]
        scores = []
        for y in years:
            public = {k: v for k, v in y["start_state"].items() if k != "acres_per_person"}
            plan = game.years[len(scores)]["plan"]
            from sumer.game import Decision
            scorer = Scorer(State(**public), Beliefs(), [Decision(**plan)])
            scores.append({a: scorer.norm(a, Decision(**plan)) for a in ADVISOR_IDS})
        game.satisfaction = {a: st.mean(s[a] for s in scores) if scores else 0 for a in ADVISOR_IDS}
        games["council"].append(game)
        journal.db.close()
        print(f"seed {seed}: council {game.status}", flush=True)

    policies = list(SIMPLE) + (["lone_llm"] if settings.llm_mode == "bayleaf" else [])
    for policy in policies:
        games[policy] = [await play_simple(seed, policy) for seed in range(1, n_seeds + 1)]
    out = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "rule_version": RULE_VERSION, "seeds": n_seeds, "transport": transport,
        "llm": settings.llm_mode if settings.llm_mode != "bayleaf" else settings.llm_model,
        "seconds": round(time.monotonic() - t0, 1),
        "rows": [_row(policy, games[policy]) for policy in ["council", *policies]],
        "lessons": [
            "Paused deadlocks and technical failures are counted separately from completed reigns.",
            "Scores include only ended reigns; no automatic human choice completes a benchmark run.",
            "Fairness is an offline policy metric, never a live model decision input.",
        ],
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "bench.json").write_text(json.dumps(out, indent=2) + "\n")
    (RESULTS_DIR / "bench.md").write_text(_markdown(out))
    print(_markdown(out))
    return out


def _markdown(out: dict) -> str:
    columns = ["policy", "score", "completed", "deadlocks", "technical_failures",
               "starved", "population", "fairness"]
    lines = [f"# Benchmark ({out['seeds']} seeds, {out['transport']}, {out['llm']})", "",
             "| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(str(row[c]) for c in columns) + " |" for row in out["rows"]]
    lines += ["", *["- " + item for item in out["lessons"]]]
    return "\n".join(lines) + "\n"
