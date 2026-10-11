# Council of Sumer

**Five AI advisors with clashing goals must run an ancient city together. There is no king to break ties.**

Built for the UCSC AI Hackathon @ SF Tech Week (Oct 10-11, 2026), Cisco Outshift track
"When AI Agents Disagree", **Level 3: Build a collective intelligence**.

We took *Hamurabi* (1968), the original city-management game, and replaced the ruler with a
council. Each year the city must decide how much land to trade, how much grain to feed its
people, and how much to plant. Five autonomous agents, each its own process, argue it out
one speaker at a time over **AGNTCY SLIM**, then vote. When they deadlock, you decide.

![The council debating over SLIM](docs/debate.jpg)

Every advisor plays to maximize the city's final score over the whole reign, and each adds
its own preference on top:

| Advisor | Extra preference | Default bias |
|---|---|---|
| Provider | Keep everyone fed. | 0.25 |
| Farmer | Plant as much grain as possible. | 0.25 |
| Merchant | Own the most land at the end. | 0.25 |
| Priest | Keep the most grain in reserve. | 0.25 |
| Villager | None: play for the score alone. | 0 |

Score: `10 * population + (grain + 20 * acres) / 100 - 15 * starved_total - (2000 if overthrown else 0)`.
These preferences collide: grain spent on people is not spent on seed or land.

**Bias** (0 to 1, set per seat in Setup or the API) says how hard an advisor resists
compromise. 0 bargains freely; 1 holds out. The LLM interprets it; no code enforces a veto.

## Quick start

```bash
cp .env.example .env      # set BAYLEAF_API_KEY (and optionally ELEVENLABS_API_KEY)
uv sync
uv run sumer up           # SLIM node + 5 advisor agents + web chamber
# open http://localhost:8000
```

No Docker needed: the SLIM data-plane node runs inside Python via `slim-bindings`.
See [DEMO.md](DEMO.md) for the live demo script.

```bash
uv run pytest                                      # unit tests
uv run python scripts/fake_chamber.py              # scripted UI preview, no LLM (:8010)
uv run python scripts/rehearse.py 3 11 --batch 3   # rehearse candidate demo seeds
uv run sumer bench --seeds 30                      # council vs baselines
```

## How a year works

1. **Turns, not polls.** Advisors speak in a rotating order. Each sees every plan on the table
   with its support and what was said this round and last. It tables a plan or backs one, may
   approve several, and answers others by name in at most 25 words.
2. **You can shake the vote.** After every full round the council waits for you: send a scout
   event (plague, rats, good or bad harvest, low or high land price) or let the vote stand.
   An event changes the real game conditions and every advisor votes again. The chamber shows
   who changed their vote.

   ![Your move: send a scout or let the vote stand](docs/scout.jpg)
3. **Adoption rules**, checked once you let the vote stand:
   - **Consensus**: everyone backs one plan.
   - **Strong majority**: from round 2, a plan with 4 of 5 votes passes; dissent is recorded.
   - **Majority**: when talk stalls or rounds run out, 3 of 5 with no tie passes.
   - **Runoff**: only 2 of 5, or a tie at the top. Every advisor picks A or B.
   - **Ask a human**: fewer than 2 votes, or a runoff with no majority. You pick a top plan
     or the pre-agreed safe plan.

For a council of `n`: majority is `floor(n/2) + 1`, strong majority is the larger of that and
`ceil(2n/3)`, runoff needs one fewer than a majority.

### When consensus is not the right outcome

- **Keep the disagreement** when a clear majority agrees. Forcing unanimity waters the plan
  down; the dissent stays on record as context for next year.
- **Vote it out** when the council splits near the middle.
- **Ask a human** when no majority can form.

## Level 3 criteria

| Criterion | Where it shows |
|---|---|
| Genuine disagreement | Opening plans differ by hundreds of acres and bushels; advisors rebut each other by name |
| Deliberation mechanism | Sequential turns over SLIM (propose or back, vote, reply), stop rules, runoff |
| Effect of new information | Scout events trigger a re-vote with a before/after comparison |
| Comparison to a baseline | `uv run sumer bench`: lone ruler, averaging, single-mandate dictators |
| Stop, keep disagreeing, or ask a human | Consensus, majority, runoff, and the deadlock dialog |

## Architecture

```
                      +---------------------- AGNTCY SLIM node (:46357) ----------------------+
 Browser <--SSE--  Clerk (web.py) --A2A unicast, one Brief per turn-->  provider farmer merchant
 (chamber,         relays + tallies     (only the speaker is asked)      priest villager
  narrator)        never chooses   <------------ one Ballot ------------  separate A2A agent processes
```

- **Advisors** (`agent.py`) are separate processes serving A2A agent cards over SLIM
  (`transport/slim.py`, AGNTCY App SDK). Custom seats start extra workers on demand.
- **The clerk** (`deliberation.py`) sends a `Brief` to the one advisor whose turn it is and
  collects its `Ballot`. It applies the public rules and never ranks plans itself.
- **Reasoning**: each advisor's LLM picks its proposal, votes, and speech from its mandate,
  bias, the score formula, the city state, and the debate. No coded policy or veto steers it.
  Rejected budgets come back with the shortfall so the advisor can fix them.
- **Skipped turns**: a failed turn is retried once, then that advisor skips it and the council
  continues. A skip casts no vote and does not lower the majority thresholds.
- **Verified turns**: every ballot binds to its run, year, round, speaker, state, and request
  (`protocol.py`). SQLite stores proposals, receipts, skips, and commitments; the engine
  re-checks the evidence for each decision before advancing.
- **The game** (`game.py`) pre-rolls every die from the seed, so every decider faces the same luck.
- **The narrator** (`narrator.py`) closes each year in one or two plain sentences, read aloud
  by ElevenLabs when a key is set.
- **Observability**: `sumer up` also starts a pinned OpenTelemetry Collector for AGNTCY Observe
  spans (turns, model calls, transport, verification). `SUMER_OBSERVE=false` disables it.

**Under the hood** in the chamber reveals each advisor's private reasoning, model traces, and
SLIM wire messages. Advisors only ever hear each other's public speeches.

## Custom councils

Open Setup (☰) before convening. Choose 1 to 21 agents; each seat is a preset or a custom agent
with your own name and mandate. Repeated presets vote independently (Provider1, Provider2).
The same setup works over the API:

```json
POST /api/game
{"seed": 7, "advisors": [{"id": "provider"}, {"id": "farmer"},
  {"id": "custom-scribe", "short": "Scribe", "mandate": "Keep everyone fed."}]}
```

## Game rules

Classic BASIC [Hamurabi](https://github.com/coding-horror/basic-computer-games/blob/main/43_Hammurabi/hammurabi.bas):
trade land (17-26 bushels/acre), feed people (20 bushels each), plant (1 bushel per 2 acres,
10 acres per worker). Rats, harvests (1-5 bushels/acre), immigration, and plague follow.
Over 45% starvation in a year overthrows the council.

## Cost and replay

- Every LLM reply is cached in `.cache/llm/` by prompt hash, so a rehearsed seed replays offline.
  A different scout event changes the prompt and adds calls.
- `LLM_BUDGET_USD` (default $3.00) is a hard cap. Past it, advisors skip their turns; there is
  no fallback vote. `scripts/rehearse.py` marks a seed UNUSABLE if any turn was skipped.
- The default council uses up to 30 LLM calls per year before retries.
- `.env.example` lists every setting: `REIGN_YEARS`, `MAX_ROUNDS`, `PACE_S`, voices.

Checked-in `results/bench.*` predate the current rules and are historical; regenerate with
`uv run sumer bench`. The chamber shows its Evidence panel only for current results.

## Project layout

```
src/sumer/
  game.py          Hamurabi rules, seeded dice, ratings
  advisors.py      mandates and plain-English plans
  agent.py         one advisor's LLM choices and trace
  deliberation.py  the council protocol: turns, stop rules, runoff, escalation
  scouts.py        user-chosen scout events
  narrator.py      plain-English year summaries
  protocol.py      Brief / Ballot wire format and request binding
  transport/       slim.py (AGNTCY) and inproc.py (tests)
  web.py           FastAPI + SSE chamber
web/               the chamber UI
scripts/           fake_chamber.py (UI preview), rehearse.py (live rehearsal)
tests/             unit tests
```

## City map

The council sits in the square of a bird's-eye map of the city. Fields, grain sacks, and
homes track the city state (one plot = 100 acres, one sack = 200 bushels, one home = 10
people), with exact totals below. Green lines flash when an advisor backs another's plan;
red dashed lines when it leaves out a plan it addressed.

Optional browser smoke check: `scripts/check_city_ui.cjs` (needs Playwright on `NODE_PATH`;
see the script header).
