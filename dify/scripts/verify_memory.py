"""Reproduce the Memory Inn round trip against a running PowerContext Server.

Usage:
    python scripts/verify_memory.py --base-url http://127.0.0.1:8123

It creates a throw-away Scope, writes two memories (exactly what the Dify
workflow writes in the "写入记忆" node), searches them back (the "检索记忆"
node), and prints the raw API evidence. Exit code 0 means all four calls
behaved as the Dify app expects.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

FIRST = "玩家第一次来店里，说自己是程序员，喜欢喝不加糖的美式。"
SECOND = "玩家偷偷把店里的招财猫转了个方向，老板娘假装没看见。"


def call(base_url: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict | str]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify PowerContext memory round trip.")
    parser.add_argument("--base-url", default=os.environ.get("PC_BASE_URL", "http://127.0.0.1:8123"))
    args = parser.parse_args()
    key = f"memory-inn-verify-{int(time.time())}"

    status, scope = call(
        args.base_url,
        "POST",
        "/v1/scopes",
        {"title": "记忆旅馆验证", "summary": "验证跨会话记忆", "idempotency_key": key},
    )
    if status >= 300:
        print(f"FAIL scope: {status} {scope}")
        return 1
    scope_id = scope["scope_id"]
    print(f"scope={scope_id}")

    for text in (FIRST, SECOND):
        status, result = call(
            args.base_url,
            "POST",
            "/v1/memory/remember",
            {"scope_id": scope_id, "kind": "interaction", "text": text},
        )
        if status >= 300:
            print(f"FAIL remember: {status} {result}")
            return 1
        entry = result.get("entry") or {}
        citation = entry.get("citation") or {}
        print(f"remember ok memory={result.get('memory')} version={citation.get('entry_version_id')}")

    checks = {"程序员 美式": FIRST, "招财猫 方向": SECOND}
    for query, expected in checks.items():
        status, result = call(
            args.base_url,
            "POST",
            "/v1/memory/search",
            {"scope_id": scope_id, "query": query, "limit": 5, "mode": "fts"},
        )
        if status >= 300:
            print(f"FAIL search: {status} {result}")
            return 1
        texts = [hit["text"] for hit in result["hits"]]
        print(f"search {query!r} -> {texts}")
        if expected not in texts:
            print("FAIL: expected memory was not recalled")
            return 1

    print("PASS: scope + remember + search round trip matches the Dify workflow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
