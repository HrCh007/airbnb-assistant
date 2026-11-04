# Hideout Studios — Local Guest Assistant

A friendly AI local guide for guests at Hideout Studios' Airbnb stays in **Dwarka, Janakpuri and Saket (Delhi)**. It answers questions about the guest's property from that property's own documents (RAG), and recommends nearby food, nightlife, shopping, sights and essential services, measured from the property's exact coordinates.

**Zero cost, fully local.** The LLM (Ollama), embeddings (sentence-transformers), vector database (ChromaDB) and structured data (SQLite) all run on your machine in Docker. There are no API keys, accounts, billing or paid services. Neighbourhood data comes from **OpenStreetMap** (free), with offline fallbacks.

| Guest chat (mobile) | Recommendations | Host console |
|---|---|---|
| ![welcome](docs/screenshots/mobile-welcome.png) | ![cards](docs/screenshots/mobile-recommendations.png) | ![admin](docs/screenshots/admin-property.png) |

## Quick start (Docker Desktop)

```bash
git clone https://github.com/HrCh007/airbnb-assistant.git
```

```bash
cd airbnb-assistant
```

```bash
git checkout feature/free-local-rag
```

```bash
docker compose up --build -d
```

- **Guest chat:** http://localhost:3000. Pick **Saket**, then ask "What's the Wi-Fi password?" or "Suggest places for dinner nearby".
- **Host console:** http://localhost:3000/admin, dev login `admin@example.com` / `ChangeMe-Dev-2026!`
- **API docs:** http://localhost:8000/docs

The first start downloads the local model (`qwen3:1.7b`, about 1.4 GB). Follow it with `docker compose logs -f ollama-pull`. Until it finishes, answers quote the property guide directly.

Full Windows guide (memory settings, model choice, GPU, phone access, backups, troubleshooting): **[docs/docker-windows.md](docs/docker-windows.md)**.

## What it does

- **Property Q&A (RAG):** upload PDF, DOCX, TXT, Markdown, CSV or JSON per property. Documents are extracted, chunked and embedded locally, then stored in ChromaDB (one collection per property) and SQLite. Hybrid vector + BM25 retrieval is strictly filtered by `property_id`. Structured fields (check-in, Wi-Fi, parking, kitchen, house rules, emergency contacts) are indexed too. Missing facts get *"I don't have confirmed information about that yet. Let me suggest checking with your host."*
- **Neighbourhood discovery:** about 50 categories (restaurants, cafes, street food, momos, bars, clubs, cinemas, malls, markets, parks, monuments, pharmacies, hospitals, ATMs, metro…) plus keyword search ("tibetan"). Results come from live OpenStreetMap (Overpass), a saved OSM snapshot, or a clearly labelled fictional sample set, plus your own host picks. Distances are haversine from the property; radius chips offer 1/3/5/10 km. Ranking combines relevance, distance, opening hours, budget/diet fit and data completeness, with deduplication. Every card says where its data came from.
- **Conversation:** six intents (property / local discovery / neighbourhood / mixed / small talk / emergency) via a fast deterministic router that understands English and Hinglish, with the local LLM consulted only for messages the rules can't place. The assistant remembers the property, budget, diet, companions and radius, handles follow-ups ("something cheaper", "any of these good for families?") and property switching. Conversations are stored in SQLite and can be cleared.
- **Grounded answers:** the local LLM streams answers built only from the retrieved facts and places. A verifier replaces any answer that mentions times, codes, numbers or places that aren't in the data. Without the model, answers are extractive.
- **Safety:** emergencies lead with 112 and the host's contact. Wi-Fi passwords and door codes can sit behind a per-property guest PIN. Prompt-injection hardening, rate limits, upload validation and per-session secret tokens are built in.
- **Host console:** add, edit and deactivate properties; set coordinates (with "Find from address" via OpenStreetMap); upload, delete and re-index documents and view their chunks; add and edit host picks with notes; dashboard showing the status of each local component.

## Architecture

```
browser ──► frontend (Next.js :3000) ──/api──► backend (FastAPI) ──► chromadb   (vectors, per property)
                                                    │  SQLite     └──► ollama     (local LLM)
                                                    │  sentence-transformers (in-process)
                                                    └──► OpenStreetMap Overpass/Nominatim (optional, free)
```

Details: [docs/architecture.md](docs/architecture.md).

## What needs the internet

| Needs internet | Works offline once set up |
|---|---|
| First build and model download | Property Q&A, answer generation, admin console |
| Live OpenStreetMap searches and address lookup | Nearby places from a saved OSM snapshot (`python -m scripts.build_osm_snapshot`) or sample data |

## Repository layout

```
backend/            FastAPI app (api, core, models, rag, location, llm, prompts, services), scripts, tests
frontend/           Next.js app (guest chat at /, host console at /admin), Playwright e2e
data/
  sample_documents/ FICTIONAL property documents (md, csv, json, txt, docx, pdf)
  discovery/        FICTIONAL sample places + README for the OSM snapshot
  eval/             evaluation set and results
docs/               architecture, docker-windows, configuration, testing, API, evaluation report
scripts/            Docker smoke tests (bash + PowerShell)
docker-compose.yml  (+ docker-compose.dev.yml, docker-compose.gpu.yml)
```

## Documentation

- [Docker & Windows setup](docs/docker-windows.md): install, model choice, volumes, backups, troubleshooting
- [Configuration](docs/configuration.md): every environment variable
- [Architecture](docs/architecture.md): components, request flow, data model, isolation, security
- [API](docs/api_documentation.md): REST endpoints and the streaming chat protocol
- [Testing](docs/testing.md): unit, integration, Docker smoke and end-to-end tests
- [Local development without Docker](docs/local-development.md)

## Sample data

The three properties (`Demo Property – Dwarka/Janakpuri/Saket`), their documents and every place in `data/discovery/sample_places.json` are **fictional development data**. Placeholder coordinates are rough locality centre-points. Replace everything in the host console. The Janakpuri demo uses guest PIN `2468`.

Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright) (ODbL).
