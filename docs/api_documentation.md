# API documentation

Interactive docs: `GET /docs` (Swagger UI) and `GET /redoc`. Machine-readable: [`openapi.json`](openapi.json).

Base URL: `http://localhost:8000` locally. All bodies are JSON unless noted. Errors use `{"detail": "..."}` with standard status codes: 401 (admin auth), 404, 413 (file too large), 415 (unsupported file), 422 (validation), 429 (rate limited, with `Retry-After`) and 503 (database or upstream unavailable).

## Guest endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | `{"status":"ok","database":true}` |
| GET | `/api/status` | Which providers are active (`llm`, `embeddings`, `places`), plus quick-action prompts |
| GET | `/api/locations` | Active locations with property counts |
| GET | `/api/properties?location=saket` | Public property summaries (never includes sensitive details) |
| GET | `/api/properties/{property_id}` | One public property summary |
| POST | `/api/chat/sessions` | Create a session. Body (optional): `{"location_slug": "saket"}` or `{"property_id": "…"}`. Returns `session_id` and a one-time **`session_token`** |
| GET | `/api/chat/sessions/{id}` | Session state (location, property, available properties, preferences, `pin_verified`). Header `X-Session-Token` |
| PUT | `/api/chat/sessions/{id}/selection` | Select or change location/property: `{"location_slug": "dwarka"}` (auto-selects a sole property) or `{"property_id": "…"}` |
| POST | `/api/chat/sessions/{id}/verify-pin` | `{"pin": "2468"}` unlocks sensitive facts for the current property (403 on mismatch; rate limited) |
| GET | `/api/chat/sessions/{id}/messages` | Conversation history, including place cards and suggestions |
| DELETE | `/api/chat/sessions/{id}/messages` | Clear history and learned preferences |
| POST | `/api/chat/message` | Send a message; the response is an **SSE stream** (below) |
| POST | `/api/places/search` | Direct local search: `{"property_id", "query", "included_type"?, "radius_m"?, "open_now"?, "max_price_level"?, "min_rating"?, "limit"?}` |
| GET | `/api/places/details/{place_id}` | Place Details (Google), 404 if unavailable |

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
data: {"intent": "LOCAL_DISCOVERY_QUERY", "planner": "llm", "llm": true, "places_provider": "google_places"}

event: places
data: {"previous": false, "items": [{"place_id": "ChIJ…", "name": "…", "category": "Restaurant", "distance_km": 1.2,
        "rating": 4.4, "user_rating_count": 812, "price_level": 2, "open_now": true, "address": "…",
        "maps_url": "https://maps.google.com/?cid=…", "directions_url": "https://www.google.com/maps/dir/?api=1&origin=28.52…",
        "source": "google_places", "host_note": null, "score": 0.71}]}

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
- `error`: `{"message": "..."}` if the pipeline failed after the stream started.

Intents: `PROPERTY_QUERY`, `LOCAL_DISCOVERY_QUERY`, `GENERAL_NEIGHBOURHOOD_QUERY`, `MIXED_QUERY`, `GENERAL_CONVERSATION`, `EMERGENCY_QUERY`.

curl example:

```bash
S=$(curl -s -X POST localhost:8000/api/chat/sessions -H 'content-type: application/json' -d '{"location_slug":"saket"}')
ID=$(echo $S | jq -r .session_id); TOK=$(echo $S | jq -r .session_token)
curl -N -X POST localhost:8000/api/chat/message -H "X-Session-Token: $TOK" -H 'content-type: application/json' \
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
| POST | `/api/admin/documents/reindex-all` | Re-index everything (after changing embedding provider) |
| GET/POST | `/api/admin/curated` | List (`?property_id=`) or create host picks |
| PUT/DELETE | `/api/admin/curated/{id}` | Update or delete a host pick |

The full request and response schemas are in `openapi.json`.
