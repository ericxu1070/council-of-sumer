# Council of Sumer

**Choose a council of AI advisors with competing goals to govern an ancient city, with no king to break ties.**

Built for the UCSC AI Hackathon @ SF Tech Week (Oct 10 to 11, 2026), Cisco Outshift track
"When AI Agents Disagree", **Level 3: Build a collective intelligence**.

We took *Hamurabi* (1968), the original city-management game: each year you decide how much
land to trade, how much grain to feed your people, and how much to plant, while harvests,
rats, and plague strike at random. Instead of one ruler, a configurable council of autonomous agents
decides over a five-year reign. Each one has a public mandate, and they argue
with each other, one speaker at a time, over **AGNTCY SLIM**.

The default council contains these seven roles:

| Advisor | Mandate |
|---|---|
| Voice of the Farmers | Grow the population. Nobody starves. |
| High Priest of Enlil | Keep the granaries full. Plans for a bad harvest. |
| General of the Walls | Hold and expand land. |
| Chief Merchant | Maximize wealth at market prices. |
| Canal Builder | Plant as many acres as we can, every year, and gain land and hands to sow more. |
| Royal Physician | Generous rations, food in store, no crowding to breed plague. |
| Envoy to Lagash | Peace with the neighbors: no land grabs, steady borders, modest trade. |

These mandates really conflict: the General and the Builder want land while the Envoy wants stable borders, the Physician's extra rations eat the Priest's reserve and the Builder's seed,
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

## Choose the voters

Open **Setup** before convening a new game. Choose **Number of agents** (1 to 21), then
populate each seat with a preset such as Farmer or Builder, or choose **Custom** and enter
a name and mandate. The LLM chooses proposals and votes from each agent's instructions
and the discussion. Custom agents do not need a voting profile. Repeated preset roles have
independent votes and numbered names such as Priest1, Priest2, and Builder1, Builder2.
A role with one voter keeps its name.

Every run saves its complete roster, so replay and exports retain custom names, mandates,
and voting rules. The default remains the original seven advisors. For a council of `n`
agents, a majority needs `floor(n / 2) + 1` votes; a strong majority needs the greater of
that count and `ceil(2n / 3)`. Runoff entry needs at least one vote and otherwise one fewer
than a majority. For seven agents these remain 4, 5, and 3 votes respectively; an even
split never passes as a majority.

The same setup is available through `POST /api/game`:

```json
{
  "seed": 7,
  "advisors": [
    {"id": "farmer"},
    {"id": "builder"},
    {
      "id": "custom-scribe",
      "short": "Scribe",
      "mandate": "Speak for the families and keep everyone fed."
    }
  ]
}
```

Omit `advisors` to use the default council. Empty councils, duplicate IDs, and custom
agents missing a name or mandate are rejected before creating a run.

## Architecture

```
                      +---------------------- AGNTCY SLIM node (:46357) ----------------------+
 Browser <--SSE--  Clerk (web.py) --A2A unicast, one Brief per turn-->  farmer priest general merchant
 (glass box,       relays + tallies     (only the speaker is asked)      builder healer diplomat
  narrator)        never chooses   <------------ one Ballot ------------  7 separate A2A agent processes
                                                                        (agent.py: LLM choices)
```

- **Advisors** (`agent.py`) are seven separate processes, each serving an A2A agent card over
  SLIM patterns (`transport/slim.py`, AGNTCY App SDK 0.5.5). `sumer up` starts one per advisor.
  Custom seats start additional A2A worker processes on demand and release them when the
  run ends. Every peer receives the run's roster with its brief.
- **The clerk** (`deliberation.py`) runs the meeting. For each turn it sends a `Brief` to the one
  advisor whose turn it is, over a SLIM point-to-point session to that advisor's own name (with a
  one-member group session as fallback), and collects its `Ballot`. It relays and applies a
  public rule. It never ranks plans by its own judgment, so there is no central decision-maker.
- **Ballots are validated** against the addressed advisor, run, state, and exact request
  (`protocol.py`). Invalid or stale responses are rejected and flagged in the glass box.
  This local demo relies on the SLIM transport identity; it does not provision signing keys.
- **Reasoning**: each advisor asks the LLM (`LLM_MODE=bayleaf`) to choose its proposal,
  accept votes, runoff vote, explanation, and speech from its mandate, city state, beliefs,
  and conversation. No deterministic policy, suggested plan, numeric score, threshold,
  or red-line veto guides or overrides those choices. Budgets and request bindings are
  validated. Model failures are retried once and then stop the run; mock policy mode was removed.
- **The game** (`game.py`) pre-rolls every die from the seed, so every decider faces exactly the
  same harvests, rats, and plagues.
- **The narrator** (`narrator.py`) closes each year with one or two plain-English sentences for a
  non-technical audience (built from templates, no LLM), read aloud by a deep ElevenLabs voice.
  The chamber waits for the narrator to finish before the next year's debate starts.

## The deliberation protocol

Each year is a real meeting, up to 6 rounds of turns. The counts below describe the default
seven-agent council; configured councils use the thresholds above:

1. **Turns, not polls.** The seven advisors speak one at a time in a fixed order, and the first
   seat moves one place every round. On its turn an advisor sees the table (every plan with its
   current support), everything said earlier this round, and the round before. It tables a
   proposal or backs a plan already on the table, chooses which plans to accept on the
   tabled plans, and answers the others by name in at most 25 words ("Builder, your canal plan
   starves the east quarter..."). A new proposal is tabled at once, so later speakers in the same
   round can accept it. A plan nobody backs any more is withdrawn.
2. **News arrives mid-debate.** Scout reports land at the start of round 2, so the opening
   positions and the reaction to the news are both on record.
3. **Negotiation.** The LLM decides when to compromise and which tradeoffs to accept.
   Recent outcomes and dissent are supplied as context; there is no coded concession schedule
   or automatic extra weight for an advisor.
4. **Stop rules**, checked after every turn
   - **Consensus**: all seven accept one plan. Adopt it.
   - **Strong majority**: once round 2 is complete, a plan with 5 of 7 accepts is adopted, and the dissent is recorded.
   - **Stalemate**: a whole round with no proposal change and no news. Go to the final rule.
5. **Final rule** (stalemate, or the last round ends)
   - **Majority**: the best plan has 4 of 7 accepts and no tie. Adopt it and record
     the dissent.
   - **Runoff**: the best plan has only 3 of 7 accepts, or the top two tie. The top two
     plans go head to head, and every advisor chooses A or B with a one-line speech.
     A plan with 4 of 7 runoff votes is adopted.
   - **Escalate**: under 3 accepts (deadlock), or a runoff without a majority. The reign
     pauses and the human sees each advisor's position and stated reasoning, then picks
     one of the top plans or the pre-committed safe plan.


### When consensus is not the right outcome

- **Stop and adopt** when everyone accepts.
- **Keep the disagreement** when a clear majority agrees.
  Forcing unanimity among seven would water the plan down. We adopt it, but the dissent stays on
  record and is available as context next year.
- **Vote it out** when the council splits close to the middle: a runoff between the top two plans
  forces a choice without a king.
- **Ask a human** when the council cannot form a majority.

## Level 3 criteria, and where to see each one

| Criterion | Where it shows |
|---|---|
| Genuine disagreement | Opening proposals differ by hundreds of acres and bushels; advisors answer each other by name; different accept votes in the glass box |
| Deliberation mechanism | Sequential turns over SLIM (propose or back, vote, reply), with the stop rules and runoff above |
| Effect of new information | Scouts report true news mid-debate (locusts, rats, plague, prices). See the "before and after the news" table, the **Send a scout** buttons, and the counterfactual benchmark. |
| Comparison to a baseline | Evidence tab and `results/bench.md`: lone ruler, averaging, single-mandate dictators |
| Stop, keep disagreeing, or ask a human | Consensus, majority, runoff, and escalated outcomes, plus the deadlock dialog |

## Safe and observable: the glass box

The right-hand column traces the whole system as it thinks:

- every clerk message: SLIM topic, recipient, bytes, round-trip time
- for every advisor, every turn: the LLM's **stated reasoning**, chosen proposal,
  accept or runoff votes, supplied beliefs and news, plus model latency and cost
- turn validation for every ballot, and alerts for rejected or missing ballots
- scout news and the exact belief update it caused

Advisors only hear each other's public **speeches**. The private thoughts are for humans. Click
an advisor card to filter the trace to that agent.

## Results

Checked-in [`results/bench.md`](results/bench.md) and `results/bench.json` are historical
results from the removed policy and veto rules. They do not measure the current LLM-only
council. Regenerate with `uv run sumer bench` using Bayleaf. Utility and satisfaction scores
are computed only for reports and offline baseline comparisons; they never enter advisor prompts.
Custom agents do not receive these preset-specific satisfaction scores.

## Verified turns and decision evidence

Each response binds to its run, year, round, speaker, state, and exact request (including the
frozen runoff finalists). Invalid or missing turns are retried once for the addressed speaker;
a second failure records a technical failure. A deadlock requires an explicit human selection,
including an explicit choice of the safe plan. Model failures never become policy votes.

SQLite stores immutable proposals, turn receipts, requests, model usage, and annual
commitments. Before advancing, the engine reconstructs the standing table and independently
checks the selected council's consensus, majority, runoff, or human-resolution evidence. A unique
run/year transaction prevents duplicate delivery from advancing twice. The journal defaults
to `.cache/runs.sqlite3`. Private thoughts remain available to humans without entering peers' briefs.

Checked-in benchmark results predate the corrected accounting and are historical until
regenerated. Paused deadlocks and technical failures do not receive completed-reign scores.

## Cost and replay

- Every LLM reply is cached in `.cache/llm/` by prompt hash. Use Saved runs to replay a recorded
  debate without network calls.
- `LLM_BUDGET_USD` (default $3.00) is a hard cap tracked from the cost Bayleaf reports per call.
  Past it, advisors record a technical failure after one retry; no deterministic fallback vote is generated.
- A full 5-year LLM game costs a few cents (up to 49 short LLM calls per year).

## Project layout

```
src/sumer/
  game.py          Hamurabi rules, seeded dice, ratings
  advisors.py      mandates, outcome forecasts, scout reports, plain-English plans
  agent.py         one advisor's LLM choices and its glass-box trace
  deliberation.py  the council protocol: turns, stop rules, runoff, escalation
  narrator.py      the plain-English summary of each year
  chamber.py       plays whole games
  baselines.py     lone ruler, averaging, dictators, lone LLM
  bench.py         evidence generation
  protocol.py      Brief / Ballot wire format, turn validation and request binding
  transport/       slim.py (AGNTCY) and inproc.py (tests)
  web.py           FastAPI + SSE chamber, human escalation
  voice.py         optional ElevenLabs voices (advisors and narrator)
web/               the chamber UI
scripts/           find_runoff_seed.py (demo seed search)
tests/             unit tests
```

## Saved runs, replay, and exports

The journal survives restart. Interrupted running or deadlocked negotiations are retained as
technical failures. Select a saved run in Setup to inspect the recorded council's turns, scouts,
runoff, and year summaries without model or voice calls. Recorded replay is read-only;
**Show live run** returns to an active negotiation. Live choices and scouts identify that run.

**Export run** downloads `events.jsonl`, `decisions.csv`, `summary.json`, and `report.md`.
The files include recorded turn evidence, dissent, human choices, yearly resources, recorded
random inputs, and reported model usage. Past random inputs are recorded after commitment.

## AGNTCY Observe and advisor records

The launcher also starts a pinned native OpenTelemetry Collector, with a checksum-verified
installation on first use. Observe SDK spans cover council turns, runoff votes, model
calls, transport, message verification, human resolution, and verified engine commitments.
W3C trace context travels in headers and request-bound messages. Collection errors retain the game
result and journal. Set `SUMER_OBSERVE=false` to disable the pipeline.

The collector writes OTLP JSONL under `.cache`; complete records are imported incrementally
into the immutable journal. **Year traces** and **Open trace** inspect imported spans under
the hood. Exports additionally contain `traces.jsonl`. The seven local OASF records describe
the existing advisor roles and sequential coordination; they are not directory publications.
