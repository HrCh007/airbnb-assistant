"""Anthropic Claude integration: query planning and streamed answer generation."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path

import anthropic

from app.core.config import get_settings
from app.llm.schemas import QueryPlan
from app.location.categories import CATEGORIES

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMUnavailable(Exception):
    """Claude could not be reached or refused; caller should degrade gracefully."""


@lru_cache
def system_prompt() -> str:
    path = get_settings().system_prompt_path or str(PROMPTS_DIR / "system_prompt.md")
    return Path(path).read_text(encoding="utf-8").strip()


@lru_cache
def planner_prompt() -> str:
    raw = (PROMPTS_DIR / "planner_prompt.md").read_text(encoding="utf-8").strip()
    return raw.replace("{catalogue}", ", ".join(c.key for c in CATEGORIES))


_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        s = get_settings()
        _client = anthropic.AsyncAnthropic(
            api_key=s.anthropic_api_key,
            timeout=s.claude_timeout_seconds,
            max_retries=s.claude_max_retries,
        )
    return _client


def _describe_error(exc: Exception) -> str:
    if isinstance(exc, anthropic.RateLimitError):
        return "rate_limited"
    if isinstance(exc, anthropic.AuthenticationError):
        return "authentication_failed"
    if isinstance(exc, anthropic.APIStatusError):
        return f"api_status_{exc.status_code}"
    if isinstance(exc, anthropic.APITimeoutError):
        return "timeout"
    if isinstance(exc, anthropic.APIConnectionError):
        return "connection_error"
    return type(exc).__name__


async def plan_with_llm(history: list[dict], message: str, context_summary: str) -> QueryPlan:
    """Classify intent and build a retrieval/search plan with structured outputs."""
    s = get_settings()
    convo = "\n".join(f"{m['role'].upper()}: {m['content'][:600]}" for m in history[-6:])
    user = (
        f"<context>\n{context_summary}\n</context>\n"
        f"<recent_conversation>\n{convo or '(none)'}\n</recent_conversation>\n"
        f"<latest_guest_message>\n{message}\n</latest_guest_message>\n"
        "Produce the plan for the latest guest message."
    )
    try:
        resp = await get_client().messages.parse(
            model=s.claude_planner_model,
            max_tokens=2000,
            system=planner_prompt(),
            messages=[{"role": "user", "content": user}],
            output_format=QueryPlan,
            output_config={"effort": s.claude_planner_effort},
        )
    except (anthropic.APIError, anthropic.APIConnectionError) as exc:
        log.warning("Claude planner failed: %s", _describe_error(exc))
        raise LLMUnavailable(_describe_error(exc)) from exc
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise LLMUnavailable(f"planner_stop_{resp.stop_reason}")
    plan = resp.parsed_output
    plan.planner = "llm"
    return plan


async def stream_answer(history: list[dict], user_turn: str) -> AsyncIterator[str]:
    """Stream the grounded answer text. Raises LLMUnavailable before the first
    token if Claude can't be reached; mid-stream failures end with a notice."""
    s = get_settings()
    messages = [*history, {"role": "user", "content": user_turn}]
    system = [{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}]
    common = dict(
        model=s.claude_model,
        max_tokens=s.claude_max_tokens,
        system=system,
        messages=messages,
        output_config={"effort": s.claude_answer_effort},
    )
    started = False
    try:
        if s.claude_enable_refusal_fallback:
            manager = get_client().beta.messages.stream(
                **common, betas=[_FALLBACK_BETA], fallbacks="default"
            )
        else:
            manager = get_client().messages.stream(**common)
        async with manager as stream:
            async for text in stream.text_stream:
                started = True
                yield text
            final = await stream.get_final_message()
        if final.stop_reason == "refusal":
            log.warning("Claude refused an answer")
            if not started:
                raise LLMUnavailable("refusal")
            yield "\n\nSorry, I can't help with that one. Is there anything else about your stay I can help with?"
        elif final.stop_reason == "max_tokens":
            yield "…"
    except (anthropic.APIError, anthropic.APIConnectionError) as exc:
        log.warning("Claude answer failed: %s", _describe_error(exc))
        if not started:
            raise LLMUnavailable(_describe_error(exc)) from exc
        yield "\n\n_(Sorry, my connection dropped while answering. Please ask again.)_"


def to_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)
