"""LLM clients.

OpenAIClient talks to the OpenAI API. MockLLM is used when OPENAI_API_KEY is
not set: it returns an empty answer so every agent falls back to its own
deterministic, rule based logic. Agents must work fully without an LLM.

LLM output is UNTRUSTED text. Agents only read data out of it (validated
JSON fields, or plain text shown to a human as a suggestion). They never
execute instructions, call services or change permissions because an LLM
answer asked for it.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Protocol

log = logging.getLogger(__name__)

MAX_LLM_TEXT = 8000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class LLMClient(Protocol):
    enabled: bool

    async def complete(self, system: str, user: str, json_mode: bool = False) -> str: ...


class MockLLM:
    enabled = False

    async def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        return ""


class OpenAIClient:
    enabled = True

    def __init__(self, api_key: str, model: str, timeout: float = 60.0) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=2)
        self.model = model

    async def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2,
            **kwargs,
        )
        return resp.choices[0].message.content or ""


def clean_untrusted(text: str) -> str:
    """Normalise LLM text before showing or storing it."""
    return _CONTROL.sub("", text or "").strip()[:MAX_LLM_TEXT]


def parse_json_object(text: str) -> dict | None:
    text = clean_untrusted(text)
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def build_llm(settings) -> LLMClient:
    if settings.llm_enabled:
        return OpenAIClient(settings.openai_api_key.get_secret_value(), settings.openai_model, settings.openai_timeout)
    log.info("OPENAI_API_KEY берилмаган: агентлар қоидалар асосидаги (demo) режимда ишлайди.")
    return MockLLM()
