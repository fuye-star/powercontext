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

"""Row-level CSV Sources that keep every original record addressable."""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_SOURCE_VERSION = "1"


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document to split into row Sources."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One CSV record addressed by its document and primary key value."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """A captured CSV row that keeps its original fields and column order."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the delimiter-safe identity of one CSV row."""

    _require_document_id(document_id)
    if not isinstance(primary_key_value, str):
        raise ValueError("primary_key_value must be a string")  # noqa: TRY003
    source_id = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"CSV row source id must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return source_id


def parse_csv_document(value: CSVDocumentInput, /) -> tuple[CSVRowInput, ...]:
    """Parse and validate a whole CSV document before returning any row."""

    document_id = value.document_id
    primary_key = value.primary_key
    _require_document_id(document_id)
    if not isinstance(primary_key, str) or not primary_key:
        raise ValueError("primary_key must be a non-empty string")  # noqa: TRY003

    try:
        text = value.content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV content must be valid UTF-8") from error

    try:
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ValueError("CSV content is malformed") from error

    if not records:
        raise ValueError("CSV document must start with a header record")  # noqa: TRY003
    header = records[0]
    columns = _validate_header(header)
    try:
        key_index = columns.index(primary_key)
    except ValueError as error:
        raise ValueError(f"primary key {primary_key!r} is not a header column") from error  # noqa: TRY003

    rows: list[CSVRowInput] = []
    seen: set[str] = set()
    for record in records[1:]:
        if not record:
            # A completely empty physical line carries no record.
            continue
        if len(record) != len(columns):
            raise ValueError("CSV record width must match the header width")  # noqa: TRY003
        primary_key_value = record[key_index]
        if not primary_key_value or not primary_key_value.strip():
            raise ValueError("CSV primary key value must not be blank")  # noqa: TRY003
        if primary_key_value in seen:
            raise ValueError(f"duplicate CSV primary key value {primary_key_value!r}")  # noqa: TRY003
        seen.add(primary_key_value)
        # Validate the stable identity before accepting the row.
        csv_row_source_id(document_id, primary_key_value)
        rows.append(
            CSVRowInput(
                document_id=document_id,
                primary_key=primary_key,
                primary_key_value=primary_key_value,
                columns=columns,
                values=tuple(record),
            )
        )
    return tuple(rows)


def _require_document_id(document_id: object) -> None:
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003


def _validate_header(header: list[str]) -> tuple[str, ...]:
    if not header:
        raise ValueError("CSV header must not be empty")  # noqa: TRY003
    seen: set[str] = set()
    for column in header:
        if not column or not column.strip():
            raise ValueError("CSV header fields must be non-empty and not blank")  # noqa: TRY003
        if column in seen:
            raise ValueError(f"duplicate CSV header field {column!r}")  # noqa: TRY003
        seen.add(column)
    return tuple(header)


class _CSVRowSourceAdapter:
    """Resolve and read row Sources without changing the catalog."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        identity = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=identity,
            source_id=identity,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class _CSVRowTextEvidenceProjection:
    """Project one CSV row into the canonical text evidence shape."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        field = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(field, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": field,
            },
        )


CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    _CSVRowSourceAdapter(),
    version=CSV_ROW_SOURCE_VERSION,
    projections=(_CSVRowTextEvidenceProjection(),),
)

__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSV_ROW_SOURCE_VERSION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
