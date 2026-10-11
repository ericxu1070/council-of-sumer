# Running the live demo

1. Start the council with `uv run sumer up` and open http://localhost:8000.
2. Open Setup, choose the number of agents and their mandates, then choose seed, rounds,
   and pace. Click **Convene**.
3. Each agent proposes its own plan or backs an existing plan, and can support several plans
   that are close to its goals so the council can pass a compromise.
4. After the full round, the votes appear beside **Send a scout, or keep this vote?**
5. Choose an event: plague, rats, good harvest, bad harvest, low land price, or high land price.
   The event changes the actual game conditions. Harvest, plague, and rats affect this year;
   land prices affect next year. Every agent then casts a fresh vote.
6. Show the **Before the scout / After the scout** comparison. You can introduce another
   event after the re-vote, or choose **Skip** to continue with the current result.
7. Use **Pause** in the header to hold the council and narration, then **Resume** to continue.
   Use **Stop** next to **Convene** in Setup to end the current run.
8. If the council deadlocks, choose a budget in the human decision dialog.

The narrator reads each year's result. The speaker icon mutes narration, and **Under the hood**
shows agents' private reasoning and transport messages. Setup unlocks after the reign ends.
The journal remains on disk, while saved-run, export, and trace controls are absent from the UI.
Stop the local server with `Ctrl+C` when finished.
