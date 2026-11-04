# Evaluation report

Generated 2026-11-04T11:56:18+00:00 by `python -m scripts.evaluate` over `data/eval/eval_set.json` (42 cases on the **fictional** sample dataset).

**Mode:** LLM = extractive (Ollama model not available) · embeddings = local-hash · places = sample/snapshot datasets

| Metric | Value | Definition |
|---|---|---|
| Retrieval accuracy (hit@6) | 100.0% | Expected fact present in the property's retrieved chunks |
| Retrieval MRR | 0.975 | Mean reciprocal rank of the first chunk containing the fact |
| Intent accuracy | 100.0% | Planner intent equals the label |
| Answer relevance | 100.0% | Answer contains the expected facts (or declines when it should) |
| Local results returned | 91.7% | Local/mixed/emergency cases that produced place cards |
| Groundedness | 100.0% | Answers whose factual tokens (times, codes, numbers) all appear in retrieved context |
| Hallucination rate | 0.0% | Unanswerable/isolation cases that invented an answer or leaked another property's data |
| Latency p50 / p95 | 21 / 28 ms | End-to-end per message (in-process API) |

## Failures and notes

- **[janakpuri] Which metro station is closest?** — no place results

## How to read this

- In **extractive mode** (no Ollama model) answers are quoted from retrieved chunks, so groundedness is high by
  construction; this run mainly measures routing, retrieval and isolation. Re-run inside Docker with the
  model downloaded (`docker compose exec backend python -m scripts.evaluate`) to evaluate LLM answers.
- With the sample dataset, place results are fictional; the local metrics only check that a search ran
  around the property's coordinates and returned cards.
- The evaluation set was also used while developing the heuristic planner and extractive answerer, so treat
  these numbers as a **regression suite**, not an unbiased estimate of real-world accuracy. Add new cases
  from real guest conversations (anonymised) to measure generalisation.
- Per-case details are in `data/eval/results.json`.
