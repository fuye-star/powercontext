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


class _PageIndex:
    """Cached lexical index over one Memory revision of captured PDF pages."""

    __slots__ = (
        "body_avg",
        "body_df",
        "body_len",
        "body_tf",
        "citations",
        "source_ids",
        "titles",
        "window_avg",
        "window_count",
        "window_df",
        "windows",
    )

    def __init__(
        self,
        *,
        citations: tuple[MemoryCitation, ...],
        source_ids: tuple[str, ...],
        titles: tuple[str, ...],
        body_tf: tuple[Counter[str], ...],
        body_len: tuple[int, ...],
        body_df: Counter[str],
        body_avg: float,
        windows: tuple[tuple[tuple[Counter[str], int], ...], ...],
        window_df: Counter[str],
        window_avg: float,
        window_count: int,
    ) -> None:
        self.citations = citations
        self.source_ids = source_ids
        self.titles = titles
        self.body_tf = body_tf
        self.body_len = body_len
        self.body_df = body_df
        self.body_avg = body_avg
        self.windows = windows
        self.window_df = window_df
        self.window_avg = window_avg
        self.window_count = window_count


_INDEX_CACHE: dict[tuple[str, str, int], _PageIndex] = {}


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
    reference = getattr(page, "memory_ref", None)
    if reference is None:
        return ()

    key = (reference.family, reference.artifact_id, reference.revision)
    index = _INDEX_CACHE.get(key)
    if index is None:
        index = _build_page_index(records, MemoryCitation)
        if index is None:
            return ()
        _INDEX_CACHE[key] = index
        while len(_INDEX_CACHE) > 4:
            _INDEX_CACHE.pop(next(iter(_INDEX_CACHE)))

    scores = _index_scores(index, terms)
    order = sorted(range(len(index.source_ids)), key=lambda position: (-scores[position], position))
    selected: list[MemoryCitation] = []
    seen: set[str] = set()
    for position in order:
        source_id = index.source_ids[position]
        if source_id in seen:
            continue
        seen.add(source_id)
        selected.append(index.citations[position])
        if len(selected) >= min(limit, len(index.source_ids)):
            break
    return tuple(selected)


def _build_page_index(records: object, citation_type: type) -> _PageIndex | None:
    citations: list[MemoryCitation] = []
    source_ids: list[str] = []
    titles: list[str] = []
    body_tf: list[Counter[str]] = []
    body_len: list[int] = []
    body_df: Counter[str] = Counter()
    windows: list[tuple[tuple[Counter[str], int], ...]] = []
    window_df: Counter[str] = Counter()
    window_total = 0
    window_count = 0
    body_total = 0

    for record in records:  # type: ignore[union-attr]
        entry = record.entry
        if not entry.sources:
            continue
        body = _page_body(entry.text)
        tokens = _document_tokens(body)
        counts = Counter(tokens)
        citations.append(
            citation_type(
                memory_ref=record.memory_ref,
                entry_id=entry.entry_id,
                entry_version_id=entry.entry_version_id,
            )
        )
        source_ids.append(entry.sources[0].source_id)
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        titles.append(" ".join(lines[:3]).upper())
        body_tf.append(counts)
        body_len.append(len(tokens))
        body_df.update(set(tokens))
        body_total += len(tokens)

        page_windows: list[tuple[Counter[str], int]] = []
        for position in range(len(lines)):
            chunk = _document_tokens(" ".join(lines[position : position + 2]))
            if not chunk:
                continue
            chunk_counts = Counter(chunk)
            page_windows.append((chunk_counts, len(chunk)))
            window_df.update(set(chunk))
            window_total += len(chunk)
            window_count += 1
        windows.append(tuple(page_windows))

    if not citations:
        return None
    return _PageIndex(
        citations=tuple(citations),
        source_ids=tuple(source_ids),
        titles=tuple(titles),
        body_tf=tuple(body_tf),
        body_len=tuple(body_len),
        body_df=body_df,
        body_avg=body_total / len(body_len) if body_len else 1.0,
        windows=tuple(windows),
        window_df=window_df,
        window_avg=window_total / window_count if window_count else 1.0,
        window_count=window_count,
    )


def _index_scores(index: _PageIndex, terms: tuple[str, ...]) -> list[float]:
    k1, b = _BM25_K1, _BM25_B
    count = len(index.source_ids)
    body_idf = {
        term: math.log(1 + (count - index.body_df[term] + 0.5) / (index.body_df[term] + 0.5))
        for term in terms
        if index.body_df.get(term)
    }
    window_idf = {
        term: math.log(1 + (index.window_count - index.window_df[term] + 0.5) / (index.window_df[term] + 0.5))
        for term in terms
        if index.window_df.get(term)
    }
    term_set = set(terms)
    wanted: set[str] = set()
    if term_set & _BALANCE_SHEET_TERMS:
        wanted.add("balance")
    if term_set & _INCOME_TERMS:
        wanted.add("income")
    if term_set & _CASH_FLOW_TERMS:
        wanted.add("cash")

    scores: list[float] = []
    for position in range(count):
        frequencies = index.body_tf[position]
        length = index.body_len[position] or 1
        score = 0.0
        for term in terms:
            frequency = frequencies.get(term)
            if frequency:
                idf = body_idf.get(term)
                if idf:
                    score += idf * frequency * (k1 + 1) / (
                        frequency + k1 * (1 - b + b * length / index.body_avg)
                    )

        best_window = 0.0
        for window_counts, window_length in index.windows[position]:
            window_score = 0.0
            for term in terms:
                frequency = window_counts.get(term)
                if frequency:
                    idf = window_idf.get(term)
                    if idf:
                        window_score += idf * frequency * (k1 + 1) / (
                            frequency + k1 * (1 - b + b * window_length / index.window_avg)
                        )
            if window_score > best_window:
                best_window = window_score
        combined = score + _WINDOW_WEIGHT * best_window

        title = index.titles[position]
        boost = 0.0
        if wanted:
            if "balance" in wanted and "BALANCE SHEET" in title:
                boost = _SECTION_BOOST
            elif "income" in wanted and (
                "STATEMENTS OF INCOME" in title
                or "INCOME STATEMENTS" in title
                or "STATEMENTS OF OPERATIONS" in title
            ):
                boost = _SECTION_BOOST
            elif "cash" in wanted and "CASH FLOW" in title:
                boost = _SECTION_BOOST
        for name, keywords in _SECTION_TERMS.items():
            if boost >= _SECTION_BOOST:
                break
            if term_set & keywords and name in title:
                boost = _SECTION_BOOST
        scores.append(combined * (1 + boost))
    return scores


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
_BM25_K1 = 0.9
_BM25_B = 0.75
_WINDOW_WEIGHT = 2.0
_SECTION_BOOST = 2.0

_BALANCE_SHEET_TERMS = frozenset(
    {
        "asset",
        "assets",
        "balance",
        "capital",
        "card",
        "current",
        "debt",
        "deposit",
        "deposits",
        "equity",
        "inventories",
        "inventory",
        "liabilities",
        "liability",
        "liquidity",
        "member",
        "members",
        "quick",
        "ratio",
        "receivable",
        "receivables",
        "working",
    }
)
_INCOME_TERMS = frozenset(
    {
        "cost",
        "costs",
        "earnings",
        "ebitda",
        "eps",
        "expense",
        "expenses",
        "gross",
        "income",
        "margin",
        "operating",
        "profit",
        "rate",
        "revenue",
        "revenues",
        "sales",
        "tax",
    }
)
_CASH_FLOW_TERMS = frozenset(
    {
        "amortization",
        "buyback",
        "capex",
        "cash",
        "depreciation",
        "dividend",
        "dividends",
        "expenditure",
        "expenditures",
        "financing",
        "flow",
        "flows",
        "investing",
        "repurchase",
    }
)
_SECTION_TERMS: dict[str, frozenset[str]] = {
    "LEGAL PROCEEDINGS": frozenset({"battles", "lawsuit", "lawsuits", "legal", "litigation", "proceedings"}),
    "GEOGRAPHIC": frozenset({"countries", "geographic", "geographies", "geography", "regions"}),
    "CONCENTRATION": frozenset({"concentration", "concentrated"}),
    "INCOME TAXES": frozenset({"tax", "taxation", "taxes"}),
    "PROPERTIES": frozenset({"facilities", "plants", "properties", "property"}),
    "HUMAN CAPITAL": frozenset({"employee", "employees", "headcount", "workforce"}),
    "SEGMENT": frozenset({"segment", "segments"}),
}


def _page_body(text: str) -> str:
    """Drop the document-id line that the text-evidence projection prepends."""

    _document_id, separator, body = text.partition("\n")
    return body if separator else text


def _document_tokens(text: str) -> list[str]:
    return [
        token
        for token in _WORD.findall(text.casefold())
        if token not in _QUERY_STOPWORDS and (len(token) > 1 or token.isdigit())
    ]


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
