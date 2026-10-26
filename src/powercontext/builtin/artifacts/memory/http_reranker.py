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

"""HTTP cross-encoder reranking with batched requests and global ordering."""

from __future__ import annotations

import math

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

DEFAULT_HTTP_RERANKER_POLICY_ID = "http-cross-encoder-v1"


class HTTPMemoryReranker:
    """Score Memory candidates with one HTTP service and select globally."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = DEFAULT_HTTP_RERANKER_POLICY_ID,
    ) -> None:
        if not endpoint or not endpoint.strip():
            raise ValueError("reranker endpoint must be a non-empty URL")  # noqa: TRY003
        if batch_size < 1:
            raise ValueError("reranker batch_size must be positive")  # noqa: TRY003
        self.endpoint = endpoint
        self.policy_id = policy_id
        self._client = client
        self._batch_size = batch_size

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Send every candidate once, then order the whole pool by the service scores."""

        if not candidates:
            raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
        if limit < 1 or limit > len(candidates):
            raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003

        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            ranks = tuple(range(start + 1, start + 1 + len(batch)))
            payload = {
                "query": query,
                "candidates": [
                    {"rank": rank, "text": candidate.text}
                    for rank, candidate in zip(ranks, batch, strict=True)
                ],
            }
            response = await self._client.post(self.endpoint, json=payload)
            response.raise_for_status()
            requests += 1
            scores.update(_parse_scores(response, ranks))

        ordered = sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit]
        return MemoryRerankDecision(
            selected_ranks=tuple(ordered),
            usage=InferenceUsage(requests=requests),
        )


def _parse_scores(response: httpx.Response, ranks: tuple[int, ...]) -> dict[int, float]:
    try:
        payload = response.json()
    except ValueError as error:
        raise ValueError("reranker response must be JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise ValueError("reranker response must contain exactly a scores list")  # noqa: TRY003

    entries = payload["scores"]
    if not isinstance(entries, list):
        raise ValueError("reranker scores must be a list")  # noqa: TRY003

    expected = set(ranks)
    scores: dict[int, float] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"rank", "score"}:
            raise ValueError("each reranker score must contain exactly rank and score")  # noqa: TRY003
        rank = entry["rank"]
        score = entry["score"]
        if type(rank) is not int or rank not in expected:
            raise ValueError("reranker score rank must be one of the requested ranks")  # noqa: TRY003
        if rank in scores:
            raise ValueError("reranker score ranks must be unique")  # noqa: TRY003
        if isinstance(score, bool) or not isinstance(score, int | float):
            raise ValueError("reranker score must be numeric")  # noqa: TRY003
        if not math.isfinite(score):
            raise ValueError("reranker score must be finite")  # noqa: TRY003
        scores[rank] = float(score)

    if set(scores) != expected:
        raise ValueError("reranker response must cover every requested rank exactly once")  # noqa: TRY003
    return scores


__all__ = ["DEFAULT_HTTP_RERANKER_POLICY_ID", "HTTPMemoryReranker"]
