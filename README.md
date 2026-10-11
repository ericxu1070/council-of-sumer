# Council of Sumer

Four AI advisors with competing goals govern ancient Sumer (Hamurabi, 1968) by negotiating over AGNTCY SLIM, with no king to break ties. UCSC AI Hackathon, Cisco "When AI Agents Disagree" track, Level 3.

## Run

```bash
cp .env.example .env   # fill in BAYLEAF_API_KEY, set LLM_MODE=bayleaf
uv sync
uv run sumer up        # SLIM node + 4 advisor agents + web UI on :8000
uv run sumer bench     # council vs baselines, writes results/
```

Full writeup in progress.
