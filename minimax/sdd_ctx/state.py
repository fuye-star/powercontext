"""Task state persistence through PowerContext memory."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RememberMemoryRequest, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft

STATE_KIND = "sdd-task-state"
VERIFICATION_KIND = "sdd-verification"
SCOPE_KEY = "sdd-context-state"
SCOPE_TITLE = "SDD 上下文状态"
SCOPE_SUMMARY = "规格驱动开发任务的状态与验证记录"


class TaskStateStore:
    """Read/write task progress in one PowerContext Scope."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def _config(self) -> BuiltinConfig:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        return BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{self._database_path}"),
        )

    async def write(
        self,
        task_id: str,
        *,
        done: tuple[str, ...] = (),
        todo: tuple[str, ...] = (),
        verified: tuple[str, ...] = (),
        note: str = "",
    ) -> dict[str, object]:
        payload = {
            "schema": "sdd.task-state.v1",
            "task_id": task_id,
            "done": list(done),
            "todo": list(todo),
            "verified": list(verified),
            "note": note,
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        await self._remember(task_id, STATE_KIND, json.dumps(payload, ensure_ascii=False))
        return payload

    async def record_verification(self, task_id: str, result: dict[str, object]) -> None:
        payload = {"schema": "sdd.verification.v1", "task_id": task_id, **result}
        await self._remember(task_id, VERIFICATION_KIND, json.dumps(payload, ensure_ascii=False))

    async def read(self, task_id: str) -> dict[str, object] | None:
        states = [state for state in await self._load(STATE_KIND) if state.get("task_id") == task_id]
        return states[-1] if states else None

    async def read_verifications(self, task_id: str) -> list[dict[str, object]]:
        return [item for item in await self._load(VERIFICATION_KIND) if item.get("task_id") == task_id]

    async def history(self, task_id: str) -> list[dict[str, object]]:
        return [state for state in await self._load(STATE_KIND) if state.get("task_id") == task_id]

    async def _remember(self, task_id: str, kind: str, text: str) -> None:
        del task_id
        async with open_builtin_runtime(self._config()) as runtime:
            scope = await self._scope(runtime)
            memory = runtime.memory.for_scope(scope)
            await memory.remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind=kind, text=text),))
            )

    async def _scope(self, runtime) -> str:
        descriptor = await runtime.scopes.create(
            ScopeDraft(title=SCOPE_TITLE, summary=SCOPE_SUMMARY, idempotency_key=SCOPE_KEY)
        )
        return descriptor.scope_id

    async def _load(self, kind: str) -> list[dict[str, object]]:
        async with open_builtin_runtime(self._config()) as runtime:
            scope = await self._scope(runtime)
            page = await runtime.memory.for_scope(scope).list()
        items: list[dict[str, object]] = []
        for record in page.entries:
            entry = record.entry
            if entry.kind != kind:
                continue
            try:
                items.append(json.loads(entry.text))
            except json.JSONDecodeError:
                continue
        return items


def run(coroutine):
    return asyncio.run(coroutine)


__all__ = ["TaskStateStore", "STATE_KIND", "VERIFICATION_KIND", "run"]
