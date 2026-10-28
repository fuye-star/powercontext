"""Create (or reuse) the PowerContext Scope used by the Memory Inn Dify app.

Usage:
    python scripts/init_scope.py --base-url http://127.0.0.1:8123
    python scripts/init_scope.py --base-url http://powercontext:8123 --key memory-inn-v1 --seed

The command is idempotent: the same --key always returns the same Scope. It prints
the two values that must be filled into the Dify app's environment variables.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_SEED = "记忆旅馆今天开张，老板娘阿婆在柜台后面擦一只旧茶杯。"


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
    except urllib.error.URLError as error:
        return 0, str(error)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the Memory Inn PowerContext Scope.")
    parser.add_argument("--base-url", default=os.environ.get("PC_BASE_URL", "http://127.0.0.1:8123"))
    parser.add_argument("--key", default="memory-inn-v1", help="idempotency key of the Scope")
    parser.add_argument("--seed", action="store_true", help="write one starter memory for a warmer first visit")
    args = parser.parse_args()

    status, scope = call(
        args.base_url,
        "POST",
        "/v1/scopes",
        {"title": "记忆旅馆", "summary": "会记住每一位客人的旅店", "idempotency_key": args.key},
    )
    if status == 409:
        print(
            "这个 --key 已被另一组标题/摘要占用。请换一个 --key（例如 --key memory-inn-v2），"
            "或在 Dify 环境变量里直接复用之前创建好的 PC_SCOPE_ID。",
            file=sys.stderr,
        )
        return 2
    if status >= 300 or not isinstance(scope, dict):
        print(f"创建 Scope 失败: HTTP {status} {scope}", file=sys.stderr)
        return 1
    scope_id = scope["scope_id"]
    print(f"Scope 已就绪: {scope_id}")

    if args.seed:
        status, result = call(
            args.base_url,
            "POST",
            "/v1/memory/remember",
            {"scope_id": scope_id, "kind": "inn", "text": DEFAULT_SEED},
        )
        if status >= 300:
            print(f"写入开张记忆失败: HTTP {status} {result}", file=sys.stderr)
            return 1
        print("已写入开张记忆。")

    print()
    print("把下面两个值填进 Dify 应用「环境变量」：")
    print(f"  PC_BASE_URL = {args.base_url}")
    print(f"  PC_SCOPE_ID = {scope_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
