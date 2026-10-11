"""Chat completions against any OpenAI-compatible API (Bayleaf by default).

Responses are cached on disk by prompt hash. Recorded run replay is separate
and reads SQLite. Explicit mock mode makes no network calls; live model
failures propagate to the clerk for its single retry and technical-failure status.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from sumer.config import CACHE_DIR, settings

log = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    def __init__(self, message: str, *, usage: dict | None = None):
        super().__init__(message)
        self.usage = usage


class LLMFailed(LLMUnavailable):
    """A real call was attempted and failed (network, timeout, server error).

    Retried before it reaches a caller. Unlike "no credentials" or "budget used
    up", a failure here means the run made a decision the cache cannot replay."""


ATTEMPTS = 3
BACKOFF_S = (2.0, 5.0)  # wait before the second and third attempts


@dataclass
class Reply:
    content: str
    model: str
    cached: bool
    latency_s: float
    cost_usd: float

    def meta(self) -> dict:
        return {"model": self.model, "cached": self.cached,
                "latency_s": round(self.latency_s, 2), "cost_usd": round(self.cost_usd, 6)}


def enabled() -> bool:
    return settings.llm_mode == "bayleaf" and bool(settings.llm_api_key and settings.llm_base_url)


def _cache_path(messages: list[dict]) -> Path:
    key = hashlib.sha256(
        json.dumps([settings.llm_model, messages], sort_keys=True).encode()
    ).hexdigest()
    return CACHE_DIR / "llm" / f"{key}.json"


SPEND_LOG = CACHE_DIR / "spend.log"


def spent_usd() -> float:
    """Total USD spent so far, summed across every process (append-only log)."""
    try:
        return sum(float(x) for x in SPEND_LOG.read_text().split())
    except FileNotFoundError:
        return 0.0


def _record_spend(usd: float) -> None:
    SPEND_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SPEND_LOG.open("a") as f:
        f.write(f"{usd:.8f}\n")


async def chat(messages: list[dict], temperature: float = 0.4) -> Reply:
    """One chat completion, from the cache if possible. A live call is tried up
    to ATTEMPTS times, so a slow or dropped request does not silently turn into
    an uncached policy fallback that the next replay would not reproduce."""
    path = _cache_path(messages)
    if settings.llm_cache and path.exists():
        return Reply(json.loads(path.read_text())["content"], settings.llm_model, True, 0.0, 0.0)
    if not enabled():
        raise LLMUnavailable("LLM_MODE is not bayleaf or credentials are missing")
    for attempt in range(ATTEMPTS):
        if spent_usd() >= settings.llm_budget_usd:
            raise LLMUnavailable(f"LLM budget of ${settings.llm_budget_usd} used up")
        try:
            reply = await _call(messages, temperature)
            break
        except LLMFailed as e:
            if attempt == ATTEMPTS - 1:
                raise
            log.warning("LLM call failed (attempt %d of %d): %s", attempt + 1, ATTEMPTS, e)
            await asyncio.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
    if settings.llm_cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"content": reply.content}))
    return reply


async def _call(messages: list[dict], temperature: float) -> Reply:
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 700,
        "response_format": {"type": "json_object"},
        "reasoning": {"enabled": False},  # thinking tokens cost money and add latency
    }
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as client:
            r = await client.post(
                url, json=body, headers={"Authorization": f"Bearer {settings.llm_api_key}"}
            )
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
        raise LLMFailed(f"{type(e).__name__}: {e}") from e
    cost = float((data.get("usage") or {}).get("cost") or 0.0)
    _record_spend(cost)
    if not content:
        raise LLMFailed("empty reply", usage=Reply(
            "", settings.llm_model, False, time.monotonic() - t0, cost).meta())
    return Reply(content, settings.llm_model, False, time.monotonic() - t0, cost)


def parse_json(text: str, *, strict: bool = False) -> dict:
    """Pull the first JSON object out of a model reply (tolerates code fences).

    If the reply was cut off mid-object, salvage the complete fields before the
    cut. The proposal comes first in our schema, so it usually survives."""
    if strict:
        # Live council decisions must be complete objects, never salvaged fragments.
        body = text.strip()
        if body.startswith("```") and body.endswith("```"):
            body = re.sub(r"^```(?:json)?\s*", "", body)[:-3].strip()
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("Model response must be a JSON object.")
        return value
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    body = text[start:]
    for end in range(len(body), 0, -1):
        if body[end - 1] in ']}"':
            try:
                return json.loads(body[:end].rstrip().rstrip(",") + "}")
            except json.JSONDecodeError:
                continue
    raise ValueError("unreadable JSON in reply")
