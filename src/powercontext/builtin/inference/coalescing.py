# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Coalesce concurrent embedding requests and cache successful vectors."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Sequence

from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage


class _Batch:
    """One underlying provider request shared by every waiter of its texts."""

    __slots__ = ("texts", "usage")

    def __init__(self, texts: tuple[str, ...]) -> None:
        self.texts = texts
        self.usage: InferenceUsage | None = None


class _Pending:
    """The shared in-flight computation one text currently waits on."""

    __slots__ = ("future", "batch")

    def __init__(self, future: asyncio.Future) -> None:
        self.future = future
        self.batch: _Batch | None = None


class CoalescingEmbeddingModel:
    """Share in-flight embedding work and cache successful vectors across calls."""

    def __init__(
        self,
        model: object,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must not be negative")  # noqa: TRY003
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003
        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = coalesce_ms / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._pending: dict[str, _Pending] = {}
        self._queue: list[str] = []
        self._queued: set[str] = set()
        self._flush_scheduled = False
        self._tasks: set[asyncio.Task] = set()

    async def embed(self, texts: Sequence[str], /) -> EmbeddingResult:
        items = tuple(texts)
        if not items:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        unique = tuple(dict.fromkeys(items))
        vectors: dict[str, tuple[float, ...]] = {}
        waiters: dict[str, _Pending] = {}
        for text in unique:
            cached = self._cache_get(text)
            if cached is not None:
                vectors[text] = cached
                continue
            pending = self._pending.get(text)
            if pending is None:
                pending = self._enqueue(text)
            waiters[text] = pending

        if waiters:
            resolved = await asyncio.gather(
                *(asyncio.shield(pending.future) for pending in waiters.values())
            )
            for (text, _pending), vector in zip(waiters.items(), resolved, strict=True):
                vectors[text] = vector

        usage = InferenceUsage(requests=0)
        counted: set[int] = set()
        for pending in waiters.values():
            batch = pending.batch
            if batch is None or id(batch) in counted or batch.usage is None:
                continue
            counted.add(id(batch))
            usage = _merge_usage(usage, batch.usage)
        return EmbeddingResult(vectors=tuple(vectors[text] for text in items), usage=usage)

    def _enqueue(self, text: str) -> _Pending:
        loop = asyncio.get_running_loop()
        pending = _Pending(loop.create_future())
        self._pending[text] = pending
        self._queue.append(text)
        self._queued.add(text)
        self._schedule_flush()
        return pending

    def _schedule_flush(self) -> None:
        if self._flush_scheduled:
            return
        self._flush_scheduled = True
        loop = asyncio.get_running_loop()
        if self._coalesce_seconds <= 0:
            # Defer to the next loop iteration so callers that arrive in the same turn
            # (for example one text per call) still share bounded batches.
            loop.call_soon(self._flush)
        else:
            loop.call_later(self._coalesce_seconds, self._flush)

    def _flush(self) -> None:
        self._flush_scheduled = False
        while self._queue:
            texts = tuple(self._queue[: self._max_batch_size])
            del self._queue[: len(texts)]
            for text in texts:
                self._queued.discard(text)
            batch = _Batch(texts)
            for text in texts:
                pending = self._pending.get(text)
                if pending is not None:
                    pending.batch = batch
            task = asyncio.get_running_loop().create_task(self._run_batch(batch))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_batch(self, batch: _Batch) -> None:
        try:
            result = await self._model.embed(batch.texts)
        except BaseException as error:  # propagate to every waiter, cache nothing
            self._finish_failure(batch, error)
            return
        vectors = tuple(result.vectors)
        if len(vectors) != len(batch.texts):
            self._finish_failure(
                batch,
                ValueError("embedding vector count must match the batch text count"),
            )
            return
        batch.usage = result.usage
        for text, vector in zip(batch.texts, vectors, strict=True):
            vector = tuple(vector)
            if self._cache_size:
                self._cache_put(text, vector)
            pending = self._pending.get(text)
            if pending is not None and pending.batch is batch:
                self._pending.pop(text, None)
                if not pending.future.done():
                    pending.future.set_result(vector)

    def _finish_failure(self, batch: _Batch, error: BaseException) -> None:
        for text in batch.texts:
            pending = self._pending.get(text)
            if pending is not None and pending.batch is batch:
                self._pending.pop(text, None)
                if not pending.future.done():
                    pending.future.set_exception(error)

    def _cache_get(self, text: str) -> tuple[float, ...] | None:
        if not self._cache_size:
            return None
        vector = self._cache.get(text)
        if vector is not None:
            self._cache.move_to_end(text)
        return vector

    def _cache_put(self, text: str, vector: tuple[float, ...]) -> None:
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


def _merge_usage(total: InferenceUsage, usage: InferenceUsage) -> InferenceUsage:
    def combine(left: int | None, right: int | None) -> int | None:
        if left is None and right is None:
            return None
        return (left or 0) + (right or 0)

    return InferenceUsage(
        requests=total.requests + usage.requests,
        input_tokens=combine(total.input_tokens, usage.input_tokens),
        output_tokens=combine(total.output_tokens, usage.output_tokens),
    )


__all__ = ["CoalescingEmbeddingModel"]
