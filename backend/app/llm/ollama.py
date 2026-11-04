"""Ollama client (local LLM over HTTP, no API key).

Endpoints used: GET /api/tags (health + is the model downloaded?), POST /api/chat
(streamed NDJSON answers, and JSON-schema constrained classification).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
# Model families that "think" before answering; we ask them not to (much faster on CPU).
_THINKING_FAMILIES = ("qwen3", "deepseek-r1", "gpt-oss", "magistral")


class LLMUnavailable(Exception):
    """Ollama unreachable, model not downloaded, or a failed request."""


@lru_cache
def system_prompt() -> str:
    path = get_settings().system_prompt_path or str(PROMPTS_DIR / "system_prompt.md")
    return Path(path).read_text(encoding="utf-8").strip()


@lru_cache
def router_prompt() -> str:
    return (PROMPTS_DIR / "router_prompt.md").read_text(encoding="utf-8").strip()


_client: httpx.AsyncClient | None = None
_status_cache: tuple[float, dict] | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        s = get_settings()
        _client = httpx.AsyncClient(
            base_url=s.ollama_url.rstrip("/"),
            timeout=httpx.Timeout(s.ollama_timeout_seconds, connect=5.0),
        )
    return _client


def set_client(client: httpx.AsyncClient | None) -> None:
    """Override (tests)."""
    global _client, _status_cache
    _client = client
    _status_cache = None


def _model_matches(wanted: str, available: str) -> bool:
    w = wanted if ":" in wanted else f"{wanted}:latest"
    a = available if ":" in available else f"{available}:latest"
    return w == a


async def status(force: bool = False) -> dict:
    """{"enabled", "reachable", "model", "model_ready", "models"} - cached for 15 s."""
    global _status_cache
    s = get_settings()
    if not s.llm_enabled:
        return {"enabled": False, "reachable": False, "model": s.ollama_model, "model_ready": False, "models": []}
    if not force and _status_cache and time.monotonic() - _status_cache[0] < 15:
        return _status_cache[1]
    info = {"enabled": True, "reachable": False, "model": s.ollama_model, "model_ready": False, "models": []}
    try:
        r = await get_client().get("/api/tags", timeout=5.0)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
        info.update(reachable=True, models=names, model_ready=any(_model_matches(s.ollama_model, n) for n in names))
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("Ollama not reachable: %s", type(exc).__name__)
    _status_cache = (time.monotonic(), info)
    return info


def _base_payload(messages: list[dict]) -> dict:
    s = get_settings()
    payload: dict = {
        "model": s.ollama_model,
        "messages": messages,
        "keep_alive": s.ollama_keep_alive,
        "options": {"temperature": s.ollama_temperature, "num_ctx": s.ollama_num_ctx},
    }
    if s.ollama_disable_thinking and s.ollama_model.split(":")[0].lower().startswith(_THINKING_FAMILIES):
        payload["think"] = False
    return payload


class _ThinkFilter:
    """Drops <think>...</think> blocks from a token stream (older Ollama/model combos)."""

    def __init__(self) -> None:
        self.buf = ""
        self.inside = False

    def feed(self, text: str) -> str:
        self.buf += text
        out = ""
        while self.buf:
            if self.inside:
                end = self.buf.find("</think>")
                if end == -1:
                    self.buf = self.buf[-8:]  # keep a tail in case the tag is split
                    return out
                self.buf = self.buf[end + len("</think>"):]
                self.inside = False
            else:
                start = self.buf.find("<think>")
                if start == -1:
                    # Hold back a possible partial "<think" at the end.
                    safe = len(self.buf) - 7 if "<" in self.buf[-7:] else len(self.buf)
                    out += self.buf[:safe]
                    self.buf = self.buf[safe:]
                    return out
                out += self.buf[:start]
                self.buf = self.buf[start + len("<think>"):]
                self.inside = True
        return out

    def flush(self) -> str:
        rest = "" if self.inside else self.buf
        self.buf = ""
        return rest


async def stream_chat(history: list[dict], user_turn: str) -> AsyncIterator[str]:
    """Stream the answer. Raises LLMUnavailable before the first token on failure."""
    messages = [{"role": "system", "content": system_prompt()}, *history, {"role": "user", "content": user_turn}]
    payload = {**_base_payload(messages), "stream": True}
    started = False
    filt = _ThinkFilter()
    try:
        async with get_client().stream("POST", "/api/chat", json=payload) as resp:
            if resp.status_code == 404:
                raise LLMUnavailable("model_not_downloaded")
            if resp.status_code >= 400:
                body = (await resp.aread())[:200]
                raise LLMUnavailable(f"ollama_status_{resp.status_code}: {body!r}")
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if data.get("error"):
                    raise LLMUnavailable(f"ollama_error: {str(data['error'])[:120]}")
                piece = filt.feed((data.get("message") or {}).get("content", ""))
                if piece:
                    started = True
                    yield piece
                if data.get("done"):
                    break
        tail = filt.flush()
        if tail:
            yield tail
    except LLMUnavailable:
        if started:
            yield "\n\n_(Sorry, my answer was cut short. Please ask again.)_"
            return
        raise
    except httpx.HTTPError as exc:
        log.warning("Ollama request failed: %s", type(exc).__name__)
        if not started:
            raise LLMUnavailable(type(exc).__name__) from exc
        yield "\n\n_(Sorry, my answer was cut short. Please ask again.)_"


_ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "PROPERTY_QUERY", "LOCAL_DISCOVERY_QUERY", "GENERAL_NEIGHBOURHOOD_QUERY",
                "MIXED_QUERY", "GENERAL_CONVERSATION", "EMERGENCY_QUERY",
            ],
        },
        "search_query": {"type": "string"},
    },
    "required": ["intent", "search_query"],
}


async def classify(message: str, context: str) -> dict:
    """Ask the local model for {intent, search_query}. Raises LLMUnavailable on failure."""
    messages = [
        {"role": "system", "content": router_prompt()},
        {"role": "user", "content": f"Context: {context}\nGuest message: {message}"},
    ]
    payload = {**_base_payload(messages), "stream": False, "format": _ROUTER_SCHEMA}
    payload["options"] = {**payload["options"], "temperature": 0}
    try:
        r = await get_client().post("/api/chat", json=payload, timeout=60.0)
        if r.status_code >= 400:
            raise LLMUnavailable(f"ollama_status_{r.status_code}")
        content = (r.json().get("message") or {}).get("content", "")
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        data = json.loads(content)
        if data.get("intent") not in _ROUTER_SCHEMA["properties"]["intent"]["enum"]:
            raise LLMUnavailable("bad_intent")
        return data
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise LLMUnavailable(type(exc).__name__) from exc
