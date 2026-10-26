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

"""Capture the visible events of one selected Pi session branch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SOURCE_VERSION = "1"
CHECKPOINT_VERSION = 1


class PiSessionInput(BaseModel):
    """One complete Pi v3 JSONL session archive and its selected leaf."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible Pi event on the selected branch, with its original record."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """A captured Pi event that keeps the original record readable."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build the stable identity of one Pi event."""

    source_id = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"Pi event source id must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return source_id


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    """Parse a whole Pi archive and return the visible events of one branch."""

    try:
        text = value.content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session content must be valid UTF-8") from error

    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("Pi session content must be JSON lines") from error
        if not isinstance(record, dict):
            raise ValueError("each Pi session line must be a JSON object")  # noqa: TRY003
        records.append(record)

    if not records:
        raise ValueError("Pi session archive must start with a session header")  # noqa: TRY003
    header = records[0]
    if header.get("type") != "session" or header.get("version") != 3:
        raise ValueError("Pi session header must declare type session and version 3")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Pi session header must declare a non-empty id")  # noqa: TRY003

    nodes: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in records[1:]:
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("every Pi record must declare a non-empty id")  # noqa: TRY003
        if event_id in nodes:
            raise ValueError(f"duplicate Pi record id {event_id!r}")  # noqa: TRY003
        parent_id = record.get("parentId")
        if parent_id is not None:
            if not isinstance(parent_id, str) or parent_id not in nodes:
                raise ValueError(f"Pi record {event_id!r} references a missing parent")  # noqa: TRY003
        nodes[event_id] = record
        order.append(event_id)

    if not order:
        return ()
    leaf = value.leaf_id if value.leaf_id is not None else order[-1]
    if leaf not in nodes:
        raise ValueError(f"Pi session leaf {leaf!r} does not exist")  # noqa: TRY003

    chain: list[str] = []
    current: str | None = leaf
    while current is not None:
        chain.append(current)
        current = nodes[current].get("parentId")
    chain.reverse()

    events: list[PiEventInput] = []
    for event_id in chain:
        record = nodes[event_id]
        visible = _visible_event(record)
        if visible is None:
            continue
        role, body = visible
        pi_event_source_id(session_id, event_id)
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=event_id,
                parent_id=record.get("parentId"),
                event_type=record["type"],
                role=role,
                content=f"{role}: {body}",
                record=record,
            )
        )
    return tuple(events)


def pi_event_digest(event: PiEventInput) -> str:
    """Compute the content digest used to detect rewritten history."""

    canonical = json.dumps(
        event.record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _visible_event(record: dict[str, Any]) -> tuple[str, str] | None:
    event_type = record.get("type")
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        if not isinstance(role, str) or not role:
            return None
        if role == "toolResult":
            body = _tool_result_body(message)
        elif role == "bashExecution":
            body = _bash_body(message)
        else:
            body = _content_body(message.get("content"))
        return (role, body) if body else None
    if event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        if not isinstance(summary, str) or not summary:
            return None
        return (str(event_type), summary)
    if event_type == "custom_message":
        body = _content_body(record.get("content"))
        return ("custom", body) if body else None
    return None


def _content_body(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif block_type == "toolCall":
            name = block.get("name")
            call_id = block.get("id")
            arguments = block.get("arguments")
            compact = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            parts.append(f"[tool_call {name} {call_id}] {compact}")
    return "\n".join(parts)


def _tool_result_body(message: dict[str, Any]) -> str:
    name = message.get("toolName")
    call_id = message.get("toolCallId")
    text = _content_body(message.get("content"))
    return f"[tool_result {name} {call_id}] {text}"


def _bash_body(message: dict[str, Any]) -> str:
    command = message.get("command")
    output = message.get("output")
    return f"$ {command}\n{output}"


class _PiEventSourceAdapter:
    """Resolve and read Pi event Sources without changing the catalog."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        identity = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=identity,
            source_id=identity,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class _PiEventTextEvidenceProjection:
    """Project one Pi event into the canonical text evidence shape."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    _PiEventSourceAdapter(),
    version=PI_SOURCE_VERSION,
    projections=(_PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    """Incrementally submit the visible events of one Pi session branch."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SOURCE_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_DEFINITION.name})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        events = parse_pi_session(PiSessionInput(content=self._path.read_bytes(), leaf_id=self._leaf_id))
        imported = _load_checkpoint(session.checkpoint)

        pending: list[tuple[PiEventInput, str]] = []
        for event in events:
            digest = pi_event_digest(event)
            known = imported.get(event.event_id)
            if known is None:
                pending.append((event, digest))
            elif known != digest:
                raise ValueError(f"Pi event {event.event_id!r} was rewritten after it was imported")  # noqa: TRY003

        checkpoint = dict(imported)
        for event, digest in pending:
            await session.submit(event.event_id, PI_EVENT_SOURCE_DEFINITION.name, event)
            checkpoint[event.event_id] = digest

        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"version": CHECKPOINT_VERSION, "events": checkpoint},
        )


def _load_checkpoint(checkpoint: JsonValue | None) -> dict[str, str]:
    if checkpoint is None:
        return {}
    if not isinstance(checkpoint, dict) or checkpoint.get("version") != CHECKPOINT_VERSION:
        raise ValueError("Pi session checkpoint is not recognized")  # noqa: TRY003
    events = checkpoint.get("events")
    if not isinstance(events, dict):
        raise ValueError("Pi session checkpoint must store imported events")  # noqa: TRY003
    imported: dict[str, str] = {}
    for event_id, digest in events.items():
        if not isinstance(event_id, str) or not isinstance(digest, str):
            raise ValueError("Pi session checkpoint entries must be strings")  # noqa: TRY003
        imported[event_id] = digest
    return imported


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_SESSION_CONNECTOR_NAME",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_digest",
    "pi_event_source_id",
]
