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

"""Propagate Source availability to dependent Memory and Topic Memory."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from pydantic import BaseModel, ConfigDict, Field, field_validator

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE
from powercontext.sources import SourceRef

_LIFECYCLE_FAMILIES = ("source", "memory", "topic")
_PURPOSES = ("generation", "audit")


class SourceLifecycleError(ValueError):
    """Report an invalid lifecycle registration, event, or node reference."""


class SourceLifecycleEvent(BaseModel):
    """One caller-provided Source availability event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1)
    revision: int = Field(strict=True, gt=0)
    sequence: int = Field(strict=True, gt=0)
    available: bool = Field(strict=True)
    reason: str = Field(min_length=1)

    @field_validator("node_id", "reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


@dataclass
class _Node:
    node_id: str
    family: str
    reference: dict[str, object]
    parents: tuple[str, ...]
    policy: str
    available: bool
    revision: int | None
    sequence: int | None
    reason: str | None
    ordinal: int


class ScopedSourceLifecycle:
    """Register a dependency DAG and propagate Source availability in one Scope."""

    def __init__(self, runtime: object, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = scope_id

    # -- registration ----------------------------------------------------

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        await self._register(
            node_id,
            family="source",
            reference=source_ref.model_dump(mode="json"),
            parents=(),
            policy="all",
            revision=revision,
        )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        *,
        parents: tuple[str, ...],
        policy: Literal["all", "any"],
    ) -> None:
        await self._register(
            node_id,
            family="memory",
            reference=citation.model_dump(mode="json"),
            parents=tuple(parents),
            policy=policy,
            revision=None,
        )

    async def register_topic(
        self,
        node_id: str,
        reference: ArtifactRef,
        *,
        parents: tuple[str, ...],
        policy: Literal["all", "any"],
    ) -> None:
        await self._register(
            node_id,
            family="topic",
            reference=reference.model_dump(mode="json"),
            parents=tuple(parents),
            policy=policy,
            revision=None,
        )

    async def _register(
        self,
        node_id: str,
        *,
        family: str,
        reference: dict[str, object],
        parents: tuple[str, ...],
        policy: str,
        revision: int | None,
    ) -> None:
        self._require_node_id(node_id)
        if family not in _LIFECYCLE_FAMILIES:
            raise SourceLifecycleError("unknown family")
        if policy not in {"all", "any"}:
            raise SourceLifecycleError("unknown policy")
        if family == "source":
            if revision is None or type(revision) is not int or revision < 1:
                raise SourceLifecycleError("source revision must be a positive integer")
            if parents:
                raise SourceLifecycleError("a Source cannot have parents")
        else:
            if revision is not None:
                raise SourceLifecycleError("only Sources carry a lifecycle revision")
            if not parents:
                raise SourceLifecycleError("derived nodes require at least one parent")
            if len(set(parents)) != len(parents):
                raise SourceLifecycleError("duplicate parent")
            if node_id in parents:
                raise SourceLifecycleError("self dependency")
        async with self._runtime._context(self.scope_id) as context:
            async with self._runtime._locked(self.scope_id):
                async with self._database().transaction() as connection:
                    rows = await self._load(connection)
                    existing = rows.get(node_id)
                    if existing is not None:
                        if (
                            existing.family == family
                            and existing.reference == reference
                            and existing.parents == parents
                            and existing.policy == policy
                            and existing.revision == revision
                        ):
                            return
                        raise SourceLifecycleError("node is already registered with another binding")
                    for parent in parents:
                        if parent not in rows:
                            raise SourceLifecycleError(f"unknown parent: {parent}")
                    node = _Node(
                        node_id=node_id,
                        family=family,
                        reference=reference,
                        parents=parents,
                        policy=policy,
                        available=True,
                        revision=revision,
                        sequence=None,
                        reason=None,
                        ordinal=max((item.ordinal for item in rows.values()), default=0) + 1,
                    )
                    rows[node_id] = node
                    self._propagate(rows)
                    await connection.execute(
                        SOURCE_LIFECYCLE_NODES_TABLE.insert().values(
                            scope_id=self.scope_id,
                            node_id=node.node_id,
                            family=node.family,
                            reference=json.dumps(node.reference, sort_keys=True),
                            parents=json.dumps(list(node.parents)),
                            policy=node.policy,
                            available=node.available,
                            revision=node.revision,
                            sequence=node.sequence,
                            reason=node.reason,
                            ordinal=node.ordinal,
                        )
                    )
                await self._sync_memory({node_id: node}, context)
        await self._validate_reference(family, reference)

    # -- events ----------------------------------------------------------

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event must be a SourceLifecycleEvent")
        async with self._runtime._context(self.scope_id) as context:
            async with self._runtime._locked(self.scope_id):
                async with self._database().transaction() as connection:
                    rows = await self._load(connection)
                    source = rows.get(event.node_id)
                    if source is None or source.family != "source":
                        raise SourceLifecycleError("event target is not a registered Source")
                    if event.revision != source.revision:
                        raise SourceLifecycleError("lifecycle revision mismatch")
                    if source.sequence is not None and event.sequence < source.sequence:
                        raise SourceLifecycleError("stale lifecycle sequence")
                    if source.sequence == event.sequence and (event.available, event.reason) != (
                        source.available,
                        source.reason,
                    ):
                        raise SourceLifecycleError("conflicting lifecycle event")
                    if source.sequence is None or event.sequence > source.sequence:
                        source.sequence = event.sequence
                        source.available = event.available
                        source.reason = event.reason
                    self._propagate(rows)
                    await self._persist(connection, rows)
                await self._sync_memory(rows, context)
        return sorted(item.node_id for item in rows.values() if item.available)

    async def resolve(self, node_id: str, *, purpose: str) -> object:
        if purpose not in _PURPOSES:
            raise SourceLifecycleError("unknown purpose")
        self._require_node_id(node_id)
        async with self._database().transaction() as connection:
            rows = await self._load(connection)
        node = rows.get(node_id)
        if node is None:
            raise SourceLifecycleError("unknown node")
        if purpose == "generation" and not node.available:
            raise SourceLifecycleError("node is unavailable for generation")
        return _decode_reference(node.family, node.reference)

    async def unavailable_topics(self) -> frozenset[str]:
        async with self._database().transaction() as connection:
            rows = await self._load(connection)
        return frozenset(
            str(node.reference.get("artifact_id")) for node in rows.values() if node.family == "topic" and not node.available
        )

    # -- internals -------------------------------------------------------

    def _database(self) -> object:
        return self._runtime._lifecycle_database

    def _require_node_id(self, node_id: str) -> None:
        if not isinstance(node_id, str) or not node_id.strip() or len(node_id) > 256:
            raise SourceLifecycleError("node_id must be a nonempty string")

    async def _load(self, connection: object) -> dict[str, _Node]:
        rows = (
            await connection.execute(
                select(SOURCE_LIFECYCLE_NODES_TABLE).where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id)
            )
        ).mappings()
        nodes = {}
        for row in rows:
            node = _Node(
                node_id=row["node_id"],
                family=row["family"],
                reference=json.loads(row["reference"]),
                parents=tuple(json.loads(row["parents"])),
                policy=row["policy"],
                available=bool(row["available"]),
                revision=row["revision"],
                sequence=row["sequence"],
                reason=row["reason"],
                ordinal=row["ordinal"],
            )
            nodes[node.node_id] = node
        return nodes

    async def _persist(self, connection: object, nodes: dict[str, _Node]) -> None:
        for node in nodes.values():
            await connection.execute(
                SOURCE_LIFECYCLE_NODES_TABLE.update()
                .where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node.node_id,
                )
                .values(
                    available=node.available,
                    revision=node.revision,
                    sequence=node.sequence,
                    reason=node.reason,
                )
            )

    @staticmethod
    def _propagate(nodes: dict[str, _Node]) -> None:
        ordered = sorted(nodes.values(), key=lambda item: item.ordinal)
        for node in ordered:
            if node.family == "source":
                continue
            support = [parent in nodes and nodes[parent].available for parent in node.parents]
            node.available = all(support) if node.policy == "all" else any(support)

    async def _sync_memory(self, nodes: dict[str, _Node], context: object) -> None:
        memory_nodes = [node for node in nodes.values() if node.family == "memory"]
        if not memory_nodes:
            return
        service = context.artifacts.memory
        artifact_id = context.artifacts.memory_artifact_id
        for node in memory_nodes:
            citation = MemoryCitation.model_validate(node.reference)
            try:
                memory, versions = await service.head_entries(artifact_id)
            except Exception:
                raise SourceLifecycleError("registered Memory is not readable") from None
            if memory.artifact_id != citation.memory_ref.artifact_id:
                raise SourceLifecycleError("registered Memory is not readable")
            version = next((item for item in versions if item.entry_version_id == citation.entry_version_id), None)
            item = next(
                (entry for entry in memory.content.manifest.entries if entry.entry_id == citation.entry_id),
                None,
            )
            if version is None or item is None:
                raise SourceLifecycleError("registered Memory entry is not readable")
            active = item.state == "active"
            if active == node.available:
                continue
            if node.available:
                await service.reactivate(memory, entries=(version,), reason="source lifecycle restore")
            else:
                await service.forget(memory, entries=(version,), reason="source lifecycle withdrawal")

    async def _validate_reference(self, family: str, reference: dict[str, object]) -> None:
        if family == "memory":
            citation = MemoryCitation.model_validate(reference)
            async with self._runtime._context(self.scope_id) as context:
                service = context.artifacts.memory
                try:
                    memory, versions = await service.head_entries(context.artifacts.memory_artifact_id)
                except Exception:
                    raise SourceLifecycleError("registered Memory is not readable") from None
                if memory.artifact_id != citation.memory_ref.artifact_id or not any(
                    item.entry_version_id == citation.entry_version_id for item in versions
                ):
                    raise SourceLifecycleError("registered Memory is not readable")
        elif family == "topic":
            artifact = ArtifactRef.model_validate(reference)
            try:
                await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                    artifact.family, artifact.artifact_id, artifact.revision
                )
            except Exception:
                raise SourceLifecycleError("registered Topic Memory is not readable") from None


def _decode_reference(family: str, reference: dict[str, object]) -> object:
    if family == "source":
        return SourceRef.model_validate(reference)
    if family == "memory":
        return MemoryCitation.model_validate(reference)
    return ArtifactRef.model_validate(reference)


__all__ = ["ScopedSourceLifecycle", "SourceLifecycleError", "SourceLifecycleEvent"]
