"""Embedding providers.

* OpenAIEmbedder   - text-embedding-3-small (1536-d) via the official SDK.
* LocalHashEmbedder - deterministic feature-hashing embedder (word unigrams,
  bigrams and character trigrams, signed hashing, L2-normalised). It is a
  *lexical* model used for offline development and automated tests when no
  OPENAI_API_KEY is configured. It performs real vector retrieval but has no
  semantic understanding; use OpenAI in production.

Changing provider changes the vector space: re-index all documents afterwards
(POST /api/admin/documents/reindex-all or `python -m scripts.reindex`).
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Protocol

from app.core.config import Settings, get_settings
from app.core.errors import ServiceUnavailableError

log = logging.getLogger(__name__)


class Embedder(Protocol):
    name: str
    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "a", "an", "the", "is", "are", "was", "be", "to", "of", "and", "or", "in", "on", "at",
    "for", "with", "my", "me", "i", "you", "your", "it", "this", "that", "what", "whats",
    "do", "does", "can", "how", "there", "any", "please", "tell", "about", "s", "we", "our",
}
# Light synonym normalisation so common guest phrasings hit the right facts.
_SYNONYMS = {
    "wifi": "wifi", "wi": "wifi", "fi": "wifi", "internet": "wifi", "wireless": "wifi",
    "checkout": "checkout", "check": "check", "leave": "checkout", "departure": "checkout",
    "checkin": "checkin", "arrival": "checkin", "arrive": "checkin",
    "car": "parking", "park": "parking", "parking": "parking",
    "elevator": "lift", "lift": "lift",
    "ac": "air", "aircon": "air", "conditioner": "air", "conditioning": "air",
    "pets": "pet", "dog": "pet", "dogs": "pet", "cat": "pet",
    "smoke": "smoking", "cigarette": "smoking",
    "guests": "visitors", "visitor": "visitors", "friends": "visitors",
    "doctor": "hospital", "hospitals": "hospital",
    "washer": "washing", "laundry": "washing",
    "rubbish": "garbage", "trash": "garbage", "waste": "garbage",
    "tv": "television",
}


def normalise_terms(text: str) -> str:
    t = text.lower()
    t = re.sub(r"\bwi[\s\-]?fi\b", "wifi", t)
    t = re.sub(r"\bcheck[\s\-_]?out\b", "checkout", t)
    t = re.sub(r"\bcheck[\s\-_]?in\b", "checkin", t)
    return t


def _tokens(text: str) -> list[str]:
    toks = _TOKEN.findall(normalise_terms(text))
    return [_SYNONYMS.get(t, t) for t in toks if t not in _STOP]


class LocalHashEmbedder:
    name = "local-hash"

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def _bucket(self, feature: str) -> tuple[int, float]:
        h = hashlib.blake2b(feature.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little") % self.dim
        sign = 1.0 if h[4] & 1 else -1.0
        return idx, sign

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        toks = _tokens(text)
        feats: list[tuple[str, float]] = [(f"w:{t}", 1.0) for t in toks]
        feats += [(f"b:{a}_{b}", 0.7) for a, b in zip(toks, toks[1:], strict=False)]
        for t in toks:
            padded = f"#{t}#"
            feats += [(f"c:{padded[i:i + 3]}", 0.25) for i in range(len(padded) - 2)]
        for feat, w in feats:
            idx, sign = self._bucket(feat)
            vec[idx] += sign * w
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class OpenAIEmbedder:
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        from openai import AsyncOpenAI

        self.dim = settings.embedding_dim
        self.model = settings.openai_embedding_model
        self._client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=30.0, max_retries=3)

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        import openai

        out: list[list[float]] = []
        try:
            for i in range(0, len(texts), 96):
                batch = [t[:8000] for t in texts[i : i + 96]]
                resp = await self._client.embeddings.create(
                    model=self.model, input=batch, dimensions=self.dim
                )
                out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        except openai.APIError as exc:
            log.error("OpenAI embeddings failed: %s", type(exc).__name__)
            raise ServiceUnavailableError("Embedding service is unavailable.") from exc
        return out

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts)

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text]))[0]


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        s = get_settings()
        if s.resolved_embedding_provider == "openai":
            if not s.openai_api_key:
                raise RuntimeError("EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY")
            _embedder = OpenAIEmbedder(s)
        else:
            _embedder = LocalHashEmbedder(s.embedding_dim)
        log.info("Embedding provider: %s", _embedder.name)
    return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """Override (used by tests)."""
    global _embedder
    _embedder = embedder
