# Council of Sumer

**Seven AI advisors with competing goals govern an ancient city, with no king to break ties.**

Built for the UCSC AI Hackathon @ SF Tech Week (Oct 10 to 11, 2026), Cisco Outshift track
"When AI Agents Disagree", **Level 3: Build a collective intelligence**.

We took *Hamurabi* (1968), the original city-management game: each year you decide how much
land to trade, how much grain to feed your people, and how much to plant, while harvests,
rats, and plague strike at random. Instead of one ruler, a council of seven autonomous agents
decides over a five-year reign. Each one has a public mandate and a red line, and they argue
with each other, one speaker at a time, over **AGNTCY SLIM**.

| Advisor | Mandate | Red line |
|---|---|---|
| Voice of the Farmers | Grow the population. Nobody starves. | Never starve more than 10% of people |
| High Priest of Enlil | Keep the granaries full. Plans for a bad harvest. | Never leave under half a year of food (in a bad year) |
| General of the Walls | Hold and expand land. | Never sell more than 10% of land in a year |
| Chief Merchant | Maximize wealth at market prices. | None. Everything has a price. |
| Canal Builder | Plant as many acres as we can, every year, and gain land and hands to sow more. | Never leave more than half of the land we could sow fallow |
| Royal Physician | Generous rations, food in store, no crowding to breed plague. | Never feed people less than 90% of what they need |
| Envoy to Lagash | Peace with the neighbors: no land grabs, steady borders, modest trade. | Never buy more than 10% more land in a year |

These mandates really conflict: the General and the Builder want land that the Envoy will not
let them grab, the Physician's extra rations eat the Priest's reserve and the Builder's seed,
and in a locust year the Priest's granary and the General's acres cannot both be saved.

## Hamurabi game rules

Actions are whole numbers. The engine rejects an invalid budget without changing its amounts.
Trading happens first, followed by feeding (20 bushels/person) and planting (floor(acres/2)
bushels of seed, at most ten acres/person). Rats consume remaining stores before the new
harvest is added. Immigration uses the starting population, including years with some
starvation. More than 45% starvation ends the reign before arrivals or plague; plague leaves
floor(population/2) survivors. The final year's arrivals and plague apply before the final
rating. A depopulated city also ends the reign. Average annual starvation includes zero years.

These mechanics follow [Ahl's classic BASIC Hamurabi](https://github.com/coding-horror/basic-computer-games/blob/main/43_Hammurabi/hammurabi.bas).
Seed costs retain its floor rounding, including one planted acre requiring zero bushels.

## Quick start

```bash
cp .env.example .env      # set LLM_MODE=bayleaf and BAYLEAF_API_KEY for live LLM advisors
uv sync
uv run sumer up           # SLIM node + 7 advisor agents + web chamber
# open http://localhost:8000
```

Other commands:

```bash
uv run sumer bench --seeds 30          # council vs baselines, writes results/bench.{json,md}
uv run pytest                          # unit tests
uv run sumer node | agent <id> | web   # run the pieces separately
uv run python scripts/find_runoff_seed.py --seeds 200   # demo seeds with a runoff
```

Settings live in `.env` (see `.env.example`): `REIGN_YEARS` (default 5), `MAX_ROUNDS` (default 6),
`PACE_S` (seconds between council messages, default 1.0), and the optional ElevenLabs voices.

No Docker is needed. The SLIM data-plane node runs inside Python via `slim-bindings`.

See [DEMO.md](DEMO.md) for the step-by-step live demo script.

## Architecture

```
                      +---------------------- AGNTCY SLIM node (:46357) ----------------------+
 Browser <--SSE--  Clerk (web.py) --A2A unicast, one Brief per turn-->  farmer priest general merchant
 (glass box,       relays + tallies     (only the speaker is asked)      builder healer diplomat
  narrator)        never chooses   <-------- one signed Ballot ---------  7 separate A2A agent processes
                                                                        (agent.py: policy or LLM mind)
```

- **Advisors** (`agent.py`) are seven separate processes, each serving an A2A agent card over
  SLIM patterns (`transport/slim.py`, AGNTCY App SDK 0.5.5). `sumer up` starts one per advisor.
- **The clerk** (`deliberation.py`) runs the meeting. For each turn it sends a `Brief` to the one
  advisor whose turn it is, over a SLIM point-to-point session to that advisor's own name (with a
  one-member group session as fallback), and collects its `Ballot`. It relays and applies a
  public rule. It never ranks plans by its own judgment, so there is no central decision-maker.
- **Ballots are Ed25519-signed** with distinct local credentials per advisor (`protocol.py`), every turn and every runoff vote. A
  forged or altered ballot is dropped and flagged in the glass box.
- **Reasoning**: with `LLM_MODE=bayleaf`, each advisor asks an LLM (default
  `deepseek-v4.1-flash` via UCSC Bayleaf, thinking off) for a private thought, a proposal, its
  votes, and a public speech. Red lines are enforced in code, so a model cannot argue its own
  advisor past one. With `LLM_MODE=mock`, the same advisors use a deterministic concession
  strategy, which powers explicitly selected mock mode and the benchmarks. Live failures are retried once and then stop the run.
- **The game** (`game.py`) pre-rolls every die from the seed, so every decider faces exactly the
  same harvests, rats, and plagues.
- **The narrator** (`narrator.py`) closes each year with one or two plain-English sentences for a
  non-technical audience (built from templates, no LLM), read aloud by a deep ElevenLabs voice.
  The chamber waits for the narrator to finish before the next year's debate starts.

## The deliberation protocol

Each year is a real meeting, up to 6 rounds of turns:

1. **Turns, not polls.** The seven advisors speak one at a time in a fixed order, and the first
   seat moves one place every round. On its turn an advisor sees the table (every plan with its
   current support), everything said earlier this round, and the round before. It tables a
   proposal or backs a plan already on the table, votes accept or veto (red line only) on the
   tabled plans, and answers the others by name in at most 25 words ("Builder, your canal plan
   starves the east quarter..."). A new proposal is tabled at once, so later speakers in the same
   round can accept it. A plan nobody backs any more is withdrawn.
2. **News arrives mid-debate.** Scout reports land at the start of round 2, so the opening
   positions and the reaction to the news are both on record.
3. **Concession.** Advisors start selfish and concede over time: they weigh the others' mandates
   more and accept lower scores for themselves as the meeting runs. Last year's outvoted advisors
   get double weight this year, so recorded dissent carries forward.
4. **Stop rules**, checked after every turn
   - **Consensus**: all seven accept one plan. Adopt it.
   - **Strong majority**: once round 2 is complete, a plan with 5 of 7 accepts and no red-line
     veto is adopted, and the dissent is recorded.
   - **Stalemate**: a whole round with no proposal change and no news. Go to the final rule.
5. **Final rule** (stalemate, or the last round ends)
   - **Majority**: the best plan has 4 of 7 accepts, no veto, and no tie. Adopt it and record
     the dissent.
   - **Runoff**: the best plan has only 3 of 7, the top two tie, or the leader carries a red-line
     veto. The top two plans go head to head, and every advisor, again one at a time with a
     one-line speech, votes A or B. An advisor whose red line a plan crosses must vote for the
     other. A plan with 4 of 7 runoff votes that crosses nobody's red line is adopted.
   - **Escalate**: under 3 accepts (deadlock), both finalists cross a red line, or the runoff
     winner crosses one. The reign pauses and the human sees each advisor's position and private
     reasoning, then picks one of the top plans or the pre-committed safe plan.

### When consensus is not the right outcome

- **Stop and adopt** when everyone accepts.
- **Keep the disagreement** when a clear majority agrees and nobody's red line is crossed.
  Forcing unanimity among seven would water the plan down. We adopt it, but the dissent stays on
  record and gains weight next year.
- **Vote it out** when the council splits close to the middle: a runoff between the top two plans
  forces a choice without a king.
- **Ask a human** when a red line is at stake or no majority exists. A majority must never override
  a red line, because red lines encode harms (starvation, a bare granary, losing the land, an
  unsown city, a war with Lagash) that the others' scores would trade away.

## Level 3 criteria, and where to see each one

| Criterion | Where it shows |
|---|---|
| Genuine disagreement | Opening proposals differ by hundreds of acres and bushels; advisors answer each other by name; vetoes in the glass box |
| Deliberation mechanism | Sequential turns over SLIM (propose or back, vote, reply), with the stop rules and runoff above |
| Effect of new information | Scouts report true news mid-debate (locusts, rats, plague, prices). See the "before and after the news" table, the **Send a scout** buttons, and the counterfactual benchmark. |
| Comparison to a baseline | Evidence tab and `results/bench.md`: lone ruler, averaging, single-mandate dictators |
| Stop, keep disagreeing, or ask a human | Consensus, majority, runoff, and escalated outcomes, plus the deadlock dialog |

## Safe and observable: the glass box

The right-hand column traces the whole system as it thinks:

- every clerk message: SLIM topic, recipient, bytes, round-trip time
- for every advisor, every turn: its **private thought** (LLM stated reasoning or the policy's
  explanation), the forecast it used, its self vs group weight, its acceptance threshold, and a
  per-plan table of score, threshold, red lines hit, and vote, plus LLM latency and cost
- signature verification for every ballot, and alerts for rejected or missing ballots
- scout news and the exact belief update it caused

Advisors only hear each other's public **speeches**. The private thoughts are for humans. Click
an advisor card to filter the trace to that agent.

## Results

30 seeds, five-year reigns, same dice for every decider. Full table:
[`results/bench.md`](results/bench.md) (regenerate with `uv run sumer bench`). The Evidence tab in
the UI shows the same data.

| Decider | Mean score | Survived | Mean starved | Mean people | Fairness |
|---|---|---|---|---|---|
| Farmer alone | 1225.1 | 30/30 | 0 | 102.8 | 0.360 |
| **Council (7 agents, negotiated)** | 1216.6 | 30/30 | 0.4 | 102.5 | **0.386** |
| General alone | 1215.6 | 30/30 | 0.5 | 102.4 | 0.275 |
| Physician alone | 1158.0 | 30/30 | 0 | 100.1 | 0.257 |
| Lone ruler (equal weights, no debate) | 1157.6 | 30/30 | 2.5 | 99.8 | 0.375 |
| Builder alone | 829.8 | 30/30 | 15.0 | 84.5 | 0.296 |
| Average of selfish openings | -238.5 | 30/30 | 50.3 | 31.2 | 0.0 |
| Envoy alone | -1737.9 | 19/30 | 85.1 | 5.1 | 0.0 |
| Merchant alone | -1742.5 | 19/30 | 85.2 | 5.0 | 0.0 |
| Priest alone | -1800.1 | 19/30 | 85.1 | 5.1 | 0.0 |

Fairness is the average satisfaction of the worst-served advisor (0 to 1). These numbers come from
the offline policy minds (`LLM_MODE=mock`), so they are deterministic and reproducible.

- **The council beats the lone ruler on both counts.** It scores 1216.6 vs 1157.6 for one
  equal-weight ruler, starves fewer people (0.4 vs 2.5 per reign), and has the best fairness of
  any decider (0.386).
- **It does not beat the best single mandate on raw score.** The Farmer alone scores 1225.1,
  because the score rewards population, but it serves the other six advisors worse (fairness
  0.360). The council gives up 0.7% of score to protect minority mandates.
- **Negotiation beats naive aggregation.** Averaging each advisor's selfish plan scores -238.5.
- **New information changes decisions.** Rerunning each news year from the same state without the
  report changes the adopted plan in 59 of 140 years, and scouts raise the council's mean score
  from 1198.5 to 1216.6.
- **Outcomes over 150 council-years:** 0 consensus, 123 majority with recorded dissent, 6 runoffs,
  21 escalated to a human. Mean 2.9 rounds and 20 turns per year.
- **Single mandates fail.** The Envoy, Merchant, and Priest alone each lose 11 of 30 reigns.

Honest caveats about the policy minds:

- **Seven offline advisors almost never reach unanimity.** One of them (usually the Priest, who
  plans for a bad harvest) dissents from nearly every compromise, so most years end in a strong
  majority as soon as round 2 is complete. The LLM advisors reach consensus more often.
- **Runoffs come from 4-4 ties, not 3-of-7 splits.** Late in the meeting the policy advisors have
  conceded enough that the leading plan almost always has 4 or more accepts. In 200 seeds
  (`scripts/find_runoff_seed.py`) none of the runoffs came from a 3-of-7 leader.
- **Escalations are real red-line clashes.** In every escalation we inspected, no plan on the table
  crossed nobody's red line (typically the Priest's granary against the General's land in a
  locust year), so a vote could not settle it.

## Verified turns and decision evidence

Each response binds to its run, year, round, speaker, state, and exact request (including the
frozen runoff finalists). Invalid or missing turns are retried once for the addressed speaker;
a second failure records a technical failure. A deadlock requires an explicit human selection,
including an explicit choice of the safe plan. Model failures never become policy votes.

SQLite stores immutable proposals, signed turn receipts, requests, model usage, and annual
commitments. Before advancing, the engine reconstructs the standing table and independently
checks the seven-advisor consensus, majority, runoff, or human-resolution evidence. A unique
run/year transaction prevents duplicate delivery from advancing twice. Credentials live in
`.cache/identities`, separately from the shared transport secret; the journal defaults to
`.cache/runs.sqlite3`. Private thoughts remain available to humans without entering peers' briefs.

Checked-in benchmark results predate the corrected accounting and are historical until
regenerated. Paused deadlocks and technical failures do not receive completed-reign scores.

## Cost and offline safety

- Every LLM reply is cached in `.cache/llm/` by prompt hash. Rehearse a seed once and the demo
  replays it with no network.
- `LLM_BUDGET_USD` (default $3.00) is a hard cap tracked from the cost Bayleaf reports per call.
  Past it, advisors fall back to the offline policy.
- A full 5-year LLM game costs a few cents (up to 49 short LLM calls per year).

## Project layout

```
src/sumer/
  game.py          Hamurabi rules, seeded dice, ratings
  advisors.py      mandates, red lines, forecasts, scout reports, plain-English plans
  agent.py         one advisor's mind (policy + LLM) and its glass-box trace
  deliberation.py  the council protocol: turns, stop rules, runoff, escalation
  narrator.py      the plain-English summary of each year
  chamber.py       plays whole games
  baselines.py     lone ruler, averaging, dictators, lone LLM
  bench.py         evidence generation
  protocol.py      Brief / Ballot wire format, peer signing and request binding
  transport/       slim.py (AGNTCY) and inproc.py (tests)
  web.py           FastAPI + SSE chamber, human escalation
  voice.py         optional ElevenLabs voices (advisors and narrator)
web/               the chamber UI
scripts/           find_runoff_seed.py (demo seed search)
tests/             unit tests
```

## Saved runs, replay, and exports

The journal survives restart. Interrupted running or deadlocked negotiations are retained as
technical failures. Select a saved run in Setup to inspect the seven-advisor turns, scouts,
runoff, and year summaries without model or voice calls. Recorded replay is read-only;
**Show live run** returns to an active negotiation. Live choices and scouts identify that run.

**Export run** downloads `events.jsonl`, `decisions.csv`, `summary.json`, and `report.md`.
The files include signed turn evidence, dissent, human choices, yearly resources, recorded
random inputs, and reported model usage. Past random inputs are recorded after commitment.
