"""Local AI components without network access:

* Ollama client against a mocked HTTP transport (exact request shape, streaming,
  <think> filtering, missing model, errors, JSON classification).
* SentenceTransformerEmbedder with a tiny randomly-initialised BERT built on
  the fly (the real all-MiniLM-L6-v2 is downloaded in the Docker build).
"""

from __future__ import annotations

import json
import math

import httpx
import pytest

from app.core.config import get_settings
from app.llm import ollama


def _ndjson(parts: list[str]) -> bytes:
    lines = [json.dumps({"message": {"role": "assistant", "content": p}, "done": False}) for p in parts]
    lines.append(json.dumps({"message": {"role": "assistant", "content": ""}, "done": True}))
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture
def mock_ollama(monkeypatch):
    captured: list[dict] = []
    responses: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        captured.append({"method": request.method, "path": request.url.path, "body": body})
        resp = responses.pop(0)
        return resp(request) if callable(resp) else resp

    ollama.set_client(httpx.AsyncClient(base_url="http://ollama.test", transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(get_settings(), "ollama_model", "qwen3:1.7b")
    yield captured, responses
    ollama.set_client(None)


async def test_status_detects_downloaded_model(mock_ollama):
    captured, responses = mock_ollama
    responses.append(httpx.Response(200, json={"models": [{"name": "qwen3:1.7b"}, {"name": "gemma3:1b"}]}))
    st = await ollama.status(force=True)
    assert st["reachable"] and st["model_ready"] and captured[0]["path"] == "/api/tags"
    responses.append(httpx.Response(200, json={"models": [{"name": "gemma3:1b"}]}))
    assert (await ollama.status(force=True))["model_ready"] is False


async def test_status_unreachable(mock_ollama):
    _, responses = mock_ollama
    responses.append(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    st = await ollama.status(force=True)
    assert st == {"enabled": True, "reachable": False, "model": "qwen3:1.7b", "model_ready": False, "models": []}


async def test_stream_chat_request_shape_and_think_filter(mock_ollama):
    captured, responses = mock_ollama
    responses.append(httpx.Response(200, content=_ndjson(["<think>internal", " reasoning</think>", "Wi-Fi password: ", "abc-123"])))
    out = "".join([t async for t in ollama.stream_chat([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "CONTEXT")])
    assert out == "Wi-Fi password: abc-123"
    body = captured[0]["body"]
    assert captured[0]["path"] == "/api/chat"
    assert body["model"] == "qwen3:1.7b" and body["stream"] is True
    assert body["think"] is False  # Qwen3: answer directly (fast on CPU)
    assert body["messages"][0]["role"] == "system" and "local guide" in body["messages"][0]["content"]
    assert body["messages"][-1] == {"role": "user", "content": "CONTEXT"}
    assert body["options"]["num_ctx"] == get_settings().ollama_num_ctx


async def test_non_thinking_models_get_no_think_flag(mock_ollama, monkeypatch):
    captured, responses = mock_ollama
    monkeypatch.setattr(get_settings(), "ollama_model", "gemma3:1b")
    responses.append(httpx.Response(200, content=_ndjson(["ok"])))
    _ = [t async for t in ollama.stream_chat([], "q")]
    assert "think" not in captured[0]["body"]


async def test_missing_model_and_errors_raise_before_first_token(mock_ollama):
    _, responses = mock_ollama
    responses.append(httpx.Response(404, json={"error": "model 'qwen3:1.7b' not found"}))
    with pytest.raises(ollama.LLMUnavailable, match="model_not_downloaded"):
        _ = [t async for t in ollama.stream_chat([], "q")]
    responses.append(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    with pytest.raises(ollama.LLMUnavailable):
        _ = [t async for t in ollama.stream_chat([], "q")]


async def test_classify_uses_json_schema(mock_ollama):
    captured, responses = mock_ollama
    responses.append(httpx.Response(200, json={"message": {"content": '{"intent": "LOCAL_DISCOVERY_QUERY", "search_query": "rooftop bar"}'}}))
    res = await ollama.classify("somewhere with a view for drinks", "Property: X")
    assert res == {"intent": "LOCAL_DISCOVERY_QUERY", "search_query": "rooftop bar"}
    body = captured[0]["body"]
    assert body["stream"] is False and body["format"]["properties"]["intent"]["enum"]
    assert body["options"]["temperature"] == 0
    responses.append(httpx.Response(200, json={"message": {"content": '{"intent": "MADE_UP"}'}}))
    with pytest.raises(ollama.LLMUnavailable):
        await ollama.classify("x", "")


def test_think_filter_handles_split_tags():
    f = ollama._ThinkFilter()
    out = "".join(f.feed(p) for p in ["Hel", "lo <thi", "nk>secret</th", "ink> world"]) + f.flush()
    assert out == "Hello  world"


# ------------------------------------------------------------------ sentence-transformers
@pytest.fixture(scope="module")
def tiny_st_model(tmp_path_factory):
    pytest.importorskip("sentence_transformers")
    from sentence_transformers import SentenceTransformer, models
    from transformers import BertConfig, BertModel, BertTokenizerFast

    d = tmp_path_factory.mktemp("tiny-bert")
    words = "wifi password check out time parking pets allowed the is what are there a".split()
    (d / "vocab.txt").write_text("\n".join(["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", *words]))
    BertTokenizerFast(vocab_file=str(d / "vocab.txt")).save_pretrained(d)
    cfg = BertConfig(vocab_size=5 + len(words), hidden_size=32, num_hidden_layers=1, num_attention_heads=2, intermediate_size=64)
    BertModel(cfg).save_pretrained(d)
    st = SentenceTransformer(modules=[models.Transformer(str(d)), models.Pooling(32)])
    out = tmp_path_factory.mktemp("tiny-st")
    st.save(str(out))
    return str(out)


async def test_sentence_transformer_embedder(tiny_st_model):
    from app.rag.embeddings import SentenceTransformerEmbedder

    e = SentenceTransformerEmbedder(tiny_st_model, "cpu")
    assert e.dim == 32 and e.name.startswith("st:")
    docs = await e.embed_documents(["wifi password", "parking is allowed"])
    q = await e.embed_query("wifi password")
    assert len(docs) == 2 and all(len(v) == 32 for v in docs)
    assert math.isclose(sum(x * x for x in q), 1.0, rel_tol=1e-4)  # normalised
    assert await e.embed_query("wifi password") == q  # deterministic
    assert await e.embed_documents([]) == []


def test_embedder_falls_back_when_model_unavailable(monkeypatch):
    from app.rag import embeddings

    monkeypatch.setattr(get_settings(), "embedding_provider", "sentence-transformers")
    monkeypatch.setattr(get_settings(), "embedding_model", "/nonexistent/model/path")
    embeddings.set_embedder(None)
    try:
        e = embeddings.get_embedder()
        assert e.name == "local-hash" and e.dim == 384
    finally:
        embeddings.set_embedder(None)
