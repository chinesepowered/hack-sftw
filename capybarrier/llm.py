"""Thin wrapper over any OpenAI compatible chat completions endpoint (OpenRouter, OpenAI, ...)."""

from __future__ import annotations

import json
import re
import time
from functools import lru_cache

from openai import OpenAI

from . import config


@lru_cache(maxsize=1)
def client() -> OpenAI:
    if not config.LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY is not set. Add it to .env")
    return OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY, timeout=90, max_retries=2)


def chat(messages: list[dict], model: str, tools: list[dict] | None = None, temperature: float = 0.0, max_tokens: int = 900):
    """Return (message, usage_dict). Retries transient failures."""
    kwargs = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    delay = 2.0
    last_exc: Exception | None = None
    for _ in range(4):
        try:
            resp = client().chat.completions.create(**kwargs)
            usage = getattr(resp, "usage", None)
            usage_dict = {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            }
            return resp.choices[0].message, usage_dict
        except Exception as exc:  # network or provider hiccup
            last_exc = exc
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"LLM call failed: {last_exc}")


def extract_json(text: str) -> dict:
    """Parse the first JSON object in a model reply (tolerates code fences and prose)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("unterminated JSON object in reply")
