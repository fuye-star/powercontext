"""静态检查 Dify DSL：结构、连线、环境变量和 HTTP 请求体。

不依赖 Dify 实例，用于提交前自检：

    python scripts/validate_dsl.py

退出码 0 表示结构检查通过；任何一项失败都会打印原因并返回 1。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DSL = ROOT / "memory-inn.dsl.yml"


def fail(message: str) -> None:
    print(f"FAIL {message}")
    raise SystemExit(1)


def main() -> int:
    data = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    for key in ("app", "kind", "version", "workflow"):
        if key not in data:
            fail(f"missing top-level key: {key}")
    if data["kind"] != "app" or data["version"] != "0.1.5":
        fail(f"unexpected kind/version: {data['kind']} {data['version']}")
    if data["app"].get("mode") != "advanced-chat":
        fail("app.mode should be advanced-chat")

    graph = data["workflow"]["graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    edges = graph["edges"]
    for node_id, node in nodes.items():
        node_type = node.get("data", {}).get("type")
        if not node_type:
            fail(f"node {node_id} has no data.type")
    for edge in edges:
        source, target = edge.get("source"), edge.get("target")
        if source not in nodes or target not in nodes:
            fail(f"edge references unknown node: {source} -> {target}")

    start = nodes.get("start")
    if not start or start.get("data", {}).get("type") != "start":
        fail("missing start node")
    if not any(node["data"]["type"] == "answer" for node in nodes.values()):
        fail("missing answer node")

    # 每个 code 节点都应有 main 函数，且输出变量名与下游引用一致
    code_outputs: dict[str, set[str]] = {}
    for node_id, node in nodes.items():
        if node["data"]["type"] != "code":
            continue
        code = node["data"].get("code", "")
        if "def main(" not in code:
            fail(f"code node {node_id} has no main()")
        variables = {item.get("variable") for item in node["data"].get("outputs", {}).values()} if isinstance(
            node["data"].get("outputs"), dict
        ) else set()
        code_outputs[node_id] = {name for name in variables if name}

    # HTTP 节点：POST、指向 PowerContext、body 引用了 code 节点的输出
    for node_id, node in nodes.items():
        dd = node["data"]
        if dd["type"] != "http-request":
            continue
        url = dd.get("url", "")
        if "/v1/memory/" not in url or "{{#env.PC_BASE_URL#}}" not in url:
            fail(f"http node {node_id} url must use env.PC_BASE_URL and /v1/memory/*")
        if dd.get("method") != "post":
            fail(f"http node {node_id} should POST")
        body = json.dumps(dd.get("body"), ensure_ascii=False)
        if "{{#" not in body:
            fail(f"http node {node_id} body does not reference a code node output")

    # 校验 code 节点构造的 JSON 主体包含 PowerContext OpenAPI 的必填字段
    code_text = "\n".join(node["data"].get("code", "") for node in nodes.values() if node["data"]["type"] == "code")
    for field in ("scope_id", "query", "kind", "text"):
        if field not in code_text:
            fail(f"code nodes never build required field {field!r}")

    # 环境变量占位
    env_vars = {item.get("name") for item in data["workflow"].get("environment_variables", [])}
    if "PC_BASE_URL" not in env_vars or "PC_SCOPE_ID" not in env_vars:
        fail(f"environment_variables missing PC_BASE_URL/PC_SCOPE_ID: {env_vars}")

    print(f"OK nodes={len(nodes)} edges={len(edges)} env={sorted(env_vars)} dsl_version={data['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
