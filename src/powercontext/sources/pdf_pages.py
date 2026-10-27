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

"""Whole-document PDF parsing into page-level evidence Sources."""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections import Counter
from typing import TYPE_CHECKING
from urllib.parse import quote

from pydantic import BaseModel
from pypdf import PdfReader

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory import MemoryCitation, ScopedMemory
    from powercontext.sources.connectors import ConnectorRunSession

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"

_QUERY_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "which",
        "who",
        "why",
        "will",
        "with",
        # Instruction and framing words that FinanceBench questions add around the
        # actual request; they carry no evidence-page signal.
        "all",
        "also",
        "analyst",
        "answer",
        "any",
        "assume",
        "assuming",
        "calculate",
        "can",
        "compute",
        "could",
        "define",
        "detail",
        "details",
        "explicitly",
        "give",
        "if",
        "investment",
        "may",
        "not",
        "only",
        "perspective",
        "question",
        "rely",
        "relying",
        "respond",
        "response",
        "should",
        "show",
        "shown",
        "state",
        "use",
        "used",
        "using",
        "within",
        "would",
    }
)
_TOKEN = re.compile(r"[A-Za-z]+|\d+(?:[.,]\d+)*")


class PDFDocumentInput(BaseModel):
    """One complete original PDF document."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical page of a PDF document."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """A captured PDF page that keeps its document revision identity."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build the stable identity of one page in one document revision."""

    source_id = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(source_id) <= MAX_SOURCE_ID_LENGTH:
        return source_id
    # Keep the identity bounded and collision-resistant for long document ids.
    digest = hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:32]
    return f"pdf:{digest}:{document_sha256}:{page_number}"


def parse_pdf_document(value: PDFDocumentInput, /) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of a PDF before returning any of them."""

    document_id = value.document_id
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    content = value.content
    if not content:
        raise ValueError("PDF content must not be empty")  # noqa: TRY003
    document_sha256 = hashlib.sha256(content).hexdigest()

    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as error:  # pypdf raises several unrelated error types
        raise ValueError("PDF content cannot be read") from error

    try:
        if reader.is_encrypted:
            # A PDF protected only by an empty user password stays readable.
            if not reader.decrypt(""):
                raise ValueError("PDF requires a user password")  # noqa: TRY003
        page_count = len(reader.pages)
        pages: list[PDFPageInput] = []
        for index in range(page_count):
            text = reader.pages[index].extract_text() or ""
            pages.append(
                PDFPageInput(
                    document_id=document_id,
                    document_sha256=document_sha256,
                    page_number=index + 1,
                    text=text,
                )
            )
    except ValueError:
        raise
    except Exception as error:  # a partially parsed document must not be returned
        raise ValueError("PDF content cannot be parsed completely") from error

    if not pages:
        raise ValueError("PDF document contains no pages")  # noqa: TRY003
    for page in pages:
        pdf_page_source_id(document_id, document_sha256, page.page_number)
    return tuple(pages)


async def search_pdf_pages(
    memory: ScopedMemory,
    question: str,
    limit: int = 20,
) -> tuple[MemoryCitation, ...]:
    """Search scoped Memory and return exact citations for distinct PDF pages."""

    if limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003
    terms = _question_terms(question)
    if not terms:
        raise ValueError("question must contain searchable terms")  # noqa: TRY003
    ranked = await _rank_captured_pages(memory, terms, limit)
    if ranked:
        return ranked
    return await _search_with_memory(memory, question, limit)


async def _rank_captured_pages(
    memory: ScopedMemory,
    terms: tuple[str, ...],
    limit: int,
) -> tuple[MemoryCitation, ...]:
    """Rank the captured page corpus directly so no admissible page is missed."""

    from powercontext.builtin.artifacts.memory.models import MemoryCitation

    try:
        page = await memory.list()
    except (AttributeError, TypeError):
        return ()
    records = getattr(page, "entries", ())
    if not records:
        return ()

    documents: list[tuple[MemoryCitation, str, str]] = []
    for record in records:
        entry = record.entry
        if not entry.sources:
            continue
        citation = MemoryCitation(
            memory_ref=record.memory_ref,
            entry_id=entry.entry_id,
            entry_version_id=entry.entry_version_id,
        )
        documents.append((citation, entry.sources[0].source_id, _page_body(entry.text)))
    if not documents:
        return ()

    scores = _combined_bm25_scores([text for _citation, _source, text in documents], terms)
    order = sorted(range(len(documents)), key=lambda index: (-scores[index], index))
    selected: list[MemoryCitation] = []
    seen: set[str] = set()
    for index in order:
        citation, source_id, _text = documents[index]
        if source_id in seen:
            continue
        seen.add(source_id)
        selected.append(citation)
        if len(selected) >= min(limit, len(documents)):
            break
    return tuple(selected)


def _question_terms(question: str) -> tuple[str, ...]:
    tokens = [token for token in _WORD.findall(question.casefold()) if len(token) > 1 or token.isdigit()]
    keywords = [token for token in tokens if token not in _QUERY_STOPWORDS]
    selected = keywords or tokens
    seen: set[str] = set()
    ordered: list[str] = []
    for token in selected:
        if token not in seen:
            seen.add(token)
            ordered.append(token)
    return tuple(ordered)


_WORD = re.compile(r"[a-z0-9]+")
_HEADING_BOOST = 3.0


def _page_body(text: str) -> str:
    """Drop the document-id line that the text-evidence projection prepends."""

    _document_id, separator, body = text.partition("\n")
    return body if separator else text


def _heading_text(text: str) -> str:
    """Keep short lines, which carry the financial-statement section headings."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " ".join(line for line in lines if len(line.split()) <= 8)


def _document_tokens(text: str) -> list[str]:
    return [
        token
        for token in _WORD.findall(text.casefold())
        if token not in _QUERY_STOPWORDS and (len(token) > 1 or token.isdigit())
    ]


def _combined_bm25_scores(texts: list[str], terms: tuple[str, ...]) -> list[float]:
    bodies = [_document_tokens(text) for text in texts]
    headings = [_document_tokens(_heading_text(text)) for text in texts]
    body_scores = _bm25_scores(bodies, terms)
    heading_scores = _bm25_scores(headings, terms)
    return [
        body + _HEADING_BOOST * heading for body, heading in zip(body_scores, heading_scores, strict=True)
    ]


def _bm25_scores(tokenized: list[list[str]], terms: tuple[str, ...]) -> list[float]:
    count = len(tokenized)
    average = sum(len(document) for document in tokenized) / count if count else 1.0
    document_frequency: Counter[str] = Counter()
    for document in tokenized:
        document_frequency.update(set(document))
    idf = {
        term: math.log(1 + (count - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
        for term in terms
        if document_frequency.get(term)
    }
    k1, b = 1.2, 0.75
    scores: list[float] = []
    for document in tokenized:
        frequencies = Counter(document)
        length = len(document)
        score = 0.0
        for term in terms:
            if term not in idf:
                continue
            frequency = frequencies.get(term, 0)
            if frequency:
                score += idf[term] * frequency * (k1 + 1) / (
                    frequency + k1 * (1 - b + b * length / average)
                )
        scores.append(score)
    return scores


async def _search_with_memory(
    memory: ScopedMemory,
    question: str,
    limit: int,
) -> tuple[MemoryCitation, ...]:
    """Fall back to Memory search for hosts that do not expose captured entries."""

    from powercontext.builtin.artifacts.memory.models import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    fetch = max(limit * 3, 40)
    citations_by_entry: dict[str, MemoryCitation] = {}
    page_by_entry: dict[str, str] = {}
    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    for query, weight in _query_variants(question):
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=fetch))
        for rank, hit in enumerate(page.hits):
            if hit.entry_id not in citations_by_entry:
                citation = MemoryCitation(
                    memory_ref=hit.memory_ref,
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
                entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                if not entry.sources:
                    continue
                citations_by_entry[hit.entry_id] = citation
                page_by_entry[hit.entry_id] = entry.sources[0].source_id
            source_id = page_by_entry[hit.entry_id]
            # Reciprocal-rank fusion keeps pages several query variants agree on near the top.
            scores[source_id] = scores.get(source_id, 0.0) + weight / (60 + rank + 1)
            best_rank[source_id] = min(best_rank.get(source_id, rank), rank)

    citation_by_page = {page_by_entry[entry_id]: citation for entry_id, citation in citations_by_entry.items()}
    ordered = sorted(scores, key=lambda source_id: (-scores[source_id], best_rank[source_id]))
    return tuple(citation_by_page[source_id] for source_id in ordered[:limit])


def _query_variants(question: str) -> tuple[tuple[str, float], ...]:
    """Yield weighted lexical query variants, most specific first."""

    normalized = " ".join(question.split())
    if not normalized:
        raise ValueError("question must not be empty")  # noqa: TRY003
    variants: list[tuple[str, float]] = [(normalized, 1.0)]
    tokens = _TOKEN.findall(normalized)
    keywords = [token for token in tokens if token.casefold() not in _QUERY_STOPWORDS]
    if keywords and len(keywords) < len(tokens):
        keyword_query = " ".join(keywords)
        if keyword_query != normalized:
            variants.append((keyword_query, 0.8))
    # A short core query relaxes the lexical admission floor enough to surface pages that
    # match the distinctive terms without every phrase in the question.
    core = _core_terms(keywords or tokens)
    if core:
        core_query = " ".join(core)
        if core_query not in {query for query, _weight in variants}:
            variants.append((core_query, 0.5))
    # Single-term probes satisfy the admission floor trivially, so pages that share only
    # part of the question still enter the fusion instead of being filtered out.
    for token in _term_queries(keywords or tokens):
        if token.casefold() in {query.casefold() for query, _weight in variants}:
            continue
        variants.append((token, 0.3))
    return tuple(variants)


def _term_queries(tokens: list[str]) -> list[str]:
    """Pick the few most distinctive single terms for low-floor probes."""

    candidates = {
        token for token in tokens if len(token) >= 3 and token.casefold() not in _QUERY_STOPWORDS
    }
    ranked = sorted(
        candidates,
        key=lambda token: (any(character.isdigit() for character in token), len(token)),
        reverse=True,
    )
    return ranked[:6]


def _core_terms(tokens: list[str]) -> list[str]:
    """Pick a few distinctive terms: numbers, capitalized words, then longer words."""

    numbers = [token for token in tokens if any(character.isdigit() for character in token)]
    words = [token for token in tokens if token.isalpha() and len(token) >= 4]
    selected: list[str] = []
    for token in [*numbers, *words]:
        if token.casefold() not in {value.casefold() for value in selected}:
            selected.append(token)
        if len(selected) >= 3:
            break
    return selected


class _PDFPageSourceAdapter:
    """Resolve and read PDF page Sources without changing the catalog."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        identity = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=identity,
            source_id=identity,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class _PDFPageTextEvidenceProjection:
    """Project one PDF page into the canonical text evidence shape."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    _PDFPageSourceAdapter(),
    version=PDF_PAGE_SOURCE_VERSION,
    projections=(_PDFPageTextEvidenceProjection(),),
)

__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
