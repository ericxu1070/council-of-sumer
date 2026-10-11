> Historical results from the previous game and voting rules. These are not completion evidence for the revised implementation. Regenerate with the existing benchmark command.

# Benchmark (30 seeds, transport inproc, reasoning mock)

| policy | score | survived | starved | population | fairness |
|---|---|---|---|---|---|
| Farmer alone | 1225.1 | 30/30 | 0 | 102.8 | 0.36 |
| Council (7 agents, negotiated) | 1216.6 | 30/30 | 0.4 | 102.5 | 0.386 |
| General alone | 1215.6 | 30/30 | 0.5 | 102.4 | 0.275 |
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
| Council | 1216.6 | 1198.5 | 59/140 years |
| Lone ruler | 1157.6 | 1118.1 | - |

## Lessons

- Negotiation beats averaging: the council scored 1216.6 vs -238.5 for averaging each advisor's selfish plan (30/30 reigns survived vs 30/30).
- A single equal-weight ruler scored 1157.6 vs the council's 1216.6 (the council is ahead). The council's worst-served advisor averaged 0.386 satisfaction vs 0.375 under the lone ruler.
- News changed the adopted plan in 59 of 140 years where it arrived. Scouts moved the council's mean score from 1198.5 (silent) to 1216.6.
- Outcomes over 150 council-years: 0 consensus, 123 majority with recorded dissent, 6 decided by runoff, 21 escalated to a human. Mean rounds per year: 2.93, mean turns per year: 20.1.
