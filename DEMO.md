# Running the live demo

## Setup (~2 min, before judges arrive)

1. Start everything:
   ```bash
   cd ~/Projects/council-of-sumer && uv run sumer up
   ```
   Wait for `Council of Sumer: open http://localhost:8000`.
2. Open **http://localhost:8000** in a full-screen browser.
3. Check the header chips: **via AGNTCY SLIM** and **deepseek-v4.1-flash**.
4. In the left sidebar set **Seed TBD** (chosen after rehearsal), **Rounds 6**, **Scouts Report**,
   **Pace 1.0**. Seed, Rounds, and Scouts must match the rehearsal or the replay misses the cache.

## During the pitch (~3 min)

1. Click **Convene**. The seven advisors speak one at a time, about 1 s apart, and answer each
   other by name. A five-year reign takes a few minutes at pace 1.0.
2. Click any advisor's thinking to show its private reasoning and scores.
3. Flip **Glass box**: every thought expands and the SLIM wire messages appear (one unicast
   message per turn).
4. When the scout report arrives (start of round 2), point at how the next speakers change course.
5. At the end of each year the narrator reads a one-line summary. The next year starts when it
   finishes.
6. Placeholder: the runoff year and any deadlock year for the chosen seed go here (the seed is
   picked in a later step). On a deadlock, click **Safe fallback**.
7. At the end card, open the evidence view for the baseline comparison.

Stop with `Ctrl+C`.

## Notes

- **Pace slider**: drag to 0 to fast-forward, up to 3 or 4 to slow down while talking.
- **Off-script deadlock choice**: any other choice still works, but the years after it make live
  LLM calls (about 2 min and a few cents per year).
- **Narrator**: the year summaries are spoken by ElevenLabs (voice "Brian"; override with
  `ELEVENLABS_VOICE_NARRATOR`). Audio is cached in `.cache/voice/`, so a rehearsed seed replays
  offline.
- **No Wi-Fi needed**: the cached replay and SLIM both run locally.
- **Cache is local**: replies live in `.cache/llm/` (git-ignored). Demo from the laptop that ran the
  rehearsal, or copy that folder. To re-rehearse a seed, play it once with `LLM_MODE=bayleaf`.

## If something breaks

| Problem | Fix |
|---|---|
| `Address already in use` on start | `pkill -f "python -m sumer.cli"`, then start again |
| Header says "offline policy" | `.env` has `LLM_MODE=mock`; set it to `bayleaf` and restart |
| Years are slow (~2 min each) | Cache missed: check the seed, Rounds 6, Scouts Report |
| Anything else at the venue | `LLM_MODE=mock uv run sumer up`: same UI, no API needed |

Turn validation checks preserve the seven-advisor speaking order, red lines, runoff, scouts, and
narrator. Invalid responses receive one retry for that speaker. A second failure is recorded
as technical failure. Deadlock without a human choice remains paused; selecting the safe plan
is recorded as an explicit human resolution. The engine verifies the journal before each year
advances.

Use **Saved runs** in Setup to replay the recorded seven-advisor debate. Replay is read-only
and silent. **Export run** downloads the event journal, annual decision table, summary, and
report. **Show live run** returns to an active game. On restart, interrupted runs retain their
evidence and are marked as technical failures.

Under the hood, **Year traces** and each turn's **Open trace** show imported Observe spans.
Allow a few seconds for the local collector to flush. Exports include traces, and a collection
error leaves the authoritative game result intact. The launcher serves all seven advisors
and starts the pinned local collector.
