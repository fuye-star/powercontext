"""Minimal MiniMax chat client (OpenAI-compatible HTTP API)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MODEL = "MiniMax-M3.1-Flash"


@dataclass(frozen=True)
class MiniMaxConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    max_completion_tokens: int = 8000

    @classmethod
    def from_env(cls) -> "MiniMaxConfig":
        key = os.environ.get("MINIMAX_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "MINIMAX_API_KEY is not set. Export a MiniMax API key before running the agent."
            )
        return cls(
            api_key=key,
            base_url=os.environ.get("MINIMAX_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            model=os.environ.get("MINIMAX_MODEL", DEFAULT_MODEL),
            max_completion_tokens=int(os.environ.get("MINIMAX_MAX_TOKENS", "8000")),
        )


def chat(messages: list[dict[str, str]], config: MiniMaxConfig, *, timeout: float = 180.0) -> dict:
    body = {
        "model": config.model,
        "messages": messages,
        "max_completion_tokens": config.max_completion_tokens,
    }
    request = urllib.request.Request(
        f"{config.base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"MiniMax HTTP {error.code}: {detail}") from error


def extract_code(response: dict) -> str:
    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError("MiniMax response has no choices")
    content = (choices[0].get("message") or {}).get("content") or ""
    fence = "```"
    if fence in content:
        start = content.index(fence)
        rest = content[start + len(fence) :]
        if "\n" in rest:
            rest = rest.split("\n", 1)[1]
        end = rest.find(fence)
        if end >= 0:
            return rest[:end].strip("\n")
    return content.strip()


__all__ = ["MiniMaxConfig", "chat", "extract_code", "DEFAULT_BASE_URL", "DEFAULT_MODEL"]
