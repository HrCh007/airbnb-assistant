# Local development without Docker

Useful for working on the code. For normal use, run the Docker stack instead (see [docker-windows.md](docker-windows.md)).

You need Python 3.12+, Node.js 22+ and, optionally, Ollama for Windows/macOS/Linux (https://ollama.com/download).

## Backend

ChromaDB runs embedded on disk here (`CHROMA_MODE=persistent`), so no database server is needed.

```bash
cd backend
python -m venv .venv
```

Activate it with `.venv\Scripts\activate` (Windows) or `source .venv/bin/activate` (macOS/Linux).

```bash
pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0
```

```bash
pip install -r requirements-dev.txt
```

```bash
uvicorn app.main:app --reload --port 8000
```

Data goes to `backend/.data/` (SQLite, uploads, Chroma). On first start the three fictional demo properties are created and the embedding model is downloaded (about 90 MB, once).

For answers from a local model, install Ollama and run `ollama pull qwen3:1.7b`. The backend uses `OLLAMA_URL=http://localhost:11434` by default.

## Frontend

```bash
cd frontend
npm install
```

```bash
npm run dev
```

Open http://localhost:3000. The dev server proxies `/api` to `BACKEND_INTERNAL_URL` (default `http://localhost:8000`).

## Useful scripts (run in `backend/`)

| Command | Purpose |
|---|---|
| `python -m scripts.seed --reset` | Wipe properties/documents and reload the demo data |
| `python -m scripts.create_admin --email you@example.com` | Create or reset an admin account |
| `python -m scripts.build_osm_snapshot` | Save real OpenStreetMap places around your properties |
| `python scripts/check_hardware.py` | Recommend an Ollama model for this machine |
| `python -m scripts.evaluate` | Run the evaluation set and write `docs/evaluation_report.md` |
