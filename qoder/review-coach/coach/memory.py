"""PowerContext-backed memory: review history, weak topics and preferences."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RememberMemoryRequest, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft

SCOPE_KEY = "review-coach"
SCOPE_TITLE = "复习教练记忆"
SCOPE_SUMMARY = "保存每个用户的复习记录、薄弱主题与偏好，供后续检索与出题复用"
ATTEMPT_KIND = "review-attempt"
QUERY_KIND = "review-query"


class Memory:
    """Each CLI invocation opens PowerContext again, so state must survive restarts."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _config(self) -> BuiltinConfig:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        return BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{self.database_path}"))

    async def _scope(self, runtime) -> str:
        descriptor = await runtime.scopes.create(
            ScopeDraft(title=SCOPE_TITLE, summary=SCOPE_SUMMARY, idempotency_key=SCOPE_KEY)
        )
        return descriptor.scope_id

    async def _remember(self, kind: str, payload: dict[str, object]) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        async with open_builtin_runtime(self._config()) as runtime:
            scope = await self._scope(runtime)
            memory = runtime.memory.for_scope(scope)
            await memory.remember(RememberMemoryRequest(entries=(MemoryEntryInput(kind=kind, text=text),)))

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

    def record_attempt(self, user: str, *, note_id: str, topic: str, correct: bool) -> None:
        asyncio.run(
            self._remember(
                ATTEMPT_KIND,
                {
                    "user": user,
                    "note_id": note_id,
                    "topic": topic,
                    "correct": correct,
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                },
            )
        )

    def record_query(self, user: str, query: str, hits: list[str]) -> None:
        asyncio.run(
            self._remember(
                QUERY_KIND,
                {"user": user, "query": query, "hits": hits, "at": datetime.now(UTC).isoformat(timespec="seconds")},
            )
        )

    def attempts(self, user: str) -> list[dict[str, object]]:
        return [item for item in asyncio.run(self._load(ATTEMPT_KIND)) if item.get("user") == user]

    def queries(self, user: str, *, limit: int = 5) -> list[dict[str, object]]:
        items = [item for item in asyncio.run(self._load(QUERY_KIND)) if item.get("user") == user]
        return items[-limit:]

    def profile(self, user: str) -> dict[str, object]:
        attempts = self.attempts(user)
        stats: dict[str, list[bool]] = {}
        for attempt in attempts:
            topic = str(attempt.get("topic") or "未分类")
            stats.setdefault(topic, []).append(bool(attempt.get("correct")))
        rows = []
        for topic, results in stats.items():
            accuracy = sum(1 for item in results if item) / len(results)
            rows.append({"topic": topic, "attempts": len(results), "accuracy": round(accuracy, 3)})
        rows.sort(key=lambda row: (row["accuracy"], -row["attempts"]))
        weak = [row for row in rows if row["accuracy"] < 0.6]
        correct = sum(1 for attempt in attempts if attempt.get("correct"))
        return {
            "attempts": len(attempts),
            "correct": correct,
            "accuracy": round(correct / len(attempts), 3) if attempts else None,
            "topics": rows,
            "weak_topics": weak,
            "recent_queries": self.queries(user),
        }
