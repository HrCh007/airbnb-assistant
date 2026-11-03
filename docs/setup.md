# Setup (local development)

## Prerequisites

- Python 3.12+ (tested on 3.13) and Node.js 22+
- PostgreSQL 15+ with the **pgvector** extension (≥ 0.5 for HNSW), or Docker
- Optional: Anthropic, OpenAI and Google Maps API keys (see below)

## Option A: Docker Compose (simplest)

```bash
cp .env.example .env
docker compose up --build -d             # db (pgvector/pgvector:pg16) + backend + frontend
docker compose exec backend python -m scripts.seed
```

- Chat: http://localhost:3000 · Admin: http://localhost:3000/admin · API docs: http://localhost:8000/docs
- If ports 5432, 8000 or 3000 are already in use, set `DB_PORT`, `BACKEND_PORT` or `FRONTEND_PORT` before `docker compose up`.
- The backend container runs `alembic upgrade head` on every start.
- Behind a TLS-inspecting corporate proxy, the Dockerfiles accept an optional build secret: `docker build --secret id=extra_ca,src=/path/to/ca.pem …`.

## Option B: run services directly

### 1. Database

```bash
# Ubuntu: sudo apt install postgresql-16 postgresql-16-pgvector    macOS: brew install postgresql@16 pgvector
createuser -s airbnb && psql -c "ALTER USER airbnb PASSWORD 'airbnb';"
createdb -O airbnb airbnb_assistant
createdb -O airbnb airbnb_assistant_test    # for the test suite
```

The migration runs `CREATE EXTENSION IF NOT EXISTS vector`. On managed databases, enable the extension in the provider's dashboard if your user can't create it.

### 2. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp ../.env.example ../.env                  # edit as needed; backend reads ./.env or ../.env
alembic upgrade head                        # create schema (pgvector, HNSW index, constraints)
python -m scripts.seed                      # 3 fictional properties, sample documents, dev admin
uvicorn app.main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
npm run dev                                 # http://localhost:3000
```

## Obtaining API keys

| Variable | Where | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | https://console.anthropic.com → Settings → API Keys | Default model `claude-opus-5-5` for both planning and answers (configurable with `CLAUDE_MODEL`, `CLAUDE_PLANNER_MODEL`, effort levels). `CLAUDE_ENABLE_REFUSAL_FALLBACK=true` sends the server-side fallback beta; set it to `false` if your account or platform rejects it. |
| `OPENAI_API_KEY` | https://platform.openai.com/api-keys | Used only for `text-embedding-3-small` embeddings (1536-d). With `EMBEDDING_PROVIDER=auto` it is picked up automatically. |
| `GOOGLE_MAPS_API_KEY` | Google Cloud Console → APIs & Services → enable **Places API (New)** → Credentials → Create API key | Restrict the key to *Places API (New)*. The backend calls `places:searchText`, `places:searchNearby` and `places/{id}` with a field mask (rating, hours, price level and so on). These fields are billed at Places "Pro/Enterprise" SKU rates; results are cached for `PLACES_CACHE_TTL_SECONDS`. |
| `SECRET_KEY` | `openssl rand -hex 32` | Signs admin JWTs. Required in production. |
| `DATABASE_URL` | local or Supabase | `postgres://` and `postgresql://` URLs are accepted (converted to asyncpg). |

**Switching embedding provider changes the vector space.** After adding or removing `OPENAI_API_KEY`, call `POST /api/admin/documents/reindex-all` (with an admin token) or re-seed.

## Managing properties and documents

1. Sign in at `/admin` (dev: `admin@example.com` / `ChangeMe-Dev-2026!`). For real accounts use `python -m scripts.create_admin --email you@example.com`.
2. **Properties → a property → Details:** replace the placeholder data. Enter the **exact latitude/longitude** of the building (in Google Maps, right-click the building and click the coordinates to copy them) and the discovery radius. Fill in any structured fields you have; empty fields are simply unknown to the assistant. Saving re-indexes the property profile immediately.
3. **Documents tab:** upload PDF, DOCX, TXT, MD, CSV or JSON (≤ 10 MB). Processing runs in the background (status: pending → processing → ready/failed). Use the eye icon to inspect the chunks, including which ones are flagged *sensitive*, and the refresh icon to re-index.
4. **Host picks tab:** add your favourite places with a short note. They appear alongside live results for matching requests.
5. Optionally set a **guest PIN** (shared in the booking message) so Wi-Fi passwords and access codes are only revealed to verified guests.
6. Untick *Contains placeholder data* once the property is real.

Embeddings are generated automatically when documents are uploaded or properties are saved. To regenerate everything: `POST /api/admin/documents/reindex-all`.

## Testing

```bash
# Backend: unit + integration (uses TEST_DATABASE_URL, default .../airbnb_assistant_test,
# which is dropped and rebuilt on every run)
cd backend && pytest -q
ruff check .

# Frontend
cd frontend && npm run lint && npm run typecheck && npm run build

# End-to-end (Playwright): start the stack first
cd backend && python -m scripts.seed --reset
PLACES_PROVIDER=mock RATE_LIMIT_CHAT_PER_MINUTE=500 uvicorn app.main:app --port 8000 &
cd frontend && npm run dev &
npx playwright test          # mobile (Pixel 7) + desktop projects

# Evaluation report
cd backend && python -m scripts.evaluate --places mock   # → docs/evaluation_report.md
```

### Verifying live integrations

Once you have keys, run these checks; they were **not** possible while building this project.

```bash
export ANTHROPIC_API_KEY=… OPENAI_API_KEY=… GOOGLE_MAPS_API_KEY=…
cd backend
LIVE_ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY pytest -m live -q        # Claude streaming smoke test
curl -s localhost:8000/api/status                                    # llm=claude, embeddings=openai, places=google_places
curl -s -X POST localhost:8000/api/admin/documents/reindex-all -H "Authorization: Bearer $TOKEN"
python -m scripts.evaluate                                           # evaluation with real Claude + embeddings + Places
```

Then walk through the Definition-of-Done conversation in the UI (select Saket → Wi-Fi → dinner → cheaper → drinks and dancing → checkout).
