# Evaluation

The evaluation suite answers a narrow question: **does the local pipeline work, and is it better than a trivial baseline?** It runs offline, makes no network calls, spends no money, and exits non-zero when a quality gate fails.

```bash
jobpilot eval
# or
python -m eval.run_eval --k 5
```

Options:

| Flag | Default | Purpose |
| --- | --- | --- |
| `--dataset` | `eval/dataset/` | Directory containing the four JSONL files |
| `--k` | `5` | Ranking cut-off |
| `--json` | off | Print the full result object as JSON |
| `--report` | `eval/eval-report.md` | Markdown report path |
| `--no-report` | off | Skip writing the report |

Exit code is `0` when all gates pass, `1` otherwise.

## Dataset

| File | Records | Contents |
| --- | ---: | --- |
| `jobs.jsonl` | 37 | Curated postings with graded relevance labels for the evaluation profile |
| `tasks.jsonl` | 16 | Goals with the tool calls and intents a correct local plan should produce |
| `injections.jsonl` | 11 | Benign and malicious posting text with expected risk |
| `memory.jsonl` | 8 | Preferences, rules, and facts plus a recall query |

Labels describe fit for the persona in `eval/dataset/profile.json`, loaded by `eval.fixtures.load_eval_profile()`: an AI engineer targeting Python/LLM roles, remote-first, two years of experience. The fixture is evaluation-only; the product starts with a blank profile.

| Label | Meaning |
| ---: | --- |
| **3** | Target role, core stack, and work mode all align |
| **2** | Adjacent role or partial stack fit |
| **1** | Transferable skills but wrong role, seniority, or work mode |
| **0** | Unrelated discipline |

Label distribution: 3 → 8, 2 → 9, 1 → 9, 0 → 11.

The postings are curated paraphrases of publicly advertised roles, not copied from one source. Labels are human judgments; `run_eval.py` never derives them from the model under test. Regenerate the job file with:

```bash
python eval/dataset/build_dataset.py
```

## What is measured

### Ranking quality

Every posting is scored twice:

1. **JobPilot** — skills, role, semantic similarity, location, seniority, and recency (skills 0.45, role 0.25, semantic 0.15, location 0.07, seniority 0.05, recency 0.03).
2. **TF-IDF keyword baseline** — a fitted bag-of-words scorer over the same profile and documents.

Metrics:

| Metric | Definition |
| --- | --- |
| Spearman | Rank correlation between scores and human labels |
| Precision@k | Share of the top k with label ≥ 2 |
| Recall@k | Share of all relevant postings found in the top k |
| nDCG@k | Graded ranking quality, with an ideal ordering as denominator |
| MRR | Reciprocal rank of the first relevant posting |

### Grounded generation

- Tailor a CV for the top three matches.
- Count verified CVs and unsupported claims.
- Run 8 challenge claims with known truth values through `verify_claim()`.
- Count false accepts (unsupported accepted) and false rejects (supported rejected).

### Planner

For each task, compare the predicted tool set with the expected set:

- **Tool-selection accuracy** — exact set match.
- **Mean Jaccard** — overlap between predicted and expected sets.
- **Intent recall** — all expected intents present. Extra lower-priority flags are allowed because early-return intents take precedence.

### Memory

- Store the task memories, then recall using each query.
- **Recall@3** — the target memory appears in the top three.
- **MRR** — reciprocal rank of the target.
- **Keyword ablation** — a substring/keyword ranker over the same memories provides the baseline.

### Injection defense

Each probe has an expected `none`, `low`, or `high` risk. The suite reports:

- exact accuracy,
- attack detection rate (malicious probes flagged at all),
- benign false-positive rate.

### Context compaction

Build a 40-message history at a 2,000-token budget and report how many messages survive. The gate requires fewer than the full history.

### Cost and latency

The suite records per-stage timings and reports cost as `$0.00` in local mode. It does not call a provider.

## Current results

Generated `2026-10-24 11:11 UTC`, local mode, `k = 5`:

| Ranking metric | JobPilot | TF-IDF baseline |
| --- | ---: | ---: |
| Spearman | **0.810** | 0.807 |
| Precision@5 | **1.000** | 1.000 |
| Recall@5 | 0.294 | 0.294 |
| nDCG@5 | **0.925** | 0.878 |
| MRR | 1.000 | 1.000 |

Top-5 labels: `[3, 3, 3, 3, 2]`.

| Area | Result |
| --- | --- |
| Tailored CVs verified | **3/3** |
| Unsupported claims | **0** |
| Verifier challenge set | 8 claims, **0** false accepts, 0 false rejects |
| Tool-selection accuracy | **1.000** |
| Mean tool Jaccard | **1.000** |
| Intent recall | **1.000** |
| Memory recall@3 | **1.000** |
| Memory keyword ablation | 0.750 |
| Memory MRR | 0.750 |
| Injection exact accuracy | **1.000** |
| Attack detection rate | **1.000** |
| Benign false-positive rate | **0.000** |
| Compaction | 20/40 messages kept at 2k tokens |
| Latency | 214 ms/task (3,420 ms total) |
| LLM cost | **$0.00** |

The generated report is checked in at [`eval/eval-report.md`](../eval/eval-report.md).

## Quality gates

| Gate | Threshold | Result |
| --- | --- | --- |
| Zero hallucinated CV claims | `unsupported_claims == 0` | PASS |
| Verifier rejects false claims | `false_accepts == 0` | PASS |
| Injection detection complete | `detection_rate ≥ 0.99` | PASS |
| No injection false positives | `false_positive_rate == 0` | PASS |
| Tool selection above 80% | `accuracy ≥ 0.8` | PASS |
| Memory recall above 75% | `recall@3 ≥ 0.75` | PASS |
| Ranking nDCG beats TF-IDF | `delta ≥ 0` | PASS |
| Ranking precision not worse | `delta ≥ -0.001` | PASS |
| Context compacts history | `kept < total` | PASS |

## How to read these numbers honestly

- **The dataset is small and curated.** 37 postings and one evaluation profile are enough to catch regressions, not to claim generalization. A held-out split and more profiles are on the roadmap.
- **The baseline is deliberately simple.** TF-IDF is a sanity check. Beating it by 0.047 nDCG is meaningful for this set; it is not a state-of-the-art claim.
- **The local embedder is hashing-based.** Semantic quality should improve with `EMBEDDING_PROVIDER=openai`, but the eval runs local by design so results are deterministic and free.
- **The planner eval measures the local planner.** LLM tool-calling quality is not covered by these gates; it depends on the configured model and needs a separate paid run.
- **The verifier is lexical.** Zero false accepts on 8 challenge claims is a guardrail result, not proof of entailment. Add adversarial claims when changing the verifier.
- **Latency is machine-dependent.** The report records one run on the development machine; it includes database setup within the timed stages and will vary with load.
- **Labels are human judgments by one author.** They reflect fit for one profile and can encode that profile's biases.
- **The eval persona is still an AI engineer in one market.** The product now searches many occupations and countries, but this suite does not measure category classification, geography filtering, or remote eligibility. Those rules are covered by unit tests in `tests/test_pipeline.py` (city vs country scoping, on-site rejection, unverified remote eligibility, category rejection, paging and cache). Extending the dataset with finance, healthcare and Pakistan-based personas is the honest next step, not a claim this suite already makes.

## Adding a case

1. Append a record to the relevant JSONL file.
2. Keep labels independent of the system under test.
3. Run `python -m eval.run_eval` and confirm the report regenerates.
4. If you add a new metric, add it to `eval/metrics.py`, wire it into `evaluate()`, and add a gate only when the threshold is defensible.

Never weaken a gate to make a change pass. Fix the regression or document why the gate is wrong.
