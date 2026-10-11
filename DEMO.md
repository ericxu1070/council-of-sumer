# Live demo script

Setup (before you present, ~2 min):

1. `uv run sumer up`, open http://localhost:8000, press **F** for full screen.
2. Optional: ☰ Setup to change agents, seed (default 3 is rehearsed), rounds, or pace.
3. Click anywhere once so the browser allows narration audio.

On stage (~4 min):

1. **Open on the council.** "Five AI agents, five clashing goals, one city, no king."
   The right column introduces each advisor's goal.
2. **Convene the council.** Each advisor speaks in turn; the lit line shows who is talking
   to whom over SLIM. Green ✓ / red ✕ badges show who backs the leading plan.
3. **The votes are in.** After each round the chamber asks for your move:
   - Press **1-6** to send a scout event (plague, rats, good/bad harvest, low/high land price).
     Every advisor votes again and the panel shows who changed their vote.
   - Press **Enter** to let the vote stand.
   - Do nothing and the vote stands after 12 s (hover the panel to stop the countdown;
     turn it off in Setup).
4. **Year result.** A card shows how the council decided, the adopted plan, and what happened
   to the city. The winning advisor states the plan in their own voice, then the narrator
   reads the year's headline number. Click the card to dismiss it early.
5. **Deadlock.** If the council cannot agree, pick a plan with **1-3** (or click it).
6. **Reign over.** The final card shows the verdict and how each year was decided.

Controls:

| Key | Button | What it does |
|---|---|---|
| Space | Pause / Resume | Holds the council and the narrator |
| M | 🔊 | Mutes all voices |
| H | Under the hood | Shows private reasoning, model traces, and SLIM messages |
| F | | Toggles full screen |
| Esc | | Closes cards and panels |
| | ☰ → Stop | Ends the current run |

Stop the server with `Ctrl+C`.

If something breaks on stage:

| You see | Do this |
|---|---|
| "Lost the connection… Reconnecting" | Wait; it retries every 1.5 s |
| "The council went quiet" | A model call failed (Wi-Fi or budget). Click **Convene**; turns rehearsed before replay from the cache |
| "The chamber restarted" | The server restarted. Click **Convene** |
| No voices | Click the page once (browsers block audio until a click), check 🔊 |
