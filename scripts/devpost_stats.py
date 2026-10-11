"""Render the Devpost headline statistics image from results/bench.json.

    uv run python scripts/devpost_stats.py

Writes .cache/devpost-stats.html (the intermediate page) and renders
docs/devpost-stats.png (1600x900) and docs/devpost-stats-1200x630.png with
headless Chromium (set CHROMIUM=/path/to/binary to override). Every number
comes from results/bench.json; regenerate the benchmark first with
`uv run sumer bench --seeds N`.

The headline is chosen from the data, never fixed: it only claims what the
council actually wins against the single LLM in this benchmark.
"""

from __future__ import annotations

import html
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "results" / "bench.json"
DOCS = ROOT / "docs"


def _survived(row: dict) -> tuple[int, int]:
    a, b = row["survived"].split("/")
    return int(a), int(b)


def _rate(row: dict) -> float:
    kept, n = _survived(row)
    return kept / n if n else 0.0


def _fmt(x: float) -> str:
    return f"{x:,.0f}".replace("-", "−")  # true minus sign, not a dash


def facts(bench: dict) -> dict:
    by = {r["key"]: r for r in bench["rows"]}
    if "lone_llm" not in by:
        sys.exit("bench.json has no lone_llm row: run the benchmark with the live LLM")
    c, llm = by["council"], by["lone_llm"]
    wins = {
        "score": c["score"] > llm["score"],
        "survival": _rate(c) > _rate(llm),
        "starved": c["starved"] < llm["starved"],
        "fairness": c["fairness"] > llm["fairness"],
    }
    dictators = sorted((r for r in bench["rows"] if r["key"].startswith("dictator_")),
                       key=lambda r: -r["score"])
    return {"council": c, "llm": llm, "ruler": by["lone_ruler"], "dictators": dictators,
            "wins": wins}


def headline(f: dict, seeds: int) -> tuple[str, str]:
    """(headline, supporting line), each true for the numbers in bench.json."""
    c, l, w = f["council"], f["llm"], f["wins"]
    same = f"Same model, same {seeds} cities, same hidden harvests."
    fewer = 100 * (l["starved"] - c["starved"]) / l["starved"] if l["starved"] else 0
    if all(w.values()):
        return ("Five LLM advisors beat one LLM on every measure",
                (f"{same} Debating as a council, the model kept {_rate(c):.0%} of reigns "
                f"(alone: {_rate(l):.0%}) and starved {fewer:.0f}% fewer people."))
    if w["score"]:
        return ("The council out-rules a single LLM",
                (f"{same} Mean score {_fmt(c['score'])} for the council vs "
                f"{_fmt(l['score'])} for one LLM given every goal at once."))
    if w["survival"]:
        return ("The council keeps more reigns alive",
                f"{same} {_rate(c):.0%} of council reigns survived vs {_rate(l):.0%} for one LLM.")
    if w["starved"]:
        return ("Fewer starve under the council",
                f"{same} {c['starved']:.1f} starved per reign vs {l['starved']:.1f} for one LLM.")
    if w["fairness"]:
        return ("The council leaves no advisor behind",
                f"{same} Least-satisfied advisor {c['fairness']:.2f} vs {l['fairness']:.2f} for one LLM.")
    return ("Council vs one LLM, same dice",
            f"{same} Mean score {_fmt(c['score'])} for the council vs {_fmt(l['score'])}.")


def tiles(f: dict) -> list[tuple[str, str, str, str]]:
    """(label, council value, single-LLM value, small print)."""
    c, l = f["council"], f["llm"]
    (ck, cn), (lk, ln) = _survived(c), _survived(l)
    return [
        ("Reigns survived", f"{_rate(c):.0%}", f"{_rate(l):.0%}", f"{ck} of {cn} vs {lk} of {ln}"),
        ("Starved per reign", f"{c['starved']:.0f}", f"{l['starved']:.0f}", "people, mean of all reigns"),
        ("Least-satisfied advisor", f"{c['fairness']:.2f}", f"{l['fairness']:.2f}",
         "mandate satisfaction, 0 to 1"),
        ("Mean score", _fmt(c["score"]), _fmt(l["score"]), "the original game's verdict, as points"),
    ]


BAR_NAMES = {"council": "Council of five", "lone_llm": "Single LLM",
             "lone_ruler": "Equal-weight ruler"}


def bars_svg(f: dict, width: int, row_h: int, label_w: int) -> str:
    groups = [("Live LLM", [f["council"], f["llm"]]),
              ("Rule-based reference, no LLM", [f["ruler"], *f["dictators"]])]
    rows = [r for _, g in groups for r in g]
    hi = max(0.0, max(r["score"] for r in rows))
    lo = min(0.0, min(r["score"] for r in rows))
    span = (hi - lo) or 1
    # Room for value labels on both sides of the plot, so none touches a name.
    left, right = label_w + (78 if lo < 0 else 0), width - 84
    plot_w = right - left
    zero_x = left + plot_w * (-lo / span)
    head_h = 34
    out, y = [], 0
    for gi, (title, members) in enumerate(groups):
        if gi:
            y += 10
            out.append(f'<line x1="0" x2="{width}" y1="{y}" y2="{y}" class="divider"/>')
            y += 6
        out.append(f'<text x="0" y="{y + head_h / 2 + 2}" class="ghead" '
                   f'dominant-baseline="central">{html.escape(title.upper())}</text>')
        y += head_h
        for r in members:
            name = BAR_NAMES.get(r["key"], r["policy"])
            w = max(plot_w * abs(r["score"]) / span, 2)
            x = zero_x if r["score"] >= 0 else zero_x - w
            cls = {"council": "bar-council", "lone_llm": "bar-llm"}.get(r["key"], "bar-other")
            strong = r["key"] in ("council", "lone_llm")
            # Value label sits beyond the bar's end, on the side it extends to.
            if r["score"] >= 0:
                vx, anchor = x + w + 10, "start"
            else:
                vx, anchor = x - 10, "end"
            out.append(
                f'<text x="{label_w - 16}" y="{y + row_h / 2}" class="blabel{" strong" if strong else ""}" '
                f'text-anchor="end" dominant-baseline="central">{html.escape(name)}</text>'
                f'<rect x="{x:.1f}" y="{y + row_h * 0.15:.1f}" width="{w:.1f}" '
                f'height="{row_h * 0.7:.1f}" rx="4" class="{cls}"/>'
                f'<text x="{vx:.1f}" y="{y + row_h / 2}" class="bval{" strong" if strong else ""}" '
                f'text-anchor="{anchor}" dominant-baseline="central">{_fmt(r["score"])}</text>'
            )
            y += row_h
    out.insert(0, f'<line x1="{zero_x:.1f}" x2="{zero_x:.1f}" y1="0" y2="{y}" class="axis"/>'
                  f'<text x="{zero_x:.1f}" y="{y + 20}" class="zero" text-anchor="middle">0</text>')
    total = y + 28
    return (f'<svg viewBox="0 0 {width} {total}" width="{width}" height="{total}" '
            f'role="img" aria-label="Mean score per reign by decider">{"".join(out)}</svg>')


def wedges(n: int, seed: int) -> str:
    """A row of cuneiform-like wedge marks (decorative, not real signs)."""
    rnd = random.Random(seed)
    parts, x = [], 0
    for _ in range(n):
        kind = rnd.choice("vhvhd")
        if kind == "v":
            parts.append(f'<path d="M{x} 0 l9 0 l-4.5 22 z"/>')
            x += 14
        elif kind == "h":
            parts.append(f'<path d="M{x} 6 l0 9 l22 -4.5 z"/>')
            x += 28
        else:
            parts.append(f'<path d="M{x} 2 l10 4 l-8 8 z"/><path d="M{x + 3} 8 l14 14" stroke-width="2"/>')
            x += 22
        if rnd.random() < 0.18:
            x += 18
    return f'<svg class="wedges" viewBox="0 0 {x} 24" height="24" width="{x}">{"".join(parts)}</svg>'


CSS = """
:root {
  --clay: #d9b98a; --clay-hi: #e8cfa3; --clay-lo: #b98f5c;
  --umber: #3b2516; --umber-2: #5e3d26; --muted: #6f4d30;
  --terracotta: #a8461f; --llm: #5a3b26; --other: #b39069;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: 100vw; height: 100vh; overflow: hidden; background: #2a1a10; }
.tablet {
  position: absolute; inset: 22px; border-radius: 34px; overflow: hidden;
  background: radial-gradient(ellipse at 30% 20%, var(--clay-hi) 0%, var(--clay) 45%, var(--clay-lo) 100%);
  box-shadow: inset 0 0 0 3px rgba(59,37,22,.35), inset 0 0 60px rgba(59,37,22,.45);
  color: var(--umber); font-family: 'EB Garamond', Georgia, serif;
}
.grain { position: absolute; inset: 0; opacity: .35; mix-blend-mode: multiply; pointer-events: none; }
.wedges { fill: var(--umber-2); stroke: var(--umber-2); opacity: .45; }
.wedge-row { position: absolute; left: 56px; right: 56px; height: 24px; overflow: hidden; }
.wedge-row.top { top: 26px; } .wedge-row.bottom { bottom: 22px; }
.content { position: absolute; inset: 70px 64px 92px 64px; display: grid;
  grid-template-columns: 700px 1fr; column-gap: 60px; align-items: start; }
.kicker { font-family: 'Cinzel', serif; font-weight: 700; letter-spacing: .22em;
  font-size: 19px; color: var(--terracotta); }
h1 { font-family: 'Cinzel', serif; font-weight: 800; font-size: 62px; line-height: 1.05;
  margin-top: 12px; color: var(--umber);
  text-shadow: 0 1px 0 rgba(255,240,210,.55), 0 -1px 0 rgba(59,37,22,.35); }
.sub { font-size: 26px; line-height: 1.3; margin-top: 16px; color: var(--umber-2); }
.tiles { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 26px; }
.tile { background: rgba(59,37,22,.09); border-radius: 14px; padding: 12px 18px 12px;
  box-shadow: inset 0 2px 5px rgba(59,37,22,.28), inset 0 -1px 0 rgba(255,240,210,.5); }
.tile .lab { font-family: 'Cinzel', serif; font-size: 15px; letter-spacing: .12em; font-weight: 700;
  color: var(--muted); text-transform: uppercase; }
.tile .row { display: flex; align-items: baseline; gap: 12px; margin-top: 2px; }
.tile .c { font-family: 'Cinzel', serif; font-weight: 800; font-size: 48px; color: var(--terracotta); }
.tile .vs { font-size: 22px; color: var(--umber-2); }
.tile .vs b { font-weight: 700; color: var(--llm); }
.tile .small { font-size: 17px; color: var(--muted); }
.chart h2 { font-family: 'Cinzel', serif; font-size: 26px; font-weight: 700; letter-spacing: .06em; }
.chart .note { font-size: 19px; line-height: 1.3; color: var(--muted); margin: 4px 0 10px; }
.ghead { font-family: 'Cinzel', serif; font-size: 15px; font-weight: 700; letter-spacing: .14em; fill: var(--muted); }
.blabel { font-family: 'EB Garamond', serif; font-size: 22px; font-weight: 500; fill: var(--umber); }
.bval { font-family: 'Cinzel', serif; font-size: 19px; font-weight: 700; fill: var(--umber-2); }
.strong { font-weight: 700; fill: var(--umber); font-size: 24px; }
.bval.strong { font-size: 22px; }
.zero { font-family: 'Cinzel', serif; font-size: 15px; fill: var(--muted); }
.bar-council { fill: var(--terracotta); } .bar-llm { fill: var(--llm); } .bar-other { fill: var(--other); }
.axis { stroke: var(--umber); stroke-width: 1.5; opacity: .6; }
.divider { stroke: var(--umber); stroke-dasharray: 2 6; opacity: .4; }
@media (max-height: 860px) {
  h1 { font-size: 56px; } .sub { font-size: 24px; margin-top: 12px; }
  .tiles { margin-top: 18px; gap: 12px; } .tile { padding: 10px 16px; }
  .tile .c { font-size: 44px; } .tile .small { display: none; }
}
.foot { position: absolute; left: 64px; right: 64px; bottom: 54px; font-size: 18px; color: var(--muted); }
"""

GRAIN = """<svg class="grain" width="100%" height="100%"><filter id="n">
<feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="3" seed="7"/>
<feColorMatrix values="0 0 0 0 .23 0 0 0 0 .15 0 0 0 0 .09 0 0 0 .55 0"/></filter>
<rect width="100%" height="100%" filter="url(#n)"/></svg>"""


def page(bench: dict) -> str:
    f = facts(bench)
    seeds = bench["seeds"]
    head, sub = headline(f, seeds)
    model = str(bench.get("llm", "")).split("/")[-1]
    c = f["council"]
    tile_html = "".join(
        f'<div class="tile"><div class="lab">{html.escape(lab)}</div><div class="row">'
        f'<span class="c">{html.escape(cv)}</span><span class="vs">vs <b>{html.escape(lv)}</b> one LLM</span>'
        f'</div><div class="small">{html.escape(small)}</div></div>'
        for lab, cv, lv, small in tiles(f)
    )
    excluded = c["deadlocks"] + c["technical_failures"]
    foot = (f"{seeds} seeded 5-year reigns per decider, decided live by {html.escape(model)}"
            + (f" &middot; {excluded} council reign{'s' if excluded != 1 else ''} deadlocked, excluded"
               if excluded else ""))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Council of Sumer benchmark</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@700;800&family=EB+Garamond:wght@500;700&display=block" rel="stylesheet">
<style>{CSS}</style></head><body>
<div class="tablet">{GRAIN}
<div class="wedge-row top">{wedges(80, 3)}</div>
<div class="content">
  <div>
    <div class="kicker">COUNCIL OF SUMER &middot; BENCHMARK</div>
    <h1>{html.escape(head)}</h1>
    <p class="sub">{html.escape(sub)}</p>
    <div class="tiles">{tile_html}</div>
  </div>
  <div class="chart">
    <h2>Mean score per reign</h2>
    <p class="note">Higher is better. The rule-based rulers search every legal plan with the exact game rules; they are a ceiling, not an LLM.</p>
    {bars_svg(f, 700, 46, 245)}
  </div>
</div>
<div class="foot">{foot}</div>
<div class="wedge-row bottom">{wedges(80, 11)}</div>
</div></body></html>"""


def render(src: Path, out: Path, w: int, h: int) -> None:
    exe = os.getenv("CHROMIUM") or shutil.which("chromium") or shutil.which("google-chrome")
    if not exe:
        sys.exit("No Chromium found; set CHROMIUM=/path/to/chromium")
    # Lay out at 1600 CSS px wide, then scale to the target pixel width.
    scale = w / 1600
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--hide-scrollbars", f"--screenshot={out}",
         f"--window-size=1600,{round(h / scale)}", f"--force-device-scale-factor={scale}",
         "--virtual-time-budget=8000", src.as_uri()],
        check=True, capture_output=True,
    )


def main() -> None:
    bench = json.loads(BENCH.read_text())
    DOCS.mkdir(exist_ok=True)
    src = ROOT / ".cache" / "devpost-stats.html"  # intermediate page, not committed
    src.parent.mkdir(exist_ok=True)
    src.write_text(page(bench))
    for name, w, h in (("devpost-stats.png", 1600, 900), ("devpost-stats-1200x630.png", 1200, 630)):
        render(src, DOCS / name, w, h)
        print(DOCS / name)


if __name__ == "__main__":
    main()
