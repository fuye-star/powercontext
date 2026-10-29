"""Assemble the development context for one task."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .materials import query_terms
from .state import TaskStateStore
from .store import MaterialStore


@dataclass(frozen=True)
class ContextRequest:
    task_id: str
    task: str
    root: Path


def _line_number(title: str) -> int | None:
    parts = title.split(":", 1)
    if len(parts) != 2:
        return None
    token = parts[1].strip().split(" ", 1)[0]
    return int(token) if token.isdigit() else None


def _entry(row: dict[str, object], rank: int) -> dict[str, object]:
    title = str(row["title"])
    return {
        "id": str(row["symbol"]),
        "path": str(row["path"]),
        "symbol": str(row["symbol"]),
        "line": _line_number(title),
        "source": f"{row['path']}#{row['symbol']}",
        "rank": rank,
        "content": str(row["content"]),
    }


async def build_context(request: ContextRequest, store: MaterialStore) -> dict[str, object]:
    rows = store.search(request.task, limit=60)
    terms = query_terms(request.task)
    lowered = request.task.casefold()

    def keyword_rank(row: dict[str, object]) -> int:
        symbol = str(row["symbol"]).casefold()
        return 0 if symbol and symbol in lowered else 1

    rows.sort(key=lambda row: (keyword_rank(row),))
    specs = [row for row in rows if row["kind"] == "spec"][:6]
    code = [row for row in rows if row["kind"] == "code"][:6]
    tests = [row for row in rows if row["kind"] == "test"][:8]

    state = await TaskStateStore(request.root / ".data" / "powercontext.db").read(request.task_id)
    verifications = await TaskStateStore(request.root / ".data" / "powercontext.db").read_verifications(
        request.task_id
    )

    context = {
        "schema": "sdd.context.v1",
        "task": {"id": request.task_id, "text": request.task, "terms": terms},
        "specs": [_entry(row, index) for index, row in enumerate(specs)],
        "code": [_entry(row, index) for index, row in enumerate(code)],
        "tests": [_entry(row, index) for index, row in enumerate(tests)],
        "state": state,
        "verification": verifications[-1] if verifications else None,
        "sources": [str(row["path"]) for row in (*specs, *code, *tests)],
    }
    return context


def render_markdown(context: dict[str, object], *, full: bool = False) -> str:
    lines: list[str] = []
    task = context["task"]
    lines.append(f"# 任务 {task['id']}")
    lines.append("")
    lines.append(f"任务描述：{task['text']}")
    lines.append("")

    lines.append("## 适用规格")
    for item in context["specs"]:
        lines.append(f"- [{item['id']}] {item['source']}")
        if full:
            lines.append("")
            lines.append(str(item["content"]))
    lines.append("")

    lines.append("## 相关代码")
    for item in context["code"]:
        location = f"{item['path']}:{item['line']}" if item["line"] else item["path"]
        lines.append(f"- {item['symbol']} ({location})")
        if full:
            lines.append("")
            lines.append(f"```python\n{item['content']}\n```")
    lines.append("")

    lines.append("## 验证测试")
    for item in context["tests"]:
        location = f"{item['path']}:{item['line']}" if item["line"] else item["path"]
        lines.append(f"- {item['symbol']} ({location})")
    lines.append("")

    state = context.get("state") or {}
    lines.append("## 任务状态（PowerContext）")
    lines.append(f"- 已完成：{state.get('done') or '无'}")
    lines.append(f"- 待办：{state.get('todo') or '无'}")
    lines.append(f"- 已验证：{state.get('verified') or '无'}")
    return "\n".join(lines)


def dumps(context: dict[str, object]) -> str:
    return json.dumps(context, ensure_ascii=False, indent=2)


__all__ = ["ContextRequest", "build_context", "render_markdown", "dumps"]
