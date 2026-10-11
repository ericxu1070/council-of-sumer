# Benchmark (16 seeds, transport inproc, reasoning openrouter:deepseek/deepseek-v4.1-flash)

| policy | score | survived | starved | population | fairness |
|---|---|---|---|---|---|
| Merchant alone | 1221.1 | 16/16 | 0 | 99.8 | 0.999 |
| Farmer alone | 1214.3 | 16/16 | 0 | 99.4 | 0.999 |
| Average of selfish openings | 1210.6 | 16/16 | 0 | 99.3 | 0.999 |
| Lone ruler (equal weights, no debate) | 1207.3 | 16/16 | 0 | 99.2 | 0.999 |
| Provider alone | 1205.5 | 16/16 | 0 | 99.2 | 0.999 |
| Villager alone | 1205.5 | 16/16 | 0 | 99.2 | 0.999 |
| Priest alone | 1189.8 | 16/16 | 0 | 98.4 | 0.996 |
| Council (5 agents, negotiated) | -75.1 | 11/15 | 29.3 | 69.1 | 0.919 |
| Lone LLM (one prompt, all goals) | -722.5 | 9/16 | 40.4 | 54.4 | 0.858 |

## New information

| decider | score with scouts | score silent | plans changed by news |
|---|---|---|---|
| Council | -75.1 | -181.9 | 10/69 years |
| Lone ruler | 1207.3 | 1214.1 | - |

## Lessons

- Negotiation comparison: the council scored -75.1 vs 1210.6 for averaging each advisor's selfish plan (16/16 reigns survived vs 11/15).
- A single equal-weight ruler scored 1207.3 vs the council's -75.1. The council's worst-served advisor averaged 0.919 satisfaction vs 0.999 under the lone ruler.
- News changed the adopted plan in 10 of 69 years where it arrived. Scouts moved the council's mean score from -181.9 (silent) to -75.1.
- Outcomes over 70 council-years: 65 consensus, 5 majority with recorded dissent, 0 decided by runoff, 0 escalated to a human. Mean rounds per year: 1.24, mean turns per year: 5.8.
- A single LLM given every goal in one prompt scored -722.5 (9/16 reigns survived, 40.4 starved per reign) vs the council's -75.1 (11/15, 29.3). In 2 of its years the LLM's plan was still over budget after one corrected retry and the equal-weight ruler decided instead.
- Council reigns excluded from the score: 1 deadlocked, 0 technical failures (of 16).
