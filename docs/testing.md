# Testing

## Backend: unit and integration tests

The tests need no Docker, no network and no models. They use a temporary SQLite database, an embedded ChromaDB, the lexical hash embedder, a fake Overpass API and a fake Ollama. The sentence-transformers code path is exercised with a tiny model built inside the test.

```bash
cd backend
python -m venv .venv
```

Activate it with `.venv\Scripts\activate` (Windows) or `source .venv/bin/activate` (macOS/Linux). Then install CPU-only PyTorch and the dev requirements:

```bash
pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0
```

```bash
pip install -r requirements-dev.txt
```

```bash
pytest -q
```

```bash
ruff check .
```

| Area | Files |
|---|---|
| Extraction, chunking, sensitivity, embeddings | `tests/unit/test_ingestion_units.py`, `tests/unit/test_local_ai.py` |
| Routing / intents / Hinglish / follow-ups | `tests/unit/test_planner.py` |
| Overpass client, selectors, opening hours, ranking, datasets, geocoder | `tests/unit/test_location_units.py` |
| Ollama client (streaming, think-filter, errors, JSON routing) | `tests/unit/test_local_ai.py` |
| Prompt-injection hardening, auth, rate limit, log redaction | `tests/unit/test_compose_security.py` |
| Acceptance conversation, RAG answers, discovery, radius, fallbacks, memory, switching, PIN, session isolation | `tests/integration/test_chat_scenarios.py` |
| Retrieval isolation, per-property collections, admin CRUD, uploads (all formats), re-indexing, host picks | `tests/integration/test_isolation_and_admin.py` |
| LLM grounding contract, history scoping, answer verification, outage fallback, LLM routing | `tests/integration/test_llm_grounding.py` |

## Docker: stack smoke test

`scripts/docker-smoke-test.sh` (bash) / `scripts/docker-smoke-test.ps1` (PowerShell) build and start the stack, then check:

- container health;
- frontend → backend → ChromaDB connectivity;
- the acceptance conversation through the frontend's `/api` proxy;
- persistence across `docker compose down` and `up` (the same data is still there);
- operation with no API keys.

```bash
./scripts/docker-smoke-test.sh
```

## End-to-end (Playwright)

With the stack running on http://localhost:3000:

```bash
cd frontend && npm install && npx playwright install chromium && npx playwright test
```

## Evaluation report

```bash
docker compose exec backend python -m scripts.evaluate
```

This writes `docs/evaluation_report.md` (retrieval hit@k and MRR, intent accuracy, answer relevance, groundedness, hallucination rate, latency).
