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

"""Chunked context packs and prefix-reuse deltas for incremental transport."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any

CONTEXT_PACK_SCHEMA = "powercontext.context-pack.v1"
CONTEXT_PACK_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES = 512

_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})


class ContextPackError(Exception):
    """Raised when a pack or delta violates the wire contract."""


class ContextPack:
    """One complete context body split into deterministic UTF-8 chunks."""

    __slots__ = ("items", "digest", "byte_count")

    def __init__(self, items: tuple[bytes, ...], digest: str, byte_count: int) -> None:
        self.items = items
        self.digest = digest
        self.byte_count = byte_count

    @classmethod
    def build(cls, items: tuple[bytes, ...], /) -> ContextPack:
        """Build a full pack from already-chunked bytes."""

        for item in items:
            if not isinstance(item, (bytes, bytearray)):
                raise ContextPackError("pack items must be bytes")
            item.decode("utf-8")
        digest = _pack_digest(items)
        byte_count = _pack_length(items, digest)
        return cls(tuple(bytes(item) for item in items), digest, byte_count)

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Split text into bounded, valid UTF-8 chunks and build the pack."""

        if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int) or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")
        if not isinstance(content, str):
            raise ContextPackError("content must be text")
        items: list[bytes] = []
        buffer = bytearray()
        for character in content:
            encoded = character.encode("utf-8")
            if len(encoded) > chunk_bytes:
                raise ContextPackError("a character does not fit into one chunk")
            if len(buffer) + len(encoded) > chunk_bytes:
                items.append(bytes(buffer))
                buffer = bytearray()
            buffer.extend(encoded)
        if buffer:
            items.append(bytes(buffer))
        return cls.build(tuple(items))

    def to_bytes(self) -> bytes:
        """Serialize this pack as canonical compact JSON."""

        return _pack_json(self.items, self.digest, self.byte_count)

    @classmethod
    def from_bytes(cls, data: bytes, /) -> ContextPack:
        """Parse and verify one serialized pack."""

        payload = _load_json(data)
        if set(payload) != _PACK_FIELDS:
            raise ContextPackError("pack must contain exactly the declared fields")
        if payload["schema"] != CONTEXT_PACK_SCHEMA:
            raise ContextPackError("unsupported pack schema")
        items = _decode_items(payload["items"])
        digest = payload["digest"]
        if not _valid_digest(digest):
            raise ContextPackError("pack digest is malformed")
        if digest != _pack_digest(items):
            raise ContextPackError("pack digest does not match its items")
        byte_count = payload["byte_count"]
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
            raise ContextPackError("pack byte_count must be a non-negative integer")
        pack = cls(items, digest, byte_count)
        if len(pack.to_bytes()) != byte_count:
            raise ContextPackError("pack byte_count does not match its serialized length")
        return pack

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, ContextPack)
            and self.items == other.items
            and self.digest == other.digest
            and self.byte_count == other.byte_count
        )

    def __repr__(self) -> str:
        return f"ContextPack(items={len(self.items)}, byte_count={self.byte_count})"


class ContextPackDelta:
    """A shared prefix of an existing pack plus the target suffix."""

    __slots__ = ("base_digest", "target_digest", "prefix_items", "appended")

    def __init__(
        self,
        base_digest: str,
        target_digest: str,
        prefix_items: int,
        appended: tuple[bytes, ...],
    ) -> None:
        self.base_digest = base_digest
        self.target_digest = target_digest
        self.prefix_items = prefix_items
        self.appended = tuple(appended)

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack, /) -> ContextPackDelta:
        """Reuse the longest common item prefix and carry the target suffix."""

        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("delta endpoints must be ContextPack values")
        prefix = 0
        limit = min(len(base.items), len(target.items))
        while prefix < limit and base.items[prefix] == target.items[prefix]:
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize this delta as canonical compact JSON."""

        payload = {
            "schema": CONTEXT_PACK_DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": [base64.b64encode(item).decode("ascii") for item in self.appended],
        }
        return _dump(payload)

    @classmethod
    def from_bytes(cls, data: bytes, /) -> ContextPackDelta:
        """Parse and verify one serialized delta."""

        payload = _load_json(data)
        if set(payload) != _DELTA_FIELDS:
            raise ContextPackError("delta must contain exactly the declared fields")
        if payload["schema"] != CONTEXT_PACK_DELTA_SCHEMA:
            raise ContextPackError("unsupported delta schema")
        base_digest = payload["base_digest"]
        target_digest = payload["target_digest"]
        if not _valid_digest(base_digest) or not _valid_digest(target_digest):
            raise ContextPackError("delta digests are malformed")
        prefix = payload["prefix_items"]
        if isinstance(prefix, bool) or not isinstance(prefix, int) or prefix < 0:
            raise ContextPackError("delta prefix_items must be a non-negative integer")
        appended = _decode_items(payload["appended"])
        return cls(base_digest, target_digest, prefix, appended)


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta, /) -> ContextPack:
    """Rebuild the target pack from a base pack and one delta."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("base and delta must use the declared types")
    if base.digest != delta.base_digest:
        raise ContextPackError("delta base digest does not match the base pack")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix is longer than the base pack")
    target = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("delta target digest does not match the rebuilt pack")
    return target


def _dump(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pack_digest(items: tuple[bytes, ...]) -> str:
    unsigned = {
        "schema": CONTEXT_PACK_SCHEMA,
        "items": [base64.b64encode(item).decode("ascii") for item in items],
    }
    return "sha256:" + hashlib.sha256(_dump(unsigned)).hexdigest()


def _pack_json(items: tuple[bytes, ...], digest: str, byte_count: int) -> bytes:
    return _dump(
        {
            "schema": CONTEXT_PACK_SCHEMA,
            "items": [base64.b64encode(item).decode("ascii") for item in items],
            "digest": digest,
            "byte_count": byte_count,
        }
    )


def _pack_length(items: tuple[bytes, ...], digest: str) -> int:
    byte_count = 0
    while True:
        length = len(_pack_json(items, digest, byte_count))
        if length == byte_count:
            return byte_count
        byte_count = length


def _load_json(data: bytes) -> dict[str, Any]:
    if not isinstance(data, (bytes, bytearray)):
        raise ContextPackError("wire payload must be bytes")
    try:
        payload = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextPackError("wire payload must be UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ContextPackError("wire payload must be a JSON object")
    return payload


def _decode_items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("chunk items must be a JSON array")
    items: list[bytes] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ContextPackError("chunk items must be base64 strings")
        try:
            chunk = base64.b64decode(entry, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ContextPackError("chunk items must be valid base64") from error
        try:
            chunk.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ContextPackError("chunk items must be valid UTF-8") from error
        items.append(chunk)
    return tuple(items)


def _valid_digest(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    hex_part = value[len("sha256:") :]
    if len(hex_part) != 64 or hex_part != hex_part.lower():
        return False
    try:
        int(hex_part, 16)
    except ValueError:
        return False
    return True


__all__ = [
    "CONTEXT_PACK_DELTA_SCHEMA",
    "CONTEXT_PACK_SCHEMA",
    "DEFAULT_CHUNK_BYTES",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
