"""Runtime status of the local components (shown in /api/status and the dashboard)."""

from __future__ import annotations

from app.core.config import get_settings
from app.llm import ollama
from app.location.providers import get_snapshot
from app.rag import vectorstore
from app.rag.embeddings import get_embedder


async def runtime_status() -> dict:
    s = get_settings()
    llm = await ollama.status()
    if not llm["enabled"]:
        llm_state = "disabled"
    elif not llm["reachable"]:
        llm_state = "unreachable"
    elif not llm["model_ready"]:
        llm_state = "model_not_downloaded"
    else:
        llm_state = "ready"
    snapshot = get_snapshot()
    return {
        "llm": {"provider": "ollama", "model": s.ollama_model, "state": llm_state, "installed_models": llm["models"]},
        "embeddings": {"provider": get_embedder().name, "dimensions": get_embedder().dim},
        "vector_store": {"provider": f"chromadb ({s.chroma_mode})", "reachable": await vectorstore.heartbeat()},
        "database": "sqlite",
        "discovery": {
            "live_osm": s.discovery_live,
            "osm_snapshot": snapshot.available,
            "sample_data": s.discovery_sample_data,
        },
        "paid_apis": "none",
    }
