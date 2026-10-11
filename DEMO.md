# Running the live demo

## Setup (~2 min, before judges arrive)

1. Start everything:
   ```bash
   cd ~/Projects/council-of-sumer && uv run sumer up
   ```
   Wait for `Council of Sumer: open http://localhost:8000`.
2. Open **http://localhost:8000** in a full-screen browser and turn the laptop volume up (the
   narrator speaks).
3. Open the setup drawer (**≡**, top right) and set **Seed TBD** (chosen after rehearsal),
   **Rounds per year 5**, **Scout reports On**, **Pace 0.8 s**. Seed, Rounds, and Scouts must match
   the rehearsal or the replay misses the cache. Use **Saved runs** for a recorded rehearsal
   without new model calls.
4. **Do not click the "Send a scout" buttons during the demo.** An extra report changes what the
   advisors hear, so every later turn becomes a live LLM call (slower, a few cents).

## During the pitch

1. Click **Convene the council**. The five default advisors speak one at a time, 0.8 s apart, and
   answer each other by name.
2. Click any message's 💬 to show that advisor's stated reasoning.
3. Flip **Under the hood**: every thought expands and the SLIM wire messages appear (one unicast
   message per turn).
4. When the scout report arrives (start of round 2), point at how the next speakers change course.
5. At the end of each year the narrator reads a one-line summary. The next year starts when it
   finishes.
6. Placeholder: the runoff year and any deadlock year for the chosen seed go here (the seed is
   picked after a rehearsal). On a deadlock, click **Play it safe**.
7. At the end card, click **People** on the right to show the growth graph, then open
   **Evidence** for the baseline comparison.

Stop with `Ctrl+C`.

## Notes

- **Pace**: 0.8 s per message. Under the earlier bar rules a five-year reign took about 2:45; re-time
  it after rehearsing the new seed. Drag to 0 to fast-forward a year while you talk; the replay is
  identical at any pace.
- **Narrator**: ElevenLabs voice "Brian" (override with `ELEVENLABS_VOICE_NARRATOR`). The
  🔊 button mutes it.
- **Rounds**: the drawer defaults to `MAX_ROUNDS` from `.env` (5). Leave it at 5.
- **No Wi-Fi needed**: the cached replay, the cached narration, and SLIM all run locally.
- **Cache is local**: replies live in `.cache/llm/` (git-ignored). Demo from the laptop that ran the
  rehearsal, or copy that folder and `.cache/voice/`.
- **Rehearse**: `uv run python scripts/rehearse.py <seeds...> --rounds 5` plays each seed with the
  live LLM and prints, per year, the outcome, runoff trigger, rounds and turns. Pick a seed with a
  3-of-7 runoff, then rehearse it again: it must print `calls 0 live` and `replayable`. If it
  prints `UNUSABLE`, a live call failed; run it again.

## If something breaks

| Problem | Fix |
|---|---|
| `Address already in use` on start | `pkill -f "python -m sumer.cli"`, then start again |
| Header says "LLM unavailable" | Set `LLM_MODE=bayleaf`, check credentials, and restart |
| Turns arrive slowly (seconds each), spend rises | Cache missed: check the seed, Rounds 5, Scouts On, and that no scout button was clicked |
| No narrator audio | Click anywhere once (browsers block audio until a click); check `.cache/voice/` was copied |
| No API connection at the venue | Replay a recorded debate from **Saved runs** |

Turn validation checks preserve the five-advisor speaking order, legal budgets, runoff, scouts, and
narrator. Invalid responses receive one retry for that speaker. A second failure is recorded
as technical failure. Deadlock without a human choice remains paused; selecting the safe plan
is recorded as an explicit human resolution. The engine verifies the journal before each year
advances.

Use **Saved runs** in Setup to replay the recorded five-advisor debate. Replay is read-only
and silent. **Export run** downloads the event journal, annual decision table, summary, and
report. **Show live run** returns to an active game. On restart, interrupted runs retain their
evidence and are marked as technical failures.

Under the hood, **Year traces** and each turn's **Open trace** show imported Observe spans.
Allow a few seconds for the local collector to flush. Exports include traces, and a collection
error leaves the authoritative game result intact. The launcher serves all five default advisors
and starts the pinned local collector.
