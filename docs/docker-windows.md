# Running on Docker Desktop (Windows)

Everything runs on your own machine. No API keys, accounts, credit cards or paid services are involved.

## What gets installed

| You install | Runs inside Docker (downloaded automatically) |
|---|---|
| **Docker Desktop** (with WSL 2), **Git** | Next.js frontend, FastAPI backend, ChromaDB, Ollama + the language model, the embedding model |

## 1. One-time setup

1. Install **Docker Desktop**: https://www.docker.com/products/docker-desktop. Accept the WSL 2 option and restart if asked.
2. Install **Git**: https://git-scm.com/download/win (default options).
3. Open Docker Desktop and wait until it says *Engine running*.
4. **Give Docker enough memory.** Docker Desktop's WSL 2 VM gets about half of your RAM by default. To change that, create `%UserProfile%\.wslconfig`:
   ```ini
   [wsl2]
   memory=8GB
   ```
   Then run `wsl --shutdown` in PowerShell and restart Docker Desktop.

## 2. Get the code and start

In **PowerShell**:

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

The first run takes a while. It builds the images (about 5–10 minutes, including the ~90 MB embedding model and CPU-only PyTorch) and downloads the language model (about 1.4 GB for the default `qwen3:1.7b`). Later starts take seconds.

Follow the model download:

```bash
docker compose logs -f ollama-pull
```

Then open:

- **Guest chat:** http://localhost:3000
- **Host console:** http://localhost:3000/admin (dev login `admin@example.com` / `ChangeMe-Dev-2026!`; set `ADMIN_EMAIL`/`ADMIN_PASSWORD` in `.env` to choose your own)
- **API docs:** http://localhost:8000/docs

The app is usable before the model finishes downloading: answers quote the property guide directly, and the yellow notice disappears once the model is ready.

## Choosing a model

Check what Docker can use:

```bash
docker compose exec backend python -m scripts.check_hardware
```

| Model | Download | Memory for Docker | Notes |
|---|---|---|---|
| `qwen3:4b` | ~2.6 GB | ≥ 12 GB | Best answers; slower on CPU |
| `qwen3:1.7b` (default) | ~1.4 GB | ≥ 6 GB | Good balance on a typical laptop |
| `gemma3:1b` | ~0.8 GB | ≥ 4 GB | Low-memory machines |
| `qwen3:0.6b` | ~0.5 GB | < 4 GB | Smallest; basic phrasing |

To switch models, set `OLLAMA_MODEL=qwen3:4b` in `.env`, then:

```bash
docker compose up -d
```

`ollama-pull` downloads the new model automatically. Remove a model you no longer use with `docker compose exec ollama ollama rm qwen3:1.7b`.

With an **NVIDIA GPU** (WSL 2 with current NVIDIA drivers), start the stack with the GPU override so Ollama uses the GPU:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

CPU-only is the default and works everywhere; expect a few seconds to about 30 seconds per answer depending on the model and machine. Answers stream as they are written.

## Services, network and volumes

| Service | Image | Published port | Data volume |
|---|---|---|---|
| `frontend` | built from `frontend/` | `3000` (all interfaces) | none |
| `backend` | built from `backend/Dockerfile` | `127.0.0.1:8000` (API docs) | `app_data` → `/data` (SQLite `app.db`, `uploads/`, `discovery/osm_snapshot.json`, `secret_key`) |
| `chromadb` | `chromadb/chroma:1.5.9` | none (internal) | `chroma_data` → `/data` |
| `ollama` | `ollama/ollama:0.40.14` | none (internal) | `ollama_models` → `/root/.ollama` |
| `ollama-pull` | same as `ollama` | none | one-shot model download |

- **Networks:** `internal` connects the backend, ChromaDB and Ollama. `web` connects the frontend and backend. The browser only talks to the frontend, which proxies `/api` to `http://backend:8000`.
- **Data survives** `docker compose down`, restarts and rebuilds. Only `docker compose down -v` deletes the volumes.
- **Health checks** cover every long-running service. The backend waits for healthy ChromaDB and Ollama, and the frontend waits for a healthy backend. All services use `restart: unless-stopped`.

## Everyday commands

```bash
docker compose ps
```

```bash
docker compose logs -f backend
```

```bash
docker compose down
```

```bash
docker compose up -d
```

**Back up your data** (database, uploads, vectors) to the current folder:

```bash
docker run --rm -v hideout-assistant_app_data:/a -v hideout-assistant_chroma_data:/c -v ${PWD}:/backup alpine tar czf /backup/hideout-backup.tgz /a /c
```

**Development mode** (hot reload; Ollama on `localhost:11434`, ChromaDB on `localhost:8001`):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

## Using it from your phone on the same Wi-Fi

Find your PC's IP address with `ipconfig` (e.g. `192.168.1.20`), then open `http://192.168.1.20:3000` on the phone. Allow Docker Desktop through Windows Firewall if prompted.

## What needs the internet

| Needs internet | Works offline |
|---|---|
| First build (images, Python/npm packages, embedding model, fonts) | Property Q&A (RAG): embeddings, ChromaDB, SQLite |
| First model download (`ollama-pull`) | Answer generation with the downloaded Ollama model |
| Live nearby-place search (OpenStreetMap Overpass) | Nearby places from the OSM snapshot or sample data |
| "Find from address" in the admin console (Nominatim) | Admin console, uploads, re-indexing |

**Real places offline:** run this once while online. It saves OpenStreetMap data around every property into the `app_data` volume, and the app uses it whenever live search is unavailable.

```bash
docker compose exec backend python -m scripts.build_osm_snapshot
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| Docker Desktop stuck on *Starting the Docker Engine* | `wsl --shutdown`, `wsl --update`, restart Docker Desktop; enable virtualization in BIOS |
| `port is already allocated` | Set `FRONTEND_PORT=3001` / `BACKEND_PORT=8001` in `.env` |
| `429 Too Many Requests` during build | Docker Hub download limit: wait a few minutes, or sign in to Docker Desktop |
| Yellow notice "model is still downloading" stays | Check `docker compose logs ollama-pull`; retry with `docker compose run --rm ollama-pull` |
| Chat says "Can't reach the assistant right now" after ~30 s | Fixed in this version (the frontend relay allowed only 30 s per answer). Rebuild the frontend: `docker compose up --build -d` |
| Answers are very slow | Use a smaller model (`OLLAMA_MODEL=qwen3:0.6b`) or give Docker more memory/CPUs |
| Ollama container restarts / out of memory | Smaller model, or more memory in `.wslconfig` |
| Places say "Live map search didn't respond" (or show "Sample · fictional") | Run `docker compose exec backend python -m scripts.check_discovery` to see what each OpenStreetMap server returned. The dashboard also shows the last error. Public Overpass servers are sometimes busy: try again, add another server in `OVERPASS_URLS`, and build a snapshot (`python -m scripts.build_osm_snapshot`) while it works |
| Want to start completely fresh | `docker compose down -v` (deletes all data and the downloaded model) |
| Backend unhealthy | `docker compose logs backend`; usually ChromaDB not ready yet; `docker compose restart backend` |
