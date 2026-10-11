# Benchmark (30 seeds, transport inproc, reasoning mock)

| policy | score | survived | starved | population | fairness |
|---|---|---|---|---|---|
| Farmer alone | 1225.1 | 30/30 | 0 | 102.8 | 0.36 |
| General alone | 1215.6 | 30/30 | 0.5 | 102.4 | 0.275 |
| Council (7 agents, negotiated) | 1210.1 | 30/30 | 0.4 | 102.1 | 0.381 |
| Physician alone | 1158.0 | 30/30 | 0 | 100.1 | 0.257 |
| Lone ruler (equal weights, no debate) | 1157.6 | 30/30 | 2.5 | 99.8 | 0.375 |
| Builder alone | 829.8 | 30/30 | 15.0 | 84.5 | 0.296 |
| Average of selfish openings | -238.5 | 30/30 | 50.3 | 31.2 | 0.0 |
| Envoy alone | -1737.9 | 19/30 | 85.1 | 5.1 | 0.0 |
| Merchant alone | -1742.5 | 19/30 | 85.2 | 5.0 | 0.0 |
| Priest alone | -1800.1 | 19/30 | 85.1 | 5.1 | 0.0 |

## New information

| decider | score with scouts | score silent | plans changed by news |
|---|---|---|---|
| Council | 1210.1 | 1208.9 | 77/140 years |
| Lone ruler | 1157.6 | 1118.1 | - |

## Lessons

- Negotiation beats averaging: the council scored 1210.1 vs -238.5 for averaging each advisor's selfish plan (30/30 reigns survived vs 30/30).
- A single equal-weight ruler scored 1157.6 vs the council's 1210.1 (the council is ahead). The council's worst-served advisor averaged 0.381 satisfaction vs 0.375 under the lone ruler.
- News changed the adopted plan in 77 of 140 years where it arrived. Scouts moved the council's mean score from 1208.9 (silent) to 1210.1.
- Outcomes over 150 council-years: 1 consensus, 127 majority with recorded dissent, 4 decided by runoff, 18 escalated to a human. Mean rounds per year: 3.55, mean turns per year: 24.3.
