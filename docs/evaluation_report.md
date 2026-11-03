# Evaluation report

Generated 2026-11-03T14:16:19+00:00 by `python -m scripts.evaluate` over `data/eval/eval_set.json` (42 cases on the **fictional** sample dataset).

**Mode:** LLM = offline (no ANTHROPIC_API_KEY) · embeddings = local · places = mock

| Metric | Value | Definition |
|---|---|---|
| Retrieval accuracy (hit@6) | 100.0% | Expected fact present in the property's retrieved chunks |
| Retrieval MRR | 0.95 | Mean reciprocal rank of the first chunk containing the fact |
| Intent accuracy | 100.0% | Planner intent equals the label |
| Answer relevance | 100.0% | Answer contains the expected facts (or declines when it should) |
| Local results returned | 100.0% | Local/mixed/emergency cases that produced place cards |
| Groundedness | 100.0% | Answers whose factual tokens (times, codes, numbers) all appear in retrieved context |
| Hallucination rate | 0.0% | Unanswerable/isolation cases that invented an answer or leaked another property's data |
| Latency p50 / p95 | 20 / 23 ms | End-to-end per message (in-process API) |

## Failures and notes

No failing cases.

## How to read this

- In **offline mode** answers are extractive (quoted from retrieved chunks), so groundedness is high by
  construction; this run mainly measures routing, retrieval and isolation. Re-run with
  `ANTHROPIC_API_KEY` (and `OPENAI_API_KEY` for semantic embeddings) to evaluate Claude-generated answers.
- With `PLACES_PROVIDER=mock`, place results are fictional; the local metrics only check that a search ran
  around the property's coordinates and returned cards. Live quality requires `GOOGLE_MAPS_API_KEY`.
- The evaluation set was also used while developing the heuristic planner and extractive answerer, so treat
  these numbers as a **regression suite**, not an unbiased estimate of real-world accuracy. Add new cases
  from real guest conversations (anonymised) to measure generalisation.
- Per-case details are in `data/eval/results.json`.
