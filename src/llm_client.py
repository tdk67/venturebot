"""LLM client  -- OpenRouter chat-completions wrapper.

Budget enforcement (S7) is integrated: every call does `budget.check_budget()`
before and `budget.record_usage()` after. Structured output is supported via
JSON-schema instruction (OpenRouter providers vary in response_format support).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from . import config, budget


class LLMError(Exception):
    """LLM call failed after retries."""


def _json_schema_instruction(schema: dict) -> str:
    return (
        "\n\nIMPORTANT: Respond with a single valid JSON object. "
        "No markdown, no backticks, no extra text before or after the JSON. "
        "The JSON must match this schema:\n" + json.dumps(schema, indent=2)
    )


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:8080",
        "X-Title": "VentureBot",
    }


def _estimate_tokens(messages: list[dict], max_tokens: int) -> tuple[int, int]:
    """Rough token estimate for pre-call budget check (conservative)."""
    in_tokens = sum(len(str(m.get("content", ""))) // 4 for m in messages)
    return in_tokens, max_tokens


def call_llm_raw(
    model: str,
    messages: list[dict],
    temperature: float = 0.3,
    max_tokens: int | None = None,
    timeout: int | None = None,
    retries: int = 2,
) -> str:
    max_tokens = max_tokens or config.MAX_TOKENS
    timeout = timeout or config.LLM_TIMEOUT
    api_key = config.openrouter_api_key()

    budget.reset_daily_if_needed()
    in_tokens, out_tokens = _estimate_tokens(messages, max_tokens)
    budget.check_budget(in_tokens, out_tokens, model)

    payload = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens:
        payload["max_tokens"] = max_tokens

    last_err = None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    f"{config.OPENROUTER_BASE}/chat/completions",
                    headers=_headers(api_key),
                    json=payload,
                )
                resp.raise_for_status()
                result = resp.json()

            usage = result.get("usage", {})
            budget.record_usage(
                model,
                usage.get("prompt_tokens", in_tokens),
                usage.get("completion_tokens", out_tokens),
            )
            content = result["choices"][0]["message"].get("content") or ""
            content = content.strip()
            if content:
                return content
            last_err = LLMError(f"empty response from {model}")
            if attempt < retries:
                continue
        except (httpx.HTTPError, KeyError, IndexError) as e:
            last_err = e
            if attempt < retries:
                continue
    raise LLMError(f"LLM call to {model} failed after {retries + 1} attempts: {last_err}")


def call_llm(
    model: str,
    messages: list[dict],
    schema: dict,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    timeout: int | None = None,
    retries: int = 2,
) -> dict[str, Any]:
    """Structured JSON call. Raises LLMError on parse failure."""
    msgs = [dict(m) for m in messages]
    for m in reversed(msgs):
        if m["role"] == "user":
            m["content"] = str(m["content"]) + _json_schema_instruction(schema)
            break
    text = call_llm_raw(
        model, msgs,
        temperature=temperature, max_tokens=max_tokens, timeout=timeout, retries=retries,
    )
    parsed = _parse_json_response(text, model)
    if "_raw" in parsed:
        raise LLMError(f"{model} returned unparseable JSON: {parsed['_raw'][:300]}")
    return parsed


def _parse_json_response(content: str, model: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text, "_error": "JSON parse failed", "_model": model}
