"""Local embedding providers (no API keys, no network at query time).

* SentenceTransformerEmbedder - runs a sentence-transformers model on the local
  CPU (default: all-MiniLM-L6-v2, 384 dimensions). The Docker image downloads
  the model at build time, so it works offline afterwards.
* LocalHashEmbedder - deterministic feature-hashing embedder (word unigrams,
  bigrams, character trigrams). Lexical only; used by the automated tests and
  as an automatic fallback if the model can't be loaded.

Vectors from different providers/models are not comparable: each ChromaDB
collection records the embedder that produced it, and the app re-indexes
automatically when that changes (see rag/vectorstore.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from typing import Protocol

from app.core.config import get_settings

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


class SentenceTransformerEmbedder:
    """sentence-transformers model, executed in a worker thread so the event loop
    stays responsive. Embeddings are L2-normalised (cosine similarity)."""

    def __init__(self, model_name: str, device: str = "cpu") -> None:
        from sentence_transformers import SentenceTransformer  # heavy import, done lazily

        self._model = SentenceTransformer(model_name, device=device)
        getter = getattr(self._model, "get_embedding_dimension", None) or getattr(
            self._model, "get_sentence_embedding_dimension", lambda: None
        )
        dim = getter()
        self.dim = int(dim) if dim else len(self._model.encode(["x"])[0])
        self.name = f"st:{model_name.rsplit('/', 1)[-1]}"

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(
            texts, batch_size=32, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
        )
        return [v.tolist() for v in vecs]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._encode, texts) if texts else []

    async def embed_query(self, text: str) -> list[float]:
        return (await asyncio.to_thread(self._encode, [text]))[0]


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """The configured embedder; falls back to the hash embedder (with a warning)
    if the sentence-transformers model can't be loaded, so the app keeps working."""
    global _embedder
    if _embedder is None:
        s = get_settings()
        if s.embedding_provider == "sentence-transformers":
            try:
                _embedder = SentenceTransformerEmbedder(s.embedding_model, s.embedding_device)
            except Exception as exc:  # missing package, model not downloaded and offline, ...
                log.warning(
                    "Could not load embedding model %s (%s); falling back to the lexical hash embedder. "
                    "Retrieval quality is reduced until the model is available.",
                    s.embedding_model, type(exc).__name__,
                )
                _embedder = LocalHashEmbedder(384)
        else:
            _embedder = LocalHashEmbedder(384)
        log.info("Embedding provider: %s (%d dimensions)", _embedder.name, _embedder.dim)
    return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """Override (used by tests)."""
    global _embedder
    _embedder = embedder
