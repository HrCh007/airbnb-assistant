# API documentation

Interactive docs: http://localhost:8000/docs (Swagger UI) and `/redoc`. Machine-readable: [`openapi.json`](openapi.json).

In Docker the browser calls these endpoints on the frontend's origin (http://localhost:3000/api/...), which proxies them to the backend.

All bodies are JSON unless noted. Errors use `{"detail": "..."}`: 401 (admin auth), 404, 413 (file too large), 415 (unsupported file), 422 (validation), 429 (rate limited, with `Retry-After`) and 503 (knowledge base or geocoder unavailable). No endpoint needs an API key.

## Guest endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | `{"status","database","vector_store","llm"}`; `llm` is `ready`, `model_not_downloaded` or `unreachable` |
| GET | `/api/status` | Local component status (LLM model/state, embeddings, vector store, map data sources, `paid_apis: "none"`), quick actions, radius options |
| GET | `/api/locations` | Active locations with property counts |
| GET | `/api/properties?location=saket` | Public property summaries (never includes sensitive details) |
| GET | `/api/properties/{property_id}` | One public property summary |
| POST | `/api/chat/sessions` | Create a session. Body (optional): `{"location_slug": "saket"}` or `{"property_id": "…"}`. Returns `session_id` and a one-time **`session_token`** |
| GET | `/api/chat/sessions/{id}` | Session state (location, property, available properties, preferences, `pin_verified`). Header `X-Session-Token` |
| PUT | `/api/chat/sessions/{id}/selection` | Select or change location/property: `{"location_slug": "dwarka"}` (auto-selects a sole property) or `{"property_id": "…"}` |
| POST | `/api/chat/sessions/{id}/verify-pin` | `{"pin": "2468"}` unlocks sensitive facts for the current property (403 on mismatch; rate limited) |
| PUT | `/api/chat/sessions/{id}/preferences` | `{"max_distance_km": 1\|3\|5\|10, "budget"?, "dietary"?}`: e.g. the search radius chips |
| GET | `/api/chat/sessions/{id}/messages` | Conversation history, including place cards and suggestions |
| DELETE | `/api/chat/sessions/{id}/messages` | Clear history and learned preferences |
| POST | `/api/chat/message` | Send a message; the response is an **SSE stream** (below) |
| POST | `/api/places/search` | Direct local search: `{"property_id", "query", "category_key"?, "radius_m"?, "open_now"?, "limit"?}` → results, provider (`osm`, `osm_snapshot`, `sample`), notice, attribution |

Every `/api/chat/sessions/{id}*` call and `/api/chat/message` requires `X-Session-Token`. A missing or wrong token returns 404, the same response as an unknown session.

### Chat streaming protocol

```http
POST /api/chat/message
X-Session-Token: <token>
Content-Type: application/json

{"session_id": "8d1c…", "message": "Suggest some nice places for dinner nearby."}
```

Response `Content-Type: text/event-stream`:

```
event: meta
data: {"intent": "LOCAL_DISCOVERY_QUERY", "planner": "heuristic", "llm": true, "discovery": "osm"}

event: places
data: {"previous": false, "items": [{"osm_id": "node/123", "name": "…", "category": "Restaurant", "cuisine": "North Indian",
        "distance_km": 1.2, "opening_hours": "Mo-Su 12:00-23:00", "open_now": true, "price_level": null, "address": "…",
        "osm_url": "https://www.openstreetmap.org/node/123", "directions_url": "https://www.google.com/maps/dir/?api=1&origin=28.52…",
        "source": "osm", "host_note": null, "score": 0.71}]}

event: token
data: "Here are a few great dinner spots near your stay:"

event: token
data: "\n\n1. **…**"

event: suggestions
data: ["Anything more budget-friendly?", "Any of these good for families?", "Which is closest?"]

event: done
data: {"message_id": "…", "latency_ms": 2140, "intent": "LOCAL_DISCOVERY_QUERY"}
```

Other events:
- `session`: the guest switched property; carries the new session state.
- `status`: short progress text ("Searching OpenStreetMap for cinema near your stay…", "Writing your answer…").
- `: keep-alive` comment lines are sent every few seconds while the backend is busy; SSE clients ignore them.
- `replace`: the full answer text, sent when a streamed LLM answer failed fact verification and was replaced by the checked (extractive) answer.
- `error`: `{"message": "..."}` if the pipeline failed after the stream started.

Intents: `PROPERTY_QUERY`, `LOCAL_DISCOVERY_QUERY`, `GENERAL_NEIGHBOURHOOD_QUERY`, `MIXED_QUERY`, `GENERAL_CONVERSATION`, `EMERGENCY_QUERY`.

curl example:

```bash
S=$(curl -s -X POST localhost:3000/api/chat/sessions -H 'content-type: application/json' -d '{"location_slug":"saket"}')
ID=$(echo $S | jq -r .session_id); TOK=$(echo $S | jq -r .session_token)
curl -N -X POST localhost:3000/api/chat/message -H "X-Session-Token: $TOK" -H 'content-type: application/json' \
     -d "{\"session_id\":\"$ID\",\"message\":\"What's the checkout time?\"}"
```

## Admin endpoints (Bearer JWT)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/admin/login` | `{"email","password"}` → `{"access_token","expires_at"}` |
| GET | `/api/admin/me` | Current admin |
| GET | `/api/admin/dashboard` | Counts, documents by status, 7-day intents, integration status |
| GET/POST | `/api/admin/locations` | List or create locations (`{"slug","name","tagline"?}`) |
| GET | `/api/admin/properties` | All properties, including inactive, with document/chunk counts |
| POST | `/api/admin/properties` | Create (`PropertyCreate`: name, location_slug, address, latitude, longitude, discovery_radius_m, bedrooms, bathrooms, max_guests, description, amenities[], `details{check_in_out, facilities, house_rules, emergency, neighbourhood}`, guest_pin?, is_placeholder, is_active) |
| GET | `/api/admin/properties/{id}` | Full property (including structured details) |
| PUT | `/api/admin/properties/{id}` | Partial update. `guest_pin: ""` removes the PIN. Re-indexes the profile |
| DELETE | `/api/admin/properties/{id}` | Deactivate; `?hard=true` permanently deletes (cascades documents, chunks, picks) |
| POST | `/api/admin/documents/upload` | `multipart/form-data`: `property_id`, `file` → 202, processed in background |
| GET | `/api/admin/documents?property_id=&include_system=` | Documents with status, chunk_count, uploaded_at, last_indexed_at, error_message |
| GET | `/api/admin/documents/{id}/chunks` | Chunks with section, sensitivity flag and metadata |
| DELETE | `/api/admin/documents/{id}` | Remove the document and its chunks |
| POST | `/api/admin/documents/{id}/reindex` | Re-run extraction and embedding |
| POST | `/api/admin/documents/reindex-all` | Re-embed every document (after changing the embedding model) |
| GET | `/api/admin/geocode?q=…` | Address → coordinates via OpenStreetMap Nominatim (rate limited, 1/s) |
| GET/POST | `/api/admin/curated` | List (`?property_id=`) or create host picks |
| PUT/DELETE | `/api/admin/curated/{id}` | Update or delete a host pick |

The full request and response schemas are in `openapi.json`.
