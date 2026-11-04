# Configuration (environment variables)

All settings are optional. Put overrides in `.env` next to `docker-compose.yml` (see `.env.example`). None of them is an API key.

| Variable | Default | Purpose |
|---|---|---|
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | dev account `admin@example.com` / `ChangeMe-Dev-2026!` | First admin account, created on first start |
| `SECRET_KEY` | auto-generated, kept in `/data/secret_key` | Signs admin login tokens |
| `OLLAMA_MODEL` | `qwen3:1.7b` | Local LLM; downloaded by the `ollama-pull` service |
| `OLLAMA_URL` | `http://ollama:11434` in Docker | Ollama endpoint |
| `OLLAMA_KEEP_ALIVE` | `30m` | How long the model stays loaded |
| `OLLAMA_NUM_CTX` | `4096` | Context window passed to the model |
| `OLLAMA_TEMPERATURE` | `0.3` | Lower = more literal answers |
| `OLLAMA_DISABLE_THINKING` | `true` | Ask Qwen3-style models to answer directly (much faster on CPU) |
| `LLM_ENABLED` | `true` | `false` = extractive answers only |
| `LLM_ROUTING` | `true` | Use the LLM to classify messages the rules can't place |
| `VERIFY_LLM_ANSWERS` | `true` | Replace answers with unsupported facts / unknown place names |
| `EMBEDDING_PROVIDER` | `sentence-transformers` | `hash` = lexical fallback (tests) |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Baked into the image at build time |
| `CHROMA_MODE` | `http` in Docker, `persistent` locally | `http` (container), `persistent` (embedded on disk), `memory` |
| `CHROMA_HOST` / `CHROMA_PORT` | `chromadb` / `8000` | ChromaDB service |
| `DATA_DIR` | `/data` in Docker, `./.data` locally | SQLite DB, uploads, OSM snapshot, secret key |
| `DATABASE_URL` | `sqlite+aiosqlite:///<DATA_DIR>/app.db` | Override only if you know you need to |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `900` / `150` | Characters per chunk / overlap |
| `RETRIEVAL_TOP_K` | `6` | Chunks given to the model |
| `DISCOVERY_LIVE` | `true` | Live OpenStreetMap (Overpass) searches |
| `OVERPASS_URLS` | overpass-api.de, overpass.private.coffee, overpass.kumi.systems | Comma-separated Overpass servers, tried in order until one answers |
| `NOMINATIM_URL` | `https://nominatim.openstreetmap.org` | Geocoding in the admin console |
| `OSM_USER_AGENT` | generic | **Set your business name and email** (OSM usage policy) |
| `DISCOVERY_SAMPLE_DATA` | `false` | Fictional sample places as the last fallback (demo only) |
| `DEFAULT_DISCOVERY_RADIUS_M` | `3000` | Used when a property has no radius set |
| `ALLOWED_RADII_KM` | `1,3,5,10` | Radius chips shown to guests |
| `DISCOVERY_CACHE_TTL_SECONDS` | `3600` | Cache for live results |
| `RANK_WEIGHT_*` | relevance .40, distance .35, open .10, preference .10, completeness .05 | Recommendation ranking |
| `RATE_LIMIT_CHAT_PER_MINUTE` | `20` | Per IP |
| `MAX_UPLOAD_MB` | `10` | Document size limit |
| `AUTO_SEED_SAMPLE_DATA` | `true` | Load the fictional demo properties into an empty database |
| `FRONTEND_PORT` / `BACKEND_PORT` | `3000` / `8000` | Host ports |
| `SYSTEM_PROMPT_PATH` | `backend/app/prompts/system_prompt.md` | Use your own assistant personality/rules |

Changing `EMBEDDING_MODEL` requires rebuilding the backend image (`docker compose build backend`). Existing documents are re-indexed automatically on the next start.
