# Architecture

## Components

```
                ┌───────────────────────────── Next.js (Vercel) ─────────────────────────────┐
 Guest phone ──►│  /            chat UI · location/property picker · SSE stream · cards       │
 Host laptop ──►│  /admin       console · properties · documents · host picks · dashboard     │
                └───────────────┬───────────────────────────────────────────────┬────────────┘
                                │ REST + text/event-stream                       │ Bearer JWT
                ┌───────────────▼──────────────────── FastAPI (Render) ─────────▼────────────┐
                │ api/public.py   sessions · chat (SSE) · properties · places                │
                │ api/admin.py    login · dashboard · CRUD · upload · re-index · curated     │
                │                                                                            │
                │ services/orchestrator.py  ── plan ──► rag/retriever ──► location/discovery │
                │        │                       │            │                  │          │
                │        ▼                       ▼            ▼                  ▼          │
                │ llm/claude.py (planner +   llm/heuristic  pgvector +     Google Places    │
                │ streamed answers)          _planner       full-text      API (New)        │
                │ rag/ingestion.py  extract → clean → chunk → embed → store (background)    │
                └───────────────┬───────────────────────────────────────────────┬────────────┘
                                │ asyncpg                                         │ HTTPS
                ┌───────────────▼─────────── PostgreSQL 16 + pgvector (Supabase) ┴────────────┐
                │ locations · properties · property_documents · knowledge_chunks (vector)     │
                │ conversations · messages · guest_preferences · curated_recommendations ·    │
                │ admin_users                                                                 │
                └──────────────────────────────────────────────────────────────────────────────┘
```

| Spec component | Implementation |
|---|---|
| A. Frontend chatbot | `frontend/components/chat/*`, `hooks/useChat.ts`, `services/api.ts` (SSE parser) |
| B. Location/property selection | `services/sessions.py` (auto-select single property, switch), `LocationPicker.tsx` |
| C. Backend API | `app/api/public.py`, `app/api/admin.py`, OpenAPI at `/docs` |
| D. RAG ingestion | `app/rag/extractors.py`, `chunking.py`, `embeddings.py`, `ingestion.py`, `profile.py` |
| E. Vector DB / knowledge base | `knowledge_chunks` (pgvector HNSW + GIN tsvector), `app/rag/retriever.py` |
| F. Location intelligence | `app/location/providers.py`, `discovery.py`, `ranking.py`, `geo.py`, `categories.py` |
| G. LLM orchestration + memory | `app/services/orchestrator.py`, `app/llm/*`, `messages` + `guest_preferences` tables |
| H. Admin interface | `frontend/app/admin/*`, `frontend/components/admin/*` |

## Request flow: `POST /api/chat/message`

1. **Validate** the session (UUID plus a secret session token whose SHA-256 is stored) and enforce the rate limit.
2. **Persist** the guest message, tagged with the current `property_id`.
3. **Load context:** recent history **for the current property only**, guest preferences, and the most recent local-search plan and results (these survive intervening property questions).
4. **Plan.** Claude (`messages.parse` with a Pydantic schema, low effort) returns `QueryPlan{intent, property_question, places{action, query, type, radius, open_now, price, rating, rank_by_distance}, preference_updates, clarification_question, switch_location, language}`. If Claude is not configured or fails, `heuristic_plan()` is used. A detected emergency is never downgraded.
5. **Switch property** if requested ("actually I'm in Dwarka"), then emit a `session` event.
6. **Property RAG**, but only for PROPERTY / MIXED / EMERGENCY / NEIGHBOURHOOD intents. Retrieval uses `retrieve(db, property_id, question, allow_sensitive=…)`. Sensitive chunks are withheld when the property has a guest PIN that the session hasn't verified, and Claude receives a notice instead.
7. **Local discovery**, only when the plan includes `places`:
   - `search` / `refine_previous` searches Google Places around the property's **exact coordinates** with the planned filters, computes distances, drops results outside 1.5× the radius, ranks them and merges matching host picks;
   - `filter_previous` reuses the places shown earlier, with no API call.
   Results are emitted as a `places` event (rendered as cards).
8. **Generate.** Claude streams the answer (`messages.stream`, adaptive thinking, `effort=medium`, cached system prompt, optional server-side refusal fallback). The context block is a user turn containing `<session>`, `<guest_profile>`, `<property_knowledge>` (excerpts with source ids), `<local_results>` and `<notices>`, all marked as data and with tag-injection neutralised. Without Claude, or if it fails before the first token, `offline_answer()` composes an extractive reply.
9. **Persist** the assistant message with an internal trace (intent, planner, cited chunk ids and scores, places shown, search plan, notices, latency), then emit `suggestions` and `done`.

## Data model

| Table | Key columns | Notes |
|---|---|---|
| `locations` | slug, name, tagline, sort_order | Dwarka/Janakpuri/Saket; more can be added in admin |
| `properties` | location_id, name, address, **latitude/longitude**, **discovery_radius_m**, bedrooms…, amenities[], **details JSONB**, guest_pin_hash, is_placeholder, is_active | `details` follows `PropertyDetails` (check-in/out, facilities, house rules, emergency, neighbourhood); all fields optional |
| `property_documents` | property_id, filename, type, bytes (`content`), checksum, status, error, chunk_count, last_indexed_at, is_system | Original bytes are stored so re-indexing works on stateless hosts. `is_system` marks the auto-generated profile document |
| `knowledge_chunks` | **property_id NOT NULL**, document_id, section, content, is_sensitive, `embedding vector(1536)`, `tsv` (generated), metadata JSONB | HNSW cosine index, GIN on tsv, composite FK `(document_id, property_id)` → `property_documents(id, property_id)` |
| `conversations` | location_id, **property_id**, client_token_hash, pin_verified_property_id | one per guest session |
| `messages` | conversation_id, property_id, role, content, intent, metadata JSONB | metadata = internal trace (sources, places, plan, latency) |
| `guest_preferences` | conversation_id (unique), budget, dietary, companions, interests[], travel_mode, max_distance_km | learned during the session |
| `curated_recommendations` | property_id, name, category, note, address, lat/lng, google_place_id, tags[] | host picks |
| `admin_users` | email, password_hash (bcrypt), is_active, last_login_at | |

All identifiers are UUIDs. Chunk metadata contains `property_id, location, document_name, document_type, section, chunk_id, last_updated, embedding_provider`.

## Property isolation (defence in depth)

1. **API:** a guest session is bound to one property, and `retrieve()` requires a `uuid.UUID` property id; no code path searches across properties.
2. **SQL:** `WHERE property_id = :pid` is applied inside both the vector and the keyword query, *before* ranking.
3. **Post-filter:** results are re-checked against the requested property id.
4. **Schema:** a composite foreign key makes it impossible to store a chunk under property A that belongs to property B's document.
5. **Memory:** the history sent to Claude is limited to messages exchanged under the current property, so facts like Saket's Wi-Fi password never enter Dwarka's context after a switch.
6. **Sensitive facts:** they are detected (Wi-Fi password, door/keybox/lock codes, check-in instructions), stored as separate chunks, and optionally gated by a per-property guest PIN.

Tests: `tests/integration/test_isolation_and_admin.py`, `test_chat_scenarios.py::test_cannot_get_another_propertys_private_information`, `test_llm_grounding.py::test_history_is_scoped_to_current_property_after_switch`.

## Retrieval

- **Chunking:** sections come from headings (Markdown/DOCX/PDF), CSV rows or JSON keys. Tiny sibling sections are merged, *but never a sensitive one with a normal one*. Long sections are split recursively (paragraph → line → sentence → word) up to `CHUNK_SIZE` characters with `CHUNK_OVERLAP`, and each chunk is prefixed with its heading path.
- **Hybrid search:** `embedding <=> query` (cosine, top `RETRIEVAL_CANDIDATE_K`, minimum similarity 0.15) and `ts_rank_cd(tsv, to_tsquery(term1 | term2 …))`, fused with reciprocal rank fusion (k=60). Top `RETRIEVAL_TOP_K` are returned.
- **Structured fields:** saving a property regenerates its profile document, so admin-entered fields (check-out time, Wi-Fi, rules …) are retrievable straight away.

## Recommendation ranking

`score = Σ wᵢ·signalᵢ / Σ wᵢ` with configurable weights (`RANK_WEIGHT_*`):

| Signal | Definition |
|---|---|
| relevance | `0.6·(1 − 0.6·position/n) + 0.4·query-term overlap` (provider order as a soft signal) |
| distance | `1 − d / radius` |
| rating | Bayesian-smoothed rating `(n·r + 40·3.9)/(n + 40)` mapped from 3–5 onto 0–1 |
| open | open 1 · unknown 0.5 · closed 0 |
| preference | price level vs guest budget (budget / moderate / premium) |

## Security

- Secrets come only from the environment. Production refuses to start without a 32+ character `SECRET_KEY`, or with `PLACES_PROVIDER=mock`.
- Admin auth uses bcrypt (cost 12), HS256 JWTs with expiry, timing-equalised login, and rate limits (login 5/min, chat 20/min, other 120/min per IP; in-memory, see deployment for multi-instance).
- Uploads are restricted by extension allow-list and content sniffing (`%PDF`, `PK` zip, no NUL bytes in text), a 10 MB limit and an empty-file check; parsing failures are recorded per document.
- Prompt injection is mitigated by fencing retrieved and external content as data, escaping injected tag delimiters, giving a system-prompt rule to ignore embedded instructions, and keeping secrets and other properties' data out of the model context.
- The logging filter redacts API keys, bearer tokens and `password=` values; request bodies are never logged.
- Guest sessions require a per-session secret token. Only minimal personal data is stored: no names, emails or phone numbers of guests.
