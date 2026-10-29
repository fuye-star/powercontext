"""MiniMax-backed development assistant for one SDD task."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from .context import ContextRequest, build_context, render_markdown
from .minimax_client import MiniMaxConfig, chat, extract_code
from .store import MaterialStore
from .state import TaskStateStore

SYSTEM_PROMPT = (
    "你是一个严谨的 Python 工程师。根据给出的规格、当前代码和测试，完成代码修改。"
    "只输出修改后的完整文件内容，放在一个 ```python 代码块里；不要输出解释、diff 或多个文件。"
    "除非规格要求，否则不要改变现有函数签名和已有行为。"
)


def build_messages(context: dict[str, object], current_source: str, file_path: str) -> list[dict[str, str]]:
    specs = "\n\n".join(f"[{item['id']}] {item['content']}" for item in context["specs"])
    tests = "\n".join(f"- {item['symbol']} ({item['path']}:{item['line']})" for item in context["tests"])
    user = (
        f"任务：{context['task']['text']}\n\n"
        f"必须先满足的规格：\n{specs}\n\n"
        f"必须保持通过的回归行为：SPEC-001（类别筛选忽略大小写）、SPEC-005（不修改输入）。\n\n"
        f"需要修改的文件：{file_path}\n\n"
        f"当前内容：\n```python\n{current_source}\n```\n\n"
        f"用于验证的测试：\n{tests}\n\n"
        "请输出修改后的完整文件。"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def run_pytest(root: Path) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {
        "command": f"{sys.executable} -m pytest -q tests",
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-2000:],
    }


async def develop(
    *,
    root: Path,
    record_path: Path,
    task_id: str,
    task: str,
    file_path: str,
    config: MiniMaxConfig,
    apply: bool = True,
    run_tests: bool = True,
) -> dict[str, object]:
    store = MaterialStore()
    try:
        context = await build_context(ContextRequest(task_id=task_id, task=task, root=root), store)
    finally:
        store.connection.close()
    target = root / file_path
    current_source = target.read_text(encoding="utf-8")
    messages = build_messages(context, current_source, file_path)

    response = chat(messages, config)
    code = extract_code(response)
    choice = (response.get("choices") or [{}])[0]
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "task_id": task_id,
        "task": task,
        "file": file_path,
        "model": config.model,
        "base_url": config.base_url,
        "context_digest": {
            "specs": [item["id"] for item in context["specs"]],
            "code": [item["symbol"] for item in context["code"]],
            "tests": [item["symbol"] for item in context["tests"]],
        },
        "messages": messages,
        "response_text": code,
        "finish_reason": choice.get("finish_reason"),
        "usage": response.get("usage"),
    }

    if apply:
        backup = record_path.parent / "before" / f"{Path(file_path).name}.before"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        target.write_text(code + ("\n" if not code.endswith("\n") else ""), encoding="utf-8")
        record["applied_path"] = str(backup)

    if run_tests:
        record["tests"] = run_pytest(root)
        await TaskStateStore(root / ".data" / "powercontext.db").record_verification(
            task_id,
            {
                "command": record["tests"]["command"],
                "exit_code": record["tests"]["exit_code"],
                "file": file_path,
                "model": config.model,
            },
        )

    record_path.parent.mkdir(parents=True, exist_ok=True)
    with record_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def run(**kwargs) -> dict[str, object]:
    return asyncio.run(develop(**kwargs))


__all__ = ["develop", "run", "build_messages", "run_pytest"]


if __name__ == "__main__":  # pragma: no cover - thin manual entry
    raise SystemExit(
        "Use `python -m sdd_ctx minimax --task ...` instead of running this module directly."
    )
