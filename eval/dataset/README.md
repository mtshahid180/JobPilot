# Evaluation dataset

| File | Contents |
| --- | --- |
| `jobs.jsonl` | 37 curated postings with graded relevance labels (`0`–`3`) for the evaluation profile in `profile.json`. |
| `tasks.jsonl` | End-to-end goals with the tool calls a correct local plan should make. |
| `injections.jsonl` | Benign and malicious posting text with the expected injection risk. |
| `memory.jsonl` | Preferences, rules and facts plus the query that should recall them. |

Labels describe fit for `eval.fixtures.load_eval_profile()` (an AI engineer
targeting Python/LLM roles, remote-first, two years of experience). The fixture
is evaluation-only; the product starts with a blank profile:

- **3** — target role, core stack and work mode all align.
- **2** — adjacent role or partial stack fit.
- **1** — transferable skills but the wrong role, seniority or work mode.
- **0** — unrelated discipline.

The postings are curated paraphrases of publicly advertised roles, not copied
from a single source. Rebuild `jobs.jsonl` with:

```bash
python eval/dataset/build_dataset.py
```

Labels are human judgments recorded by the project author; `run_eval.py` never
derives them from the model under test.
