# Council of Sumer demo

## Prepare

```bash
uv sync
cp .env.example .env
# Fill in BAYLEAF_API_KEY for live mode; use LLM_MODE=mock for an offline rehearsal.
uv run pytest
uv run sumer up
```

Open http://localhost:8000. The launcher starts the collector, SLIM node, four advisors,
and web chamber. The first run downloads and checksum-verifies the native collector.
Keep the existing Bayleaf model and spend settings. There is no automatic offline fallback
when a live model call fails.

## Show the negotiation

1. Select seed 7 and convene. The current land quote is visible in the city report.
2. Show all four independent openings, including the assumed quote and arithmetic.
3. Follow the separate objection and revision phases. Expand **Glass box** to see each
   model's submitted explanations, assumptions, and expected effects.
4. Show a frozen candidate's four explicit YES/NO ballots and reasons. Three YES adopt as
   soon as all four valid ballots are received. A NO remains on the record.
5. If the negotiation limit is reached, show the failed ballots and all four latest valid
   proposals. Choose one to continue. Its outcome is **human resolution**, and the run is
   marked human-assisted. A deadlock is not guaranteed for a particular seed or live model.
6. If a technical failure occurs, show the recorded first failure and retry, then the
   stopped technical-failure status. The UI never presents that as a political deadlock.

The main accounting rules are whole-number budgets, rejection of overspending, rats before
the new harvest, immigration despite partial starvation, and floor-rounded plague survivors.
Future events remain unknown to the advisors; no scouting controls disclose them.

## Show evidence

- Use year channels to inspect proposals, objections, completed failed ballots, dissent, and
  the committed year's harvest, rats, starvation, immigration, and plague deaths.
- **Year traces** shows imported Observe spans. Expand an advisor's explanation and use
  **Open trace** to follow its trace ID. Allow a few seconds for collector flushing.
- **Export run** saves events, decisions, summary, traces, and a Markdown report in one ZIP.
- Select a saved run to replay its recorded messages. Replay makes no model or voice calls
  and cannot resolve a live human choice. **Show live run** returns to an active negotiation.
- Restart the server and show that saved runs remain available. An interrupted run is
  retained with technical-failure status.

The existing offline benchmark command is `uv run sumer bench --seeds 30`. Historical
checked-in results are labeled as such until regenerated. Deadlocks remain paused in a
benchmark without a human; do not describe them as completed autonomous reigns.

## Verified implementation checks

The automated suite checks integer accounting, invalid budget rejection, three-of-four
adoption with all four ballots, signatures and identity, stale/wrong candidate rejection,
one retry, genuine deadlock selection, certificate verification, exactly-once commitment,
durable replay, exports, and incremental trace import. Mock mode exercises the transport
without requiring paid model calls.
