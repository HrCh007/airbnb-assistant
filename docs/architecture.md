# Architecture

A zero-cost, self-hosted RAG assistant. Every component is open source and runs on your machine; there are no API keys or paid services.

```
 Guest phone / host laptop
          │  http://localhost:3000
 ┌────────▼─────────────── frontend (Next.js) ────────────────────────┐
 │  /        guest chat · location/property picker · SSE stream        │
 │  /admin   host console · properties · documents · host picks        │
 │  /api/*   ── proxied to the backend over the internal network ──┐   │
 └──────────────────────────────────────────────────────────────────┼───┘
 ┌──────────────────────────── backend (FastAPI) ───────────────────▼──┐
 │ api/public.py · api/admin.py                                        │
 │ services/orchestrator.py: route → RAG → discovery → answer → verify │
 │   llm/heuristic_planner.py   deterministic router (EN + Hinglish)   │
 │   rag/  extract → chunk → embed (sentence-transformers, in-process) │
 │   location/  Overpass client · datasets · ranking · opening hours   │
 │   SQLite (aiosqlite) in /data/app.db   uploads in /data/uploads     │
 └──────┬──────────────────────────┬──────────────────────────┬────────┘
        │ HTTP (internal)          │ HTTP (internal)          │ HTTPS (optional)
 ┌──────▼──────┐          ┌────────▼────────┐        ┌────────▼──────────────┐
 │  chromadb   │          │     ollama      │        │ OpenStreetMap         │
 │ one vector  │          │ qwen3:1.7b etc. │        │ Overpass (places)     │
 │ collection  │          │ (CPU or GPU)    │        │ Nominatim (geocoding) │
 │ per property│          └─────────────────┘        └───────────────────────┘
 └─────────────┘
```

| Concern | Component |
|---|---|
| UI | Next.js 16, React 19, TypeScript, Tailwind 4, Lucide |
| API | FastAPI, Pydantic, Server-Sent Events for streaming |
| Structured data | SQLite + SQLAlchemy (async, `aiosqlite`), foreign keys enforced, WAL mode |
| Embeddings | `sentence-transformers` `all-MiniLM-L6-v2` (384-d), CPU, baked into the image |
| Vector store | ChromaDB 1.5.9 (HTTP service in Docker; embedded on-disk store for local dev) |
| LLM | Ollama (default `qwen3:1.7b`, configurable) |
| Documents | PyMuPDF (PDF), python-docx (DOCX), TXT, Markdown, CSV, JSON |
| Local discovery | OpenStreetMap via Overpass (live), an OSM snapshot file, a fictional sample dataset, host picks |

## Request flow: `POST /api/chat/message`

1. **Validate** the session (UUID plus a secret token whose SHA-256 is stored) and apply the rate limit.
2. **Persist** the message under the current `property_id`, and load that property's recent history, guest preferences, and the most recent search plan and results.
3. **Route.** `heuristic_plan()` classifies the message into one of the six intents (PROPERTY / LOCAL_DISCOVERY / GENERAL_NEIGHBOURHOOD / MIXED / GENERAL_CONVERSATION / EMERGENCY). It also extracts a search plan (category, keyword, radius, budget, late-night, follow-up action) and preference updates. Only when the rules can't place a message is the local LLM asked to classify it (a JSON-schema constrained call), which avoids a slow CPU inference on most messages.
4. **Switch property** if the guest says so ("actually I'm in Dwarka").
5. **Property RAG**, for property, mixed, emergency and neighbourhood intents. A vector search runs in the property's own ChromaDB collection, BM25 keyword search runs over the same property's chunks in SQLite, and the two are combined with reciprocal rank fusion. Sensitive chunks (Wi-Fi password, door codes) are withheld until the guest's PIN is verified, if the property has one.
6. **Local discovery**, when the plan needs places. Searches run around the property's **exact coordinates**, trying in order:
   1. live Overpass (`source: osm`);
   2. the saved snapshot (`osm_snapshot`);
   3. the fictional sample dataset (`sample`).

   Distances use the haversine formula. Results are filtered by radius and opening hours, deduplicated (node and way of the same venue), ranked, and merged with host picks (`host_curated`). A failure triggers a 5-minute cool-down so the chat stays fast.
7. **Answer.** Deterministic cases need no model: greetings, PIN-withheld facts, "not in the guide", and "no places found". Otherwise the local LLM receives a context block (`<session>`, `<guest_profile>`, `<property_knowledge>`, `<local_results>`, `<notices>`; marked as data, with tag injection neutralised) and streams its answer.
8. **Verify.** Fact-like tokens in the streamed answer (times, codes, phone numbers, distances) must appear in the context, and every bold place name must be one of the listed places. If not, a `replace` event swaps in the extractive answer. If Ollama is down or the model isn't downloaded, the extractive answer is used from the start.
9. **Persist** the assistant message with a trace (intent, router, cited chunk ids, places shown, search plan, verification result, latency), then emit suggestions.

## Data model (SQLite)

| Table | Purpose |
|---|---|
| `locations` | Dwarka / Janakpuri / Saket (more can be added in the admin console) |
| `properties` | name, address, **latitude/longitude**, discovery radius, rooms, amenities (JSON), `details` JSON (check-in/out, Wi-Fi, parking, kitchen, house rules, emergency contacts, nearby facilities), guest PIN hash, placeholder/active flags |
| `property_documents` | filename, type, size, checksum, `storage_path` (file in the uploads volume), status, error, chunk count, last indexed |
| `knowledge_chunks` | chunk text, section, sensitivity flag, metadata (`property_id, document_id, document_name, chunk_id, section, source, …`); **`property_id` NOT NULL** plus a composite FK `(document_id, property_id)` so a chunk can't belong to another property's document |
| `conversations` | selected location/property, session token hash, PIN-verified property |
| `messages` | role, content, intent, trace metadata |
| `guest_preferences` | budget, dietary, companions, interests, travel mode, max distance (search radius) |
| `curated_recommendations` | host picks: name, category, host note, address, coordinates, OSM id, price level, tags |
| `admin_users` | email, bcrypt hash |

Vectors live in ChromaDB under the same chunk ids, in a collection per property (`prop_<uuid>`). Each collection records the embedding model that produced it, and the backend re-indexes automatically when the model changes or vectors are missing (for example after deleting the Chroma volume).

## Isolation guarantees

1. **Separate collections:** a similarity search physically cannot reach another property's vectors.
2. **Filters at every layer:** a `where={"property_id": …}` filter in Chroma, `WHERE property_id = …` in SQL, and a final post-filter.
3. **Schema:** the composite foreign key prevents cross-property chunk/document links.
4. **Memory:** the history sent to the LLM contains only messages exchanged under the current property.
5. **Sensitive facts:** stored as their own chunks, optionally behind a per-property guest PIN.
6. **Sessions:** a per-session secret token is required to read or write a conversation.

## Hallucination controls

- Property answers come only from retrieved chunks. If the question's topic isn't in them, the deterministic reply is "I don't have confirmed information about that yet…".
- Places come only from OSM, the snapshot, host picks or clearly labelled sample data. OSM has no ratings or prices, so neither is ever shown or invented. "Budget" means the place type is budget-friendly (fast food, food court, dhaba) or the host gave an estimate.
- Post-generation verification replaces answers containing unsupported facts or unknown place names.

## Security

- bcrypt (cost 12) admin passwords and HS256 JWTs. `SECRET_KEY` is generated and stored in the data volume when not set.
- Rate limits (per IP): chat 20/min, login and PIN 5/min, other 120/min.
- Uploads: extension allow-list, content sniffing, 10 MB limit, page limit for PDFs, parsing in a worker thread with per-document error recording.
- Prompt injection: context fenced as data, injected tag delimiters escaped, Overpass keywords sanitised, and a system-prompt rule to ignore embedded instructions.
- Logs redact passwords and bearer tokens. ChromaDB and Ollama are not published outside Docker.
