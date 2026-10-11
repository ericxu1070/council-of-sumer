# Council of Sumer

**Four AI advisors with competing goals govern an ancient city, with no king to break ties.**

Built for the UCSC AI Hackathon @ SF Tech Week (Oct 10 to 11, 2026), Cisco Outshift track
"When AI Agents Disagree", Level 3: Build a collective intelligence.

The Farmer, Priest, General, and Merchant negotiate annual land, food, and planting budgets
for Hamurabi. Each has its existing public mandate and concerns. In live mode, the model
makes the proposals, objections, revisions, and explicit YES/NO decisions.

## Hamurabi game rules

Actions are whole numbers. The engine rejects an invalid budget without changing its amounts.
Trading happens first, followed by feeding (20 bushels/person) and planting (floor(acres/2)
bushels of seed, at most ten acres/person). Rats consume remaining stores before the new
harvest is added. Immigration uses the starting population, including years with some
starvation. More than 45% starvation ends the reign before arrivals or plague; plague leaves
floor(population/2) survivors. The tenth year's arrivals and plague apply before the final
rating. A depopulated city also ends the reign. Average annual starvation includes zero years.

These mechanics follow [Ahl's classic BASIC Hamurabi](https://github.com/coding-horror/basic-computer-games/blob/main/43_Hammurabi/hammurabi.bas).
Seed costs retain its floor rounding, including one planted acre requiring zero bushels.

## Quick start

```bash
cp .env.example .env
# Keep the configured Bayleaf endpoint/model; fill in BAYLEAF_API_KEY for live advisors.
uv sync
uv run sumer up
# Open http://localhost:8000
```

The launcher starts the native SLIM node, four A2A advisor processes, the web chamber, and
a local OpenTelemetry Collector. The first launch downloads Collector Contrib 0.162.0 from
its official release and verifies the published SHA-256 checksum. No Docker is required.

For an offline rehearsal, set `LLM_MODE=mock`. This is an explicitly selected fixture mode.
Live model failures never switch to it. The existing model, request settings, prompt cache,
spend log, and usage display remain configured as before. Saved-run replay reads the journal
without making model or voice calls, independent of the prompt cache.

```bash
uv run pytest
uv run ruff check src tests
uv run sumer bench --seeds 30 --transport inproc
uv run sumer collector --install
# Separate processes: sumer collector, sumer node, sumer agent <id>, sumer web
```

See [DEMO.md](DEMO.md) for the rehearsal and live demo steps.

## Council decision rules

Each year follows explicit phases:

1. Publish the current city report, including the current market quote.
2. All four advisors independently submit provisional budgets, the quote they used,
   their arithmetic, assumptions, and expected effects. No opening is disclosed to another
   advisor before all four valid openings arrive.
3. Exchange objections, then collect four revisions that cite the recorded objections
   or failed ballots they address.
4. Freeze each revised candidate. In advisor order, collect one explicit YES or NO with a
   reason from every advisor for that exact candidate.
5. After all four valid ballots arrive, three or four YES votes adopt immediately.
   Preserve dissent and every completed failed ballot.
6. If all candidates fail, repeat objections and revisions using the transcript, up to
   `MAX_ROUNDS` completed cycles.
7. If that limit is exhausted, pause for a user to choose among all four latest valid
   proposals. Record a human resolution and mark the run human-assisted.

There is no safe-budget adoption or application-enforced veto. Concerns guide each
advisor's own vote. The application supplies factual game accounting, validity checks,
message verification, and state transitions. Live prompts include the main rules, all
mandates, current state, completed historical ledger, and negotiation transcript; they
contain no computed scores, compromise budgets, concession weights, or future-event reports.

Missing, malformed, invalid, or transport-failed responses get one retry for the affected
peers, with a fresh request ID and correction reason. A second failure stops the run with
`technical_failure`. A missing voter cannot become a political deadlock.
Live replies must contain a complete JSON object; truncated decisions are rejected. Signed
model failures retain reported usage and cost, including empty or malformed paid replies.

## Transport and commit verification

The existing AGNTCY App SDK 0.5.5 serves A2A agent cards over SLIM. Addressed concurrent
SLIM sessions retain the requested peer separately from the response's untrusted sender
label. Each peer signs replies with a distinct Ed25519 credential provisioned under
`.cache/identities`; the shared SLIM secret is not a ballot signing key.

Replies bind to the run, year, cycle, phase, state hash, request ID, and candidate hash.
Unknown senders, duplicates, altered signatures, stale responses, and wrong candidates
are rejected. A passing certificate contains all four signed ballots, including dissent.
The engine independently verifies the certificate and its immutable stored records before
committing. SQLite checks the previous state and inserts a unique run/year commitment in
one transaction, preventing duplicate delivery from advancing the game twice.

These are local development credentials. The four processes share a machine and file
permissions; this does not claim isolated production identities or remote attestation.

## Persistence, replay, and exports

SQLite at `.cache/runs.sqlite3` stores immutable run manifests, append-only events,
proposals, ballots, year results, human resolutions, model usage, and imported spans.
Valid ballots are stored as they arrive, even if another voter ultimately fails. Manifests
include the application version as well as the rule and dependency versions. Stopped runs
retain their final recorded resources and mortality in the summary export.
Completed years record their random inputs for audit, without disclosing future rolls
to advisors. A server restart retains history and marks an interrupted live run as a
technical failure.

Select a saved run in the UI to replay it. Year channels navigate the recorded report,
proposal exchange, ballots, and outcome. Replay is read-only. Live selection includes
the run ID and accepts only a pending latest proposal.

**Export run** downloads a ZIP containing:

- `events.jsonl`: ordered events, signed replies, candidates, ballots, and random inputs
- `decisions.csv`: annual budgets, votes, dissent, resources, and deaths
- `summary.json`: manifest, status, human assistance, results, and recorded model usage
- `traces.jsonl`: imported Observe spans
- `report.md`: manifest, decisions, failed ballots, human resolutions, and failures

The original benchmark command remains available. Offline scores and fairness are computed
after a run; they do not steer live decisions. Paused deadlocks and technical failures are
counted separately and do not receive completed-reign scores. The checked-in older benchmark
is historical evidence, and the UI labels it accordingly until regenerated.

## AGNTCY Observe and OASF

The pipeline is AGNTCY Observe SDK 1.0.41 → OTLP/HTTP → local native Collector →
uncompressed OTLP JSONL → incremental SQLite import → the run/year trace viewer and exports.
The collector listens on loopback ports 4317/4318. It handles traces, metrics, and logs in
separate files under `.cache`. SDK telemetry and synchronization are disabled.

Explicit spans cover years, proposals, objections, revisions, ballots, transport send/receive,
agent decisions, model calls, budget validation, message and certificate verification,
human resolution, technical failure, and engine commits. W3C `traceparent` travels in both
transport headers and signed phase messages. Spans carry run, year, phase, cycle, peer, and
candidate identifiers where relevant.
The UI links advisor explanations to their trace IDs and can show a year's imported spans.
The collector may need a few seconds to flush before an export includes the latest spans.
Collection errors are recorded separately and preserve the game result and decision journal.

The four [OASF records](oasf) describe the existing advisor roles using the coordination and
multi-agent planning taxonomy. They complement the runtime A2A cards and are not a claim of
publication to an external directory.

References: [AGNTCY Observe SDK](https://docs.agntcy.org/obs-and-eval/observe-sdk/),
[OASF record guide](https://docs.agntcy.org/oasf/agent-record-guide/), and the
[Collector file exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/exporter/fileexporter).

## Project layout

- `game.py`: main game rules and seeded dice
- `agent.py`: phase-specific live model decisions and explicit offline fixtures
- `advisors.py`, `baselines.py`, `bench.py`: mandates and offline comparison policies
- `protocol.py`, `deliberation.py`, `engine.py`: signed phases, voting, and verified commitment
- `storage.py`: immutable journal, replay, trace import, and exports
- `observability.py`, `collector.py`, `observability/collector.yaml`: local Observe pipeline
- `transport/`: A2A/SLIM and in-process test transport
- `web.py`, `web/index.html`: live chamber, human selection, saved replay, and traces
