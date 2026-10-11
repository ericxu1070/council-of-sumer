"""Evidence: the council vs simpler deciders, and the effect of new information.

Every decider plays the same seeds (same hidden dice). Writes
results/bench.json (read by the UI's Evidence tab) and results/bench.md.
"""

from __future__ import annotations

import json
import statistics as st
import time
from datetime import datetime

from sumer.advisors import ADVISOR_IDS
from sumer.baselines import SIMPLE
from sumer.chamber import play_council, play_simple, scheduled_news
from sumer.config import RESULTS_DIR, settings
from sumer.deliberation import deliberate_year
from sumer.game import advance, new_game
from sumer.transport import inproc

LABELS = {
    "council": "Council (4 agents, negotiated)",
    "lone_ruler": "Lone ruler (equal weights, no debate)",
    "lone_llm": "Lone LLM (one prompt, all goals)",
    "average": "Average of selfish openings",
    "dictator_farmer": "Farmer alone",
    "dictator_priest": "Priest alone",
    "dictator_general": "General alone",
    "dictator_merchant": "Merchant alone",
}


def _row(policy: str, games: list) -> dict:
    n = len(games)
    sat = {a: st.mean(g.satisfaction[a] for g in games) for a in ADVISOR_IDS}
    row = {
        "policy": LABELS.get(policy, policy),
        "key": policy,
        "score": round(st.mean(g.rating["score"] for g in games), 1),
        "survived": f"{sum(not g.rating['impeached'] for g in games)}/{n}",
        "starved": round(st.mean(g.rating["starved_total"] for g in games), 1),
        "population": round(st.mean(g.rating["population"] for g in games), 1),
        "fairness": round(min(sat.values()), 3),
        "satisfaction": {a: round(v, 3) for a, v in sat.items()},
    }
    if policy == "council":
        for k in ("consensus", "majority", "escalated"):
            row[k] = sum(g.outcomes.get(k, 0) for g in games)
        row["mean_rounds"] = round(st.mean(g.mean_rounds for g in games), 2)
    return row


async def _counterfactual_info(seeds: list[int], gather) -> dict:
    """For every year where news arrived, rerun the same year from the same
    state without the news, and check whether the adopted plan changes."""
    news_years = changed = 0
    shifts = []
    for seed in seeds:
        state, rolls = new_game(seed)
        history: list[dict] = []
        while not state.over:
            news = scheduled_news(seed, state.year, rolls, True)
            kw = {"seed": seed, "state": state, "gather": gather, "history": history,
                      "max_rounds": settings.max_rounds}
            with_news = await deliberate_year(**kw, scheduled_news=news)
            if news:
                without = await deliberate_year(**kw, scheduled_news=None)
                news_years += 1
                if with_news.plan != without.plan:
                    changed += 1
                    shifts.append({
                        "seed": seed,
                        "year": state.year,
                        "news": news["text"],
                        "without": without.plan.__dict__,
                        "with": with_news.plan.__dict__,
                    })
            state = advance(state, with_news.plan, rolls)
            history.append(with_news.summary())
    return {"news_years": news_years, "changed": changed, "examples": shifts[:6]}


async def run(n_seeds: int, transport: str = "inproc") -> dict:
    seeds = list(range(1, n_seeds + 1))
    if transport == "slim":
        from sumer.transport.slim import SlimCouncil

        council = SlimCouncil()
        await council.connect()
        gather = council.gather
    else:
        gather = inproc.gather

    t0 = time.time()
    games: dict[str, list] = {"council": [], "council_silent": []}
    for s in seeds:
        games["council"].append(await play_council(s, gather))
        games["council_silent"].append(await play_council(s, gather, reveal_news=False))
        print(f"seed {s}: council {games['council'][-1].rating['score']}", flush=True)

    policies = list(SIMPLE) + (["lone_llm"] if settings.llm_mode == "bayleaf" else [])
    for p in policies:
        games[p] = [await play_simple(s, p) for s in seeds]
    games["lone_ruler_silent"] = [await play_simple(s, "lone_ruler", reveal_news=False) for s in seeds]

    rows = [_row(p, games[p]) for p in ["council", *policies]]
    rows.sort(key=lambda r: -r["score"])
    cf = await _counterfactual_info(seeds, gather)

    def mean_score(k):
        return round(st.mean(g.rating["score"] for g in games[k]), 1)

    info = [
        {"decider": "Council", "score with scouts": mean_score("council"),
         "score silent": mean_score("council_silent"),
         "plans changed by news": f"{cf['changed']}/{cf['news_years']} years"},
        {"decider": "Lone ruler", "score with scouts": mean_score("lone_ruler"),
         "score silent": mean_score("lone_ruler_silent"), "plans changed by news": "-"},
    ]
    by = {r["key"]: r for r in rows}
    c, lone, avg = by["council"], by["lone_ruler"], by["average"]
    lessons = [
        (f"Negotiation beats averaging: the council scored {c['score']} vs {avg['score']} for "
        f"averaging each advisor's selfish plan ({avg['survived']} reigns survived vs {c['survived']})."),
        (f"A single equal-weight ruler scored {lone['score']} vs the council's {c['score']}. "
        f"The council's edge is fairness: its worst-served advisor averaged {c['fairness']} "
        f"satisfaction vs {lone['fairness']} under the lone ruler."),
        (f"News changed the adopted plan in {cf['changed']} of {cf['news_years']} years where it "
        f"arrived. Scouts moved the council's mean score from {info[0]['score silent']} "
        f"(silent) to {info[0]['score with scouts']}."),
        (f"Outcomes over {len(seeds) * 10} council-years: {c['consensus']} consensus, "
        f"{c['majority']} majority with recorded dissent, {c['escalated']} escalated to a human. "
        f"Mean rounds per year: {c['mean_rounds']}."),
    ]
    collapsed = [r["policy"] for r in rows if r["key"].startswith("dictator_")
                 and int(r["survived"].split("/")[0]) < n_seeds / 2]
    if collapsed:
        lessons.append(
            f"Single-mandate rule fails: {', '.join(collapsed)} lost most reigns. "
            "One objective is not enough, which is why each advisor's red line matters."
        )
    out = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "seeds": n_seeds,
        "transport": transport,
        "llm": settings.llm_mode if settings.llm_mode != "bayleaf" else settings.llm_model,
        "seconds": round(time.time() - t0, 1),
        "rows": rows,
        "info": info,
        "info_examples": cf["examples"],
        "lessons": lessons,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "bench.json").write_text(json.dumps(out, indent=2))
    (RESULTS_DIR / "bench.md").write_text(_markdown(out))
    print(_markdown(out))
    return out


def _markdown(out: dict) -> str:
    cols = ["policy", "score", "survived", "starved", "population", "fairness"]
    lines = [
        f"# Benchmark ({out['seeds']} seeds, transport {out['transport']}, reasoning {out['llm']})",
        "",
        "| " + " | ".join(cols) + " |",
        "|" + "---|" * len(cols),
    ]
    lines += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in out["rows"]]
    lines += ["", "## New information", ""]
    keys = list(out["info"][0])
    lines += ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    lines += ["| " + " | ".join(str(r[k]) for k in keys) + " |" for r in out["info"]]
    lines += ["", "## Lessons", ""] + [f"- {l}" for l in out["lessons"]]
    return "\n".join(lines) + "\n"
