# JobPilot evaluation report

Generated: 2026-10-31 07:50 UTC
Mode: **local** (zero API cost) · ranking cut-off: **@5**

## Dataset

- 37 labeled job postings (labels: 3=8, 2=9, 1=9, 0=11)
- 16 planner tasks, 8 memory-recall tasks, 11 injection probes

## Ranking quality

| Metric | JobPilot (heuristic + embedding) | TF-IDF baseline |
| --- | ---: | ---: |
| Spearman correlation | 0.810 | 0.807 |
| Precision@5 | 1.000 | 1.000 |
| Recall@5 | 0.294 | 0.294 |
| nDCG@5 | 0.925 | 0.878 |
| MRR | 1.000 | 1.000 |

Top-5 labels for JobPilot: `[3, 3, 3, 3, 2]` (2+ is relevant).

## Grounded generation

- Tailored CVs verified: **3/3**
- Unsupported claims in those CVs: **0** (target 0)
- Verifier challenge set: **8** claims, false accepts **0**, false rejects **0**

## Planner and memory

| Metric | JobPilot | Ablation |
| --- | ---: | ---: |
| Tool-selection accuracy | 1.000 | — |
| Mean tool Jaccard | 1.000 | — |
| Intent recall | 1.000 | — |
| Memory recall@3 | 1.000 | 0.750 (keyword) |
| Memory MRR | 0.750 | — |

## Safety and harness

- Injection exact accuracy: **1.000**
- Attack detection rate: **1.000**
- Benign false-positive rate: **0.000**
- Context compaction kept **20/40** history messages at a 2k token budget
- Latency: **347 ms/task** (total 5559 ms)
- LLM cost: **$0.00** (local pipeline)

## Quality gates

| Gate | Result |
| --- | --- |
| zero hallucinated cv claims | PASS |
| verifier rejects false claims | PASS |
| injection detection complete | PASS |
| no injection false positives | PASS |
| tool selection above 80pct | PASS |
| memory recall above 75pct | PASS |
| ranking ndcg beats tfidf | PASS |
| ranking precision not worse | PASS |
| context compacts history | PASS |

**Overall: PASS**
