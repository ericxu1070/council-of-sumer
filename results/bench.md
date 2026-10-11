# Benchmark (30 seeds, transport inproc, reasoning mock)

| policy | score | survived | starved | population | fairness |
|---|---|---|---|---|---|
| Farmer alone | 1189.8 | 30/30 | 0 | 102.5 | 0.407 |
| Lone ruler (equal weights, no debate) | 1126.7 | 30/30 | 2.3 | 99.9 | 0.423 |
| Council (4 agents, negotiated) | 1048.3 | 30/30 | 4.1 | 96.5 | 0.459 |
| General alone | 1032.8 | 30/30 | 4.4 | 95.7 | 0.254 |
| Average of selfish openings | -2317.3 | 10/30 | 81.2 | 2.1 | 0.388 |
| Priest alone | -3147.4 | 0/30 | 88.1 | 1.9 | 0.0 |
| Merchant alone | -3163.6 | 0/30 | 93.7 | 0.2 | 0.125 |

## New information

| decider | score with scouts | score silent | plans changed by news |
|---|---|---|---|
| Council | 1048.3 | 998.5 | 85/287 years |
| Lone ruler | 1126.7 | 1132.0 | - |

## Lessons

- Negotiation beats averaging: the council scored 1048.3 vs -2317.3 for averaging each advisor's selfish plan (10/30 reigns survived vs 30/30).
- A single equal-weight ruler scored 1126.7 vs the council's 1048.3. The council's edge is fairness: its worst-served advisor averaged 0.459 satisfaction vs 0.423 under the lone ruler.
- News changed the adopted plan in 85 of 287 years where it arrived. Scouts moved the council's mean score from 998.5 (silent) to 1048.3.
- Outcomes over 300 council-years: 152 consensus, 99 majority with recorded dissent, 49 escalated to a human. Mean rounds per year: 4.15.
- Single-mandate rule fails: Priest alone, Merchant alone lost most reigns. One objective is not enough, which is why each advisor's red line matters.
