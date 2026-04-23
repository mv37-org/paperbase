"""Remote source clients for arXiv and Semantic Scholar."""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import requests

from .config import DEFAULT_AI_CATEGORIES
from .models import PaperMetadata
from .rate_limit import ARXIV_LIMITER
from .utils import canonical_arxiv_id

logger = logging.getLogger(__name__)


ARXIV_API = "http://export.arxiv.org/api/query"
S2_API = "https://api.semanticscholar.org/graph/v1"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "paperbase/0.1.0 (scientific paper indexer)"})


def _extract_arxiv_id(entry: ET.Element) -> str:
    raw = entry.findtext("atom:id", "", ARXIV_NS)
    match = re.search(r"(\d{4}\.\d{4,5})(v\d+)?$", raw)
    if match:
        return match.group(1)
    return canonical_arxiv_id(raw.split("/abs/")[-1])


def _parse_arxiv_entry(entry: ET.Element) -> PaperMetadata:
    authors = [author.findtext("atom:name", "", ARXIV_NS) for author in entry.findall("atom:author", ARXIV_NS)]
    categories = [category.get("term", "") for category in entry.findall("atom:category", ARXIV_NS)]
    primary = entry.find("arxiv:primary_category", ARXIV_NS)
    links = {link.get("title", "alternate"): link.get("href", "") for link in entry.findall("atom:link", ARXIV_NS)}
    arxiv_id = _extract_arxiv_id(entry)
    return PaperMetadata(
        arxiv_id=arxiv_id,
        title=re.sub(r"\s+", " ", entry.findtext("atom:title", "", ARXIV_NS)).strip(),
        authors=", ".join(authors),
        abstract=re.sub(r"\s+", " ", entry.findtext("atom:summary", "", ARXIV_NS)).strip(),
        categories=", ".join(categories),
        primary_category=primary.get("term", "") if primary is not None else (categories[0] if categories else ""),
        published=entry.findtext("atom:published", "", ARXIV_NS)[:10],
        updated=entry.findtext("atom:updated", "", ARXIV_NS)[:10],
        doi=entry.findtext("arxiv:doi", None, ARXIV_NS),
        comment=entry.findtext("arxiv:comment", None, ARXIV_NS),
        journal_ref=entry.findtext("arxiv:journal_ref", None, ARXIV_NS),
        pdf_url=links.get("pdf", f"https://arxiv.org/pdf/{arxiv_id}.pdf"),
        html_url=f"https://arxiv.org/html/{arxiv_id}",
    )


def search_arxiv(
    query: str,
    max_results: int = 20,
    *,
    category: str | None = None,
    sort_by: str = "relevance",
) -> list[PaperMetadata]:
    if category:
        search_query = f"cat:{category} AND ({query})"
    elif not any(f"cat:{item}" in query for item in DEFAULT_AI_CATEGORIES):
        cat_filter = " OR ".join(f"cat:{item}" for item in DEFAULT_AI_CATEGORIES[:7])
        search_query = f"({cat_filter}) AND ({query})"
    else:
        search_query = query
    sort_map = {"relevance": "relevance", "date": "submittedDate", "citations": "relevance"}
    ARXIV_LIMITER.acquire()
    response = SESSION.get(
        ARXIV_API,
        params={
            "search_query": search_query,
            "start": 0,
            "max_results": min(max_results, 100),
            "sortBy": sort_map.get(sort_by, "relevance"),
            "sortOrder": "descending",
        },
        timeout=30,
    )
    response.raise_for_status()
    root = ET.fromstring(response.text)
    return [_parse_arxiv_entry(entry) for entry in root.findall("atom:entry", ARXIV_NS)]


def fetch_recent(category: str, *, days: int = 7, max_results: int = 100) -> list[PaperMetadata]:
    ARXIV_LIMITER.acquire()
    response = SESSION.get(
        ARXIV_API,
        params={
            "search_query": f"cat:{category}",
            "start": 0,
            "max_results": min(max_results, 200),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
        timeout=30,
    )
    response.raise_for_status()
    cutoff = (datetime.now(tz=UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    root = ET.fromstring(response.text)
    papers = []
    for entry in root.findall("atom:entry", ARXIV_NS):
        paper = _parse_arxiv_entry(entry)
        if paper.published >= cutoff:
            papers.append(paper)
    return papers


def fetch_range(
    category: str,
    *,
    start_date: str,
    end_date: str,
    start_offset: int = 0,
    page_size: int = 100,
) -> tuple[list[PaperMetadata], int | None]:
    """Fetch a page of papers in ``category`` submitted between ``start_date``
    and ``end_date`` (inclusive, ``YYYY-MM-DD``).

    Returns ``(papers, next_cursor)``. ``next_cursor`` is the ``start_offset``
    to pass on the next call when the current page was full, or ``None`` when
    the response was short (last page). Used by the ``harvest_chunk`` worker
    to resume mid-chunk after a retry.
    """
    capped = max(1, min(page_size, 200))
    start_param = _date_to_arxiv_timestamp(start_date, end_of_day=False)
    end_param = _date_to_arxiv_timestamp(end_date, end_of_day=True)
    ARXIV_LIMITER.acquire()
    response = SESSION.get(
        ARXIV_API,
        params={
            "search_query": f"cat:{category} AND submittedDate:[{start_param} TO {end_param}]",
            "start": max(0, start_offset),
            "max_results": capped,
            "sortBy": "submittedDate",
            "sortOrder": "ascending",
        },
        timeout=30,
    )
    response.raise_for_status()
    root = ET.fromstring(response.text)
    entries = root.findall("atom:entry", ARXIV_NS)
    papers = [_parse_arxiv_entry(entry) for entry in entries]
    next_cursor = start_offset + len(papers) if len(papers) >= capped else None
    return papers, next_cursor


def _date_to_arxiv_timestamp(value: str, *, end_of_day: bool) -> str:
    """Convert ``YYYY-MM-DD`` to arXiv's ``YYYYMMDDhhmm`` query format."""
    cleaned = (value or "").strip().replace("-", "")
    if len(cleaned) != 8 or not cleaned.isdigit():
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}")
    return f"{cleaned}{'2359' if end_of_day else '0000'}"


def get_paper_details(arxiv_id: str) -> PaperMetadata | None:
    ARXIV_LIMITER.acquire()
    response = SESSION.get(
        ARXIV_API,
        params={"id_list": arxiv_id, "max_results": 1},
        timeout=15,
    )
    response.raise_for_status()
    root = ET.fromstring(response.text)
    entries = root.findall("atom:entry", ARXIV_NS)
    if not entries:
        return None
    return _parse_arxiv_entry(entries[0])


@dataclass(slots=True)
class EnrichmentOutcome:
    """Result of a Semantic Scholar enrichment pass.

    Callers use the counts to log observability (doctor reports success
    rate at scale) and to decide whether to enqueue a backfill job.
    """
    attempted: int = 0
    enriched: int = 0
    skipped_empty: int = 0
    batches_failed: int = 0
    batches_succeeded: int = 0

    @property
    def success_rate(self) -> float:
        return self.enriched / self.attempted if self.attempted else 0.0


def _s2_batch_with_retry(
    ids: list[str],
    *,
    max_retries: int,
    base_backoff_seconds: float,
    timeout_seconds: float,
) -> list[dict] | None:
    """POST one batch to Semantic Scholar with exponential backoff on 429/5xx.

    Honors the ``Retry-After`` header when present, otherwise falls back to
    exponential backoff: base * (4 ** attempt), i.e. 1s / 4s / 16s / 64s at
    base=1.0. Returns the parsed JSON list on success, or ``None`` after the
    final retry is exhausted. The caller logs and decides whether to requeue.
    """
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = SESSION.post(
                f"{S2_API}/paper/batch",
                json={"ids": ids},
                params={"fields": "citationCount,influentialCitationCount,externalIds,tldr"},
                timeout=timeout_seconds,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            sleep_for = base_backoff_seconds * (4 ** attempt)
            logger.warning(
                "S2 batch request failed, retrying",
                extra={"event": "s2.request_error", "attempt": attempt + 1, "sleep": sleep_for, "error": str(exc)},
            )
            time.sleep(sleep_for)
            continue

        if response.status_code in (429, 500, 502, 503, 504):
            if attempt >= max_retries:
                last_error = requests.HTTPError(f"HTTP {response.status_code} after {attempt + 1} attempts")
                break
            retry_after_header = response.headers.get("Retry-After")
            sleep_for = base_backoff_seconds * (4 ** attempt)
            if retry_after_header:
                try:
                    sleep_for = max(sleep_for, float(retry_after_header))
                except ValueError:
                    pass
            logger.warning(
                "S2 rate-limited / 5xx, backing off",
                extra={"event": "s2.backoff", "status": response.status_code, "attempt": attempt + 1, "sleep": sleep_for},
            )
            time.sleep(sleep_for)
            continue

        if not response.ok:
            last_error = requests.HTTPError(f"HTTP {response.status_code}: {response.text[:200]}")
            break

        try:
            payload = response.json()
        except ValueError as exc:
            last_error = exc
            break
        if not isinstance(payload, list):
            last_error = ValueError(f"unexpected S2 response shape: {type(payload).__name__}")
            break
        return payload

    logger.warning(
        "S2 batch enrichment gave up",
        extra={
            "event": "s2.give_up",
            "batch_size": len(ids),
            "error": str(last_error) if last_error else "",
        },
    )
    return None


def enrich_with_semantic_scholar(
    papers: list[PaperMetadata],
    *,
    batch_size: int = 20,
    max_retries: int = 4,
    base_backoff_seconds: float = 1.0,
    inter_batch_sleep_seconds: float = 1.0,
    timeout_seconds: float = 15.0,
) -> EnrichmentOutcome:
    """Populate citation / influential-cite / S2 id / tldr on each paper in-place.

    Retries each batch up to ``max_retries`` times on 429 / 5xx / connection
    errors, with exponential backoff (1s, 4s, 16s, 64s by default). Honors
    the Retry-After response header. A batch that exhausts its retries is
    logged and skipped — the caller can re-enqueue those papers via the
    ``enrich_missing_metadata`` job for a later pass.

    For inline (index-time) enrichment, set ``max_retries=1`` so a single
    transient failure doesn't block ingestion. For backfill, use the default
    so stragglers get aggressive retry coverage.
    """
    outcome = EnrichmentOutcome(attempted=len(papers))
    for offset in range(0, len(papers), batch_size):
        batch = papers[offset: offset + batch_size]
        ids = [f"ArXiv:{paper.arxiv_id}" for paper in batch]
        payload = _s2_batch_with_retry(
            ids,
            max_retries=max_retries,
            base_backoff_seconds=base_backoff_seconds,
            timeout_seconds=timeout_seconds,
        )
        if payload is None:
            outcome.batches_failed += 1
            continue
        outcome.batches_succeeded += 1
        for paper, item in zip(batch, payload):
            if not item:
                outcome.skipped_empty += 1
                continue
            paper.citation_count = item.get("citationCount", 0) or 0
            paper.influential_citation_count = item.get("influentialCitationCount", 0) or 0
            paper.semantic_scholar_id = item.get("paperId")
            tldr_payload = item.get("tldr") or {}
            tldr_text = tldr_payload.get("text") if isinstance(tldr_payload, dict) else None
            if tldr_text:
                paper.tldr = tldr_text
            outcome.enriched += 1
        if offset + batch_size < len(papers):
            time.sleep(inter_batch_sleep_seconds)
    return outcome


def search_semantic_scholar(
    query: str,
    *,
    limit: int = 20,
    year: str | None = None,
) -> list[PaperMetadata]:
    params = {
        "query": query,
        "limit": min(limit, 100),
        "fields": "externalIds,title,abstract,authors,year,citationCount,influentialCitationCount,tldr,publicationDate",
        "fieldsOfStudy": "Computer Science",
    }
    if year:
        params["year"] = year
    response = SESSION.get(f"{S2_API}/paper/search", params=params, timeout=15)
    if response.status_code != 200:
        return []
    papers: list[PaperMetadata] = []
    for item in response.json().get("data", []):
        external_ids = item.get("externalIds", {}) or {}
        arxiv_id = external_ids.get("ArXiv", "")
        if not arxiv_id:
            continue
        authors = ", ".join(author.get("name", "") for author in (item.get("authors") or []))
        tldr = item.get("tldr") or {}
        tldr_text = tldr.get("text") if isinstance(tldr, dict) else ""
        papers.append(
            PaperMetadata(
                arxiv_id=canonical_arxiv_id(arxiv_id),
                title=item.get("title", ""),
                authors=authors,
                abstract=item.get("abstract") or tldr_text or "",
                tldr=tldr_text or "",
                published=(item.get("publicationDate") or "")[:10],
                updated="",
                doi=external_ids.get("DOI"),
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                html_url=f"https://arxiv.org/html/{arxiv_id}",
                citation_count=item.get("citationCount", 0) or 0,
                influential_citation_count=item.get("influentialCitationCount", 0) or 0,
                semantic_scholar_id=item.get("paperId"),
            )
        )
    return papers


def fetch_references(
    arxiv_id: str,
    semantic_scholar_id: str | None,
    *,
    limit: int = 100,
) -> list[dict]:
    """Fetch outgoing citations from Semantic Scholar.

    Returns a list of dicts with keys: arxiv_id, semantic_scholar_id, title,
    rank. Only references that have an arXiv id are returned so we can link
    them back into our own graph.
    """
    resolved_s2_id = semantic_scholar_id
    if not resolved_s2_id:
        # Interactive path: keep the retry budget tight so a rate-limited S2
        # doesn't block the user on 1/4/16/64s backoff. The enrich_missing
        # backfill job owns the aggressive retry ladder.
        stub = PaperMetadata(arxiv_id=arxiv_id)
        enrich_with_semantic_scholar([stub], max_retries=1, base_backoff_seconds=0.5)
        resolved_s2_id = stub.semantic_scholar_id
    if not resolved_s2_id:
        return []
    try:
        response = SESSION.get(
            f"{S2_API}/paper/{resolved_s2_id}/references",
            params={"fields": "externalIds,title", "limit": min(limit, 100)},
            timeout=20,
        )
        if response.status_code != 200:
            return []
    except requests.RequestException:
        return []
    out: list[dict] = []
    for rank, item in enumerate(response.json().get("data", [])):
        cited = item.get("citedPaper") or {}
        external_ids = cited.get("externalIds") or {}
        ref_arxiv = external_ids.get("ArXiv")
        if not ref_arxiv:
            continue
        out.append(
            {
                "arxiv_id": canonical_arxiv_id(ref_arxiv),
                "semantic_scholar_id": cited.get("paperId"),
                "title": cited.get("title") or "",
                "rank": rank,
            }
        )
    return out


def get_related_papers(arxiv_id: str, semantic_scholar_id: str | None, *, limit: int = 10) -> list[PaperMetadata]:
    resolved_s2_id = semantic_scholar_id
    if not resolved_s2_id:
        # Interactive path: keep the retry budget tight so a rate-limited S2
        # doesn't block the user on 1/4/16/64s backoff. The enrich_missing
        # backfill job owns the aggressive retry ladder.
        stub = PaperMetadata(arxiv_id=arxiv_id)
        enrich_with_semantic_scholar([stub], max_retries=1, base_backoff_seconds=0.5)
        resolved_s2_id = stub.semantic_scholar_id
    if not resolved_s2_id:
        return []
    response = SESSION.get(
        f"{S2_API}/paper/{resolved_s2_id}/recommendations",
        params={
            "fields": "externalIds,title,abstract,authors,year,citationCount,publicationDate",
            "limit": limit,
        },
        timeout=20,
    )
    response.raise_for_status()
    results: list[PaperMetadata] = []
    for item in response.json().get("recommendedPapers", []):
        external_ids = item.get("externalIds", {}) or {}
        related_arxiv_id = external_ids.get("ArXiv", "")
        if not related_arxiv_id:
            continue
        results.append(
            PaperMetadata(
                arxiv_id=canonical_arxiv_id(related_arxiv_id),
                title=item.get("title", ""),
                authors=", ".join(author.get("name", "") for author in (item.get("authors") or [])),
                abstract=item.get("abstract") or "",
                published=(item.get("publicationDate") or "")[:10],
                updated="",
                doi=external_ids.get("DOI"),
                pdf_url=f"https://arxiv.org/pdf/{related_arxiv_id}.pdf",
                html_url=f"https://arxiv.org/html/{related_arxiv_id}",
                citation_count=item.get("citationCount", 0) or 0,
                semantic_scholar_id=item.get("paperId"),
            )
        )
    return results
