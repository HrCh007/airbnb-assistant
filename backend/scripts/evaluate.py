"""Evaluate the assistant on data/eval/eval_set.json and write a report.

    python -m scripts.evaluate                  # uses the current environment
    python -m scripts.evaluate --offline-maps   # use the bundled sample places (no Overpass)

Runs every case through the real HTTP API (in-process ASGI) against the
seeded database and computes:

* Retrieval accuracy  - hit@k and MRR: is the expected fact in the retrieved
                        chunks for this property? (property-type cases)
* Intent accuracy     - planner intent vs label
* Answer relevance    - expected strings present in the answer
* Groundedness        - share of "fact tokens" in answers (times, codes,
                        numbers, phone numbers) that appear verbatim in the
                        retrieved context or place results
* Hallucination rate  - unanswerable / cross-property cases where the answer
                        invents an answer or leaks another property's data
* Latency             - end-to-end p50 / p95 per message

Results: data/eval/results.json and docs/evaluation_report.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "data" / "eval" / "eval_set.json"
NF_MARK = "don't have confirmed information"
# Times ("11:00 AM"), codes ("7391", "saket-demo-2468", "B-12"), phone numbers and
# distances ("1.5 km", "500 m"): concrete facts that must come from the context.
FACT_TOKEN = re.compile(r"\+?\d[\d\-:.]*(?:\s?(?:AM|PM|km|m)\b)?|[a-z]+-demo-\d+|[A-Z]-\d+", re.I)


async def run(offline_maps: bool) -> dict:
    if offline_maps:
        os.environ["DISCOVERY_LIVE"] = "false"
    os.environ.setdefault("RATE_LIMIT_CHAT_PER_MINUTE", "100000")
    os.environ.setdefault("RATE_LIMIT_DEFAULT_PER_MINUTE", "100000")

    import httpx
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.core.database import dispose_engine, get_sessionmaker
    from app.llm import ollama
    from app.location.geo import format_distance
    from app.main import create_app
    from app.models import KnowledgeChunk, Message, Property
    from app.rag.retriever import retrieve
    from app.services.bootstrap import bootstrap

    s = get_settings()
    await bootstrap()
    llm = await ollama.status(force=True)
    cases = json.loads(EVAL.read_text())["cases"]
    app = create_app()
    results = []

    async with get_sessionmaker()() as db:
        props = {p.location.slug: p for p in (await db.execute(select(Property).where(Property.slug.like("demo-%")))).scalars()}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://eval", timeout=120) as client:
        for case in cases:
            r = await client.post("/api/chat/sessions", json={})
            sess = r.json()
            headers = {"X-Session-Token": sess["session_token"]}
            await client.put(f"/api/chat/sessions/{sess['session_id']}/selection", json={"location_slug": case["location"]}, headers=headers)

            t0 = time.perf_counter()
            text, places, intent, event = "", [], None, None
            async with client.stream("POST", "/api/chat/message", json={"session_id": sess["session_id"], "message": case["question"]}, headers=headers) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                        if event == "token":
                            text += data
                        elif event == "places":
                            places = data["items"]
                        elif event == "meta":
                            intent = data["intent"]
            latency = (time.perf_counter() - t0) * 1000

            prop = props[case["location"]]
            async with get_sessionmaker()() as db:
                allow = not prop.guest_pin_hash
                # Retrieval metric: the raw question against this property's knowledge base.
                ret = await retrieve(db, prop.id, case["question"], allow_sensitive=allow)
                # Groundedness: grade against exactly what the answer was built from
                # (chunks cited in the assistant message's trace + places as displayed).
                msg = (await db.execute(
                    select(Message).where(Message.conversation_id == uuid.UUID(sess["session_id"]), Message.role == "assistant")
                    .order_by(Message.created_at.desc()).limit(1)
                )).scalar_one()
                cited = [uuid.UUID(src["chunk_id"]) for src in msg.metadata_.get("sources", [])]
                cited_text = [c for c in (await db.execute(select(KnowledgeChunk.content).where(KnowledgeChunk.id.in_(cited)))).scalars()] if cited else []
            place_text = [
                " ".join(str(x) for x in [p["name"], format_distance(p.get("distance_km")), p.get("rating"), p.get("address")] if x)
                for p in places
            ]
            context = "\n".join(cited_text + place_text)

            res = {"category": case["category"], "location": case["location"], "question": case["question"],
                   "intent": intent, "expected_intent": case["expected_intent"], "answer": text,
                   "latency_ms": round(latency), "places": len(places)}

            # Retrieval hit@k / reciprocal rank
            fact = case.get("expected_fact_in_context")
            if fact:
                rank = next((i + 1 for i, c in enumerate(ret.chunks) if fact.lower() in c.content.lower()), None)
                res["retrieval_hit"] = rank is not None
                res["reciprocal_rank"] = 1 / rank if rank else 0.0

            # Answer relevance
            exp = case.get("expected_answer_contains")
            if exp:
                if exp == ["NOT_FOUND"]:
                    res["relevant"] = NF_MARK in text
                else:
                    res["relevant"] = all(e.lower() in text.lower() for e in exp)
            if case.get("expects_places"):
                res["places_ok"] = len(places) > 0

            # Hallucination
            hallucinated = False
            if exp == ["NOT_FOUND"]:
                hallucinated = NF_MARK not in text
            for bad in case.get("must_not_contain", []):
                if bad.lower() in text.lower():
                    hallucinated = True
            if case["category"] in {"hallucination", "isolation"}:
                res["hallucinated"] = hallucinated

            # Groundedness of factual tokens
            tokens = {t.strip().rstrip(".") for t in FACT_TOKEN.findall(text) if len(t.strip().rstrip(".")) >= 3}
            tokens -= {"112", "102", "108", "100", "101"}  # national emergency numbers come from the system prompt
            if tokens:
                supported = [t for t in tokens if t.lower() in context.lower()]
                res["grounded_fraction"] = len(supported) / len(tokens)
                res["unsupported_tokens"] = sorted(tokens - set(supported))
            results.append(res)

    await dispose_engine()

    def pct(xs):
        xs = list(xs)
        return round(100 * sum(xs) / len(xs), 1) if xs else None

    lat = [r["latency_ms"] for r in results]
    summary = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": {"llm": f"ollama:{s.ollama_model}" if llm["model_ready"] else "extractive (Ollama model not available)",
                 "embeddings": __import__("app.rag.embeddings", fromlist=["get_embedder"]).get_embedder().name,
                 "places": "live OpenStreetMap" if s.discovery_live else "sample/snapshot datasets"},
        "cases": len(results),
        "retrieval_hit_at_k": pct(r["retrieval_hit"] for r in results if "retrieval_hit" in r),
        "retrieval_mrr": round(statistics.mean(r["reciprocal_rank"] for r in results if "reciprocal_rank" in r), 3),
        "intent_accuracy": pct(r["intent"] == r["expected_intent"] for r in results),
        "answer_relevance": pct(r["relevant"] for r in results if "relevant" in r),
        "local_results_returned": pct(r["places_ok"] for r in results if "places_ok" in r),
        "groundedness": pct(r["grounded_fraction"] == 1.0 for r in results if "grounded_fraction" in r),
        "hallucination_rate": pct(r["hallucinated"] for r in results if "hallucinated" in r),
        "latency_ms_p50": round(statistics.median(lat)),
        "latency_ms_p95": round(sorted(lat)[max(0, int(len(lat) * 0.95) - 1)]),
        "top_k": s.retrieval_top_k,
    }
    return {"summary": summary, "results": results}


def write_report(data: dict) -> Path:
    s = data["summary"]
    rows = data["results"]
    lines = [
        "# Evaluation report",
        "",
        f"Generated {s['generated_at']} by `python -m scripts.evaluate` over `data/eval/eval_set.json` "
        f"({s['cases']} cases on the **fictional** sample dataset).",
        "",
        f"**Mode:** LLM = {s['mode']['llm']} · embeddings = {s['mode']['embeddings']} · places = {s['mode']['places']}",
        "",
        "| Metric | Value | Definition |",
        "|---|---|---|",
        f"| Retrieval accuracy (hit@{s['top_k']}) | {s['retrieval_hit_at_k']}% | Expected fact present in the property's retrieved chunks |",
        f"| Retrieval MRR | {s['retrieval_mrr']} | Mean reciprocal rank of the first chunk containing the fact |",
        f"| Intent accuracy | {s['intent_accuracy']}% | Planner intent equals the label |",
        f"| Answer relevance | {s['answer_relevance']}% | Answer contains the expected facts (or declines when it should) |",
        f"| Local results returned | {s['local_results_returned']}% | Local/mixed/emergency cases that produced place cards |",
        f"| Groundedness | {s['groundedness']}% | Answers whose factual tokens (times, codes, numbers) all appear in retrieved context |",
        f"| Hallucination rate | {s['hallucination_rate']}% | Unanswerable/isolation cases that invented an answer or leaked another property's data |",
        f"| Latency p50 / p95 | {s['latency_ms_p50']} / {s['latency_ms_p95']} ms | End-to-end per message (in-process API) |",
        "",
        "## Failures and notes",
        "",
    ]
    misses = [r for r in rows if r["intent"] != r["expected_intent"] or r.get("relevant") is False or r.get("hallucinated") or r.get("retrieval_hit") is False or r.get("places_ok") is False or r.get("grounded_fraction", 1) < 1]
    if not misses:
        lines.append("No failing cases.")
    for r in misses:
        why = []
        if r["intent"] != r["expected_intent"]:
            why.append(f"intent {r['intent']} (expected {r['expected_intent']})")
        if r.get("retrieval_hit") is False:
            why.append("expected fact not retrieved")
        if r.get("relevant") is False:
            why.append("expected answer content missing")
        if r.get("places_ok") is False:
            why.append("no place results")
        if r.get("hallucinated"):
            why.append("HALLUCINATION / leak")
        if r.get("grounded_fraction", 1) < 1:
            why.append(f"unsupported tokens {r['unsupported_tokens']}")
        lines.append(f"- **[{r['location']}] {r['question']}** — {'; '.join(why)}")
    lines += [
        "",
        "## How to read this",
        "",
        "- In **extractive mode** (no Ollama model) answers are quoted from retrieved chunks, so groundedness is high by",
        "  construction; this run mainly measures routing, retrieval and isolation. Re-run inside Docker with the",
        "  model downloaded (`docker compose exec backend python -m scripts.evaluate`) to evaluate LLM answers.",
        "- With the sample dataset, place results are fictional; the local metrics only check that a search ran",
        "  around the property's coordinates and returned cards.",
        "- The evaluation set was also used while developing the heuristic planner and extractive answerer, so treat",
        "  these numbers as a **regression suite**, not an unbiased estimate of real-world accuracy. Add new cases",
        "  from real guest conversations (anonymised) to measure generalisation.",
        "- Per-case details are in `data/eval/results.json`.",
    ]
    out = ROOT / "docs" / "evaluation_report.md"
    out.write_text("\n".join(lines) + "\n")
    (ROOT / "data" / "eval" / "results.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline-maps", action="store_true")
    args = ap.parse_args()
    data = asyncio.run(run(args.offline_maps))
    path = write_report(data)
    print(json.dumps(data["summary"], indent=1))
    print(f"Report written to {path}")
