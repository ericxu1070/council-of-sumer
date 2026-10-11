# Council of Sumer

**Four AI advisors with competing goals govern an ancient city, with no king to break ties.**

Built for the UCSC AI Hackathon @ SF Tech Week (Oct 10 to 11, 2026), Cisco Outshift track
"When AI Agents Disagree", **Level 3: Build a collective intelligence**.

We took *Hamurabi* (1968), the original city-management game: each year you decide how much
land to trade, how much grain to feed your people, and how much to plant, while harvests,
rats, and plague strike at random. Instead of one ruler, a council of four autonomous agents
decides. Each one has a public mandate and a red line, and they talk to each other over
**AGNTCY SLIM**.

| Advisor | Mandate | Red line |
|---|---|---|
| Voice of the Farmers | Grow the population. Nobody starves. | Never starve more than 10% of people |
| High Priest of Enlil | Keep the granaries full. Plans for a bad harvest. | Never leave under half a year of food (in a bad year) |
| General of the Walls | Hold and expand land. | Never sell more than 10% of land in a year |
| Chief Merchant | Maximize wealth at market prices. | None. Everything has a price. |

These mandates really conflict: buying land drains the granary, feeding everyone leaves less
seed, and the Priest's caution costs the General land.

## Quick start

```bash
cp .env.example .env      # set LLM_MODE=bayleaf and BAYLEAF_API_KEY for live LLM advisors
uv sync
uv run sumer up           # SLIM node + 4 advisor agents + web chamber
# open http://localhost:8000
```

Other commands:

```bash
uv run sumer bench --seeds 30          # council vs baselines, writes results/bench.{json,md}
uv run pytest                          # unit tests
uv run sumer node | agent <id> | web   # run the pieces separately
```

No Docker is needed. The SLIM data-plane node runs inside Python via `slim-bindings`.

See [DEMO.md](DEMO.md) for the step-by-step live demo script.

## Architecture

```
                      +-------------------- AGNTCY SLIM node (:46357) --------------------+
 Browser <--SSE--  Clerk (web.py) --A2A broadcast, MLS group session-->  farmer  priest  general  merchant
 (glass box)       relays + tallies        (one topic per round)          4 separate A2A agent processes
                   never chooses   <---------- signed Ballots ----------  (agent.py: policy or LLM mind)
```

- **Advisors** (`agent.py`) are four separate processes, each serving an A2A agent card over
  SLIM patterns (`transport/slim.py`, AGNTCY App SDK 0.5.5).
- **The clerk** (`deliberation.py`) broadcasts a `Brief` each round to all four advisors in one
  SLIM group session and collects `Ballot`s. It relays and applies a public rule. It never ranks
  plans by its own judgment, so there is no central decision-maker.
- **Ballots are HMAC-signed** per advisor (`protocol.py`). A forged or altered vote is dropped and
  flagged in the glass box.
- **Reasoning**: with `LLM_MODE=bayleaf`, each advisor asks an LLM (default
  `deepseek-v4.1-flash` via UCSC Bayleaf, thinking off) for a private thought, a proposal, its
  votes, and a public speech. Red lines are enforced in code, so a model cannot argue its own
  advisor past one. With `LLM_MODE=mock`, the same advisors use a deterministic concession
  strategy, which powers the benchmarks and is the automatic fallback.
- **The game** (`game.py`) pre-rolls every die from the seed, so every decider faces exactly the
  same harvests, rats, and plagues.

## The deliberation protocol

Each year runs up to 5 rounds:

1. **Round 0**: every advisor tables an opening proposal.
2. **Rounds 1 to 4**: every advisor votes accept, veto (red line only), or pass on each tabled
   plan, and tables a revised one. Advisors start selfish and concede over time: each round they
   give the others' mandates more weight and accept lower scores for themselves. Last year's
   outvoted advisors get double weight this year, so recorded dissent carries forward.
3. **Stop rules**
   - **Consensus**: all four accept one plan. Adopt it.
   - **Stalemate**: nobody moved and no news arrived. Skip to the final rule.
   - **Final rule**: if the best plan has 3 of 4 accepts and no red-line veto, adopt it as a
     **majority** and record the dissent. Otherwise it is a **deadlock**.
   - **Deadlock**: escalate to a human. The reign pauses and the human sees each advisor's
     position and private reasoning, then picks a tabled plan or the pre-committed safe plan.

### When consensus is not the right outcome

- **Stop and adopt** when everyone accepts.
- **Keep the disagreement** when 3 of 4 agree and nobody's red line is crossed. Forcing unanimity
  would water the plan down. We adopt it, but the dissent stays on record and gains weight
  next year.
- **Ask a human** when a red line is at stake or no majority exists. A majority must never override
  a red line, because red lines encode harms (starvation, a bare granary, losing the land) that
  the others' scores would trade away.

## Level 3 criteria, and where to see each one

| Criterion | Where it shows |
|---|---|
| Genuine disagreement | Opening proposals differ by hundreds of acres and bushels; vetoes in the glass box |
| Deliberation mechanism | Rounds of propose, vote, and concede over SLIM, with the stop rules above |
| Effect of new information | Scouts report true news mid-debate (locusts, rats, plague, prices). See the "before and after the news" table, the **Send a scout** buttons, and the counterfactual benchmark. |
| Comparison to a baseline | Evidence tab and `results/bench.md`: lone ruler, averaging, single-mandate dictators |
| Stop, keep disagreeing, or ask a human | Consensus, majority, and escalated badges, plus the deadlock dialog |

## Safe and observable: the glass box

The right-hand column traces the whole system as it thinks:

- every clerk broadcast: SLIM topic, recipients, bytes, round-trip time
- for every advisor, every round: its **private thought** (LLM stated reasoning or the policy's
  explanation), the forecast it used, its self vs group weight, its acceptance threshold, and a
  per-plan table of score, threshold, red lines hit, and vote, plus LLM latency and cost
- signature verification for every ballot, and alerts for rejected or missing ballots
- scout news and the exact belief update it caused

Advisors only hear each other's public **speeches**. The private thoughts are for humans. Click
an advisor card to filter the trace to that agent.

## Results

30 seeds, same dice for every decider. Full table: [`results/bench.md`](results/bench.md)
(regenerate with `uv run sumer bench`). The Evidence tab in the UI shows the same data.

| Decider | Mean score | Survived | Mean starved | Mean people | Fairness |
|---|---|---|---|---|---|
| Farmer alone | 1189.8 | 30/30 | 0 | 102.5 | 0.407 |
| Lone ruler (equal weights, no debate) | 1126.7 | 30/30 | 2.3 | 99.9 | 0.423 |
| **Council (4 agents, negotiated)** | 1048.3 | 30/30 | 4.1 | 96.5 | **0.459** |
| General alone | 1032.8 | 30/30 | 4.4 | 95.7 | 0.254 |
| Average of selfish openings | -2317.3 | 10/30 | 81.2 | 2.1 | 0.388 |
| Priest alone | -3147.4 | 0/30 | 88.1 | 1.9 | 0.0 |
| Merchant alone | -3163.6 | 0/30 | 93.7 | 0.2 | 0.125 |

Fairness is the average satisfaction of the worst-served advisor (0 to 1). These numbers come from
the offline policy minds (`LLM_MODE=mock`), so they are deterministic and reproducible.

- **Negotiation beats naive aggregation.** Averaging each advisor's selfish plan survives 10 of 30
  reigns. The council survives all 30.
- **We lose to simpler baselines on raw score and win on fairness.** A single equal-weight ruler
  scores 7% higher, and the Farmer alone scores higher still, because the score rewards
  population. The council has the best fairness of any decider: it protects minority mandates at
  some cost to the average. We think that is the honest tradeoff of a council.
- **New information changes decisions.** Rerunning each news year from the same state without the
  report changes the adopted plan in 85 of 287 years, and scouts raise the council's mean score
  from 998.5 to 1048.3. The lone ruler gains nothing from the same news (1132.0 silent vs 1126.7).
- **Outcomes over 300 council-years:** 152 consensus, 99 majority with recorded dissent, 49
  escalated to a human. Mean 4.15 rounds per year.
- **Single mandates fail.** The Priest alone and the Merchant alone lose every reign.

## Lessons learned

- The first council collapsed: a 3-of-4 majority kept outvoting the cautious Priest until the
  granary ran dry. Giving the Priest a bad-year forecast and making recorded dissent carry
  forward fixed it. Minority voices need structural weight, not just a vote.
- LLM advisors argued well but repeated themselves until we told them the deadline and every
  advisor's red line. Negotiation needs shared rules, not just goals.
- Escalation must be rare to mean anything. An early version escalated almost every year because
  red lines were too strict, which made the human the de facto king.

## Cost and offline safety

- Every LLM reply is cached in `.cache/llm/` by prompt hash. Rehearse a seed once and the demo
  replays it with no network.
- `LLM_BUDGET_USD` (default $3.00) is a hard cap tracked from the cost Bayleaf reports per call.
  Past it, advisors fall back to the offline policy.
- A full 10-year LLM game costs a few cents.

## Project layout

```
src/sumer/
  game.py          Hamurabi rules, seeded dice, ratings
  advisors.py      mandates, red lines, forecasts, scout reports
  agent.py         one advisor's mind (policy + LLM) and its glass-box trace
  deliberation.py  the council protocol and stop rules
  chamber.py       plays whole games
  baselines.py     lone ruler, averaging, dictators, lone LLM
  bench.py         evidence generation
  protocol.py      Brief / Ballot wire format, HMAC signing
  transport/       slim.py (AGNTCY) and inproc.py (tests)
  web.py           FastAPI + SSE chamber, human escalation
  voice.py         optional ElevenLabs voices
web/index.html     the chamber UI
tests/             unit tests
```
