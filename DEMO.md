# Running the live demo

## Setup (~2 min, before judges arrive)

1. Start everything:
   ```bash
   cd ~/Projects/council-of-sumer && uv run sumer up
   ```
   Wait for `Council of Sumer: open http://localhost:8000`.
2. Open **http://localhost:8000** in a full-screen browser and turn the laptop volume up (the
   narrator speaks).
3. Open the setup drawer (**≡**, top right) and check: **Seed 3**, **Rounds per year 5**,
   **Scout reports On**, **Pace 0.8 s**. These are the defaults. Seed, Rounds, and Scouts must match
   the rehearsal or the replay misses the cache.
4. **Do not click the "Send a scout" buttons during the demo.** An extra report changes what the
   advisors hear, so every later turn becomes a live LLM call (slower, a few cents) and the story
   below no longer applies.

Seed 3 was rehearsed with the live LLM and replays from `.cache/llm/` with no API calls (verified
over SLIM in the browser: 147 of 147 turns from cache, $0.00 new spend). The narrator audio for
all five years is cached in `.cache/voice/`.

## What happens in seed 3

| Year | News (start of round 2) | Outcome | Rounds | Turns | Adopted |
|---|---|---|---|---|---|
| 1 | Rich flood | Majority 5 of 7 | 3 | 21 | Farmer: keep land, feed everyone, plant every field |
| 2 | Rich flood | Majority 4 of 7 | 5 | 35 | General: buy land, plant every field |
| 3 | Land price rising | **Runoff, 3 of 7** | 5 | 42 | Envoy's plan wins the runoff 4 to 3 |
| 4 | Rich flood | Majority 4 of 7 | 3 | 21 | Builder: buy 55 acres, plant every field |
| 5 | Locusts | Majority 4 of 7 | 4 | 28 | Builder: buy 58 acres, plant every field |

No year escalates, so there is no "You decide" dialog. Nobody starves; the city grows from 100 to
136 people.

## Talk track (~2 min 45 s at Pace 0.8)

Messages land 0.8 s apart, one speaker at a time. After each year the narrator (Brian, about 7 s) reads
a one-line summary, and the next year starts when it finishes. Times below are measured.

**0:00 Click "Convene the council".** "Seven AI advisors run an ancient city with no king. Each
has a public goal and a red line. They talk one at a time over AGNTCY SLIM, one signed message per
turn, and they answer each other by name."

**Year 1 (0:00 to 0:25).** Point at the Farmer and the Priest in round 1: they open on opposite
plans. "Each advisor has a bar: the least it will accept for its own goal. The bar drops every
round and is enforced in code, like the red line, so nobody caves in the first exchange." The
Farmer wins 5 of 7. Let the narrator read the summary.

**Year 2 (0:25 to 1:01).** "Now the General wants land." Click one message's 💬 to show that
advisor's private reasoning. Flip **Under the hood** for a moment to show the SLIM wire messages,
then flip it back. The debate runs all five rounds; the General carries 4 of 7.

**Year 3 (1:01 to 1:47): the runoff.** This is the moment to point at.
- The General's plan to buy land leads with only **3 of 7**. Nobody has a majority, so the clerk
  calls a runoff between the top two plans.
- "No king, no tiebreaker. Every advisor votes again, one at a time, A or B."
- The Envoy's plan wins **4 to 3**: the runoff overturned the plurality leader. The losers are
  recorded as dissent, and dissent gets double weight next year.

**Year 4 (1:47 to 2:12).** "Each advisor stands behind one plan and may second only one other, so
support means something." The Builder wins 4 of 7.

**Year 5 (2:12 to 2:35).** Locusts are sighted at round 2: point at how the next speakers react.
The Builder still carries 4 of 7.

**End card (~2:43).** "Five years, nobody starved, 136 people." Click **People** on the right to
show the growth graph over the years. If there is time, open **Evidence** for the baseline
comparison: the council beats a lone equal-weight ruler and has the best fairness of any decider.

Stop with `Ctrl+C`.

## Notes

- **Pace**: 0.8 lands the demo at about 2:45, leaving ~15 s for an intro; 1.0 runs about 3:15. Drag to 0 to fast-forward a year
  while you talk; the replay is identical at any pace.
- **Narrator**: ElevenLabs voice "Brian" (override with `ELEVENLABS_VOICE_NARRATOR`). The
  🔊 button mutes it.
- **Rounds**: the drawer defaults to `MAX_ROUNDS` from `.env` (5). Leave it at 5.
- **No Wi-Fi needed**: the cached replay, the cached narration, and SLIM all run locally.
- **Cache is local**: replies live in `.cache/llm/` (git-ignored). Demo from the laptop that ran the
  rehearsal, or copy that folder and `.cache/voice/`.
- **Re-rehearse**: `uv run python scripts/rehearse.py 3 --rounds 5`. It must print `calls 0 live`
  and `replayable`. If it prints `UNUSABLE`, a live call failed; run it again.
- **Backup seed**: seed 5 (also rehearsed and cached) has a 3-of-7 runoff in year 3 and one
  escalation in year 2. If you use it, click **Play it safe** in the "You decide" dialog; the
  rehearsal took that choice, and any other choice makes live LLM calls for the later years. Its
  narration is not cached yet: play it once at any pace with Wi-Fi on.

## If something breaks

| Problem | Fix |
|---|---|
| `Address already in use` on start | `pkill -f "python -m sumer.cli"`, then start again |
| Header says "offline policy" | `.env` has `LLM_MODE=mock`; set it to `bayleaf` and restart |
| Turns arrive slowly (seconds each), spend rises | Cache missed: check Seed 3, Rounds 5, Scouts On, and that no scout button was clicked |
| No narrator audio | Click anywhere once (browsers block audio until a click); check `.cache/voice/` was copied |
| Anything else at the venue | `LLM_MODE=mock uv run sumer up`: same UI, no API needed |
