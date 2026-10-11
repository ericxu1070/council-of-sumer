# Running the live demo

## Setup (~2 min, before judges arrive)

1. Start everything:
   ```bash
   cd ~/Projects/council-of-sumer && uv run sumer up
   ```
   Wait for `Council of Sumer: open http://localhost:8000`.
2. Open **http://localhost:8000** in a full-screen browser.
3. Check the header chips: **via AGNTCY SLIM** and **deepseek-v4.1-flash**.
4. In the left sidebar set **Seed 7**, **Rounds 5**, **Scouts Report**, **Pace ~1.2**.
   Seed, Rounds, and Scouts must match the rehearsal or the replay misses the cache.

## During the pitch (~3 min)

1. Click **Convene**. Messages and polls stream in, about 6 s per year at pace 1.2.
2. Click any **💭 Thinking** bar to show one advisor's private reasoning and scores.
3. Flip **Glass box** (top right): every thought expands and the SLIM wire messages appear.
4. Year 1 scout report (locusts): point at how the next round's proposals shift.
5. **Years 9 and 10** deadlock and the red bar appears. Click **🛟 Safe fallback** both times.
6. At the end card, open the **📊 evidence** channel for the baseline comparison.

Stop with `Ctrl+C`.

## Notes

- **Pace slider**: drag to 0 to fast-forward, up to 3 or 4 to slow down while talking.
- **Off-script deadlock choice**: any other choice still works, but the years after it make live
  LLM calls (about 1.5 min and a few cents per year).
- **No Wi-Fi needed**: the cached replay and SLIM both run locally.
- **Cache is local**: replies live in `.cache/llm/` (git-ignored). Demo from the laptop that ran the
  rehearsal, or copy that folder. To re-rehearse a seed, play it once with `LLM_MODE=bayleaf`.

## If something breaks

| Problem | Fix |
|---|---|
| `Address already in use` on start | `pkill -f "python -m sumer.cli"`, then start again |
| Header says "offline policy" | `.env` has `LLM_MODE=mock`; set it to `bayleaf` and restart |
| Years are slow (~1.5 min each) | Cache missed: check Seed 7, Rounds 5, Scouts Report |
| Anything else at the venue | `LLM_MODE=mock uv run sumer up`: same UI, no API needed |
