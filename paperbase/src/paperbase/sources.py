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
class TopCitedCandidate:
    """Lightweight S2 search result used by the top-cited harvest planner.

    The planner doesn't need a full ``PaperMetadata`` (extraction will
    refetch arxiv metadata at index time); it only needs enough fields to
    decide whether the paper passes the AI/ML category filter and to
    enqueue an indexing chunk.
    """

    arxiv_id: str
    semantic_scholar_id: str
    title: str
    citation_count: int
    primary_category: str | None
    year: int | None


def fetch_top_cited_cs(
    *,
    min_citations: int,
    cursor: str | None = None,
    page_size: int = 1000,
    fields_of_study: str = "Computer Science",
    timeout_seconds: int = 30,
) -> tuple[list[TopCitedCandidate], str | None]:
    """Page through Semantic Scholar's bulk search ordered by citationCount
    desc, returning candidates that have an arXiv external id.

    Returns ``(candidates, next_cursor)``. When ``next_cursor`` is ``None``
    the planner has exhausted the result set; otherwise the caller passes
    it back to fetch the next page.

    The S2 endpoint returns ~1000 results/page and emits a continuation
    token. We filter to arxiv-linked papers client-side so the planner
    can reason about a single id namespace; non-arxiv high-cited CS
    papers (R, BLAST, SHELX, etc.) are out of scope for the AI/ML
    corpus this primitive feeds (see issue #39).
    """
    if min_citations < 0:
        raise ValueError(f"min_citations must be >= 0, got {min_citations}")
    if page_size < 1 or page_size > 1000:
        raise ValueError(f"page_size must be in [1, 1000], got {page_size}")

    params: dict[str, object] = {
        "query": "*",
        "fieldsOfStudy": fields_of_study,
        "sort": "citationCount:desc",
        "fields": "paperId,title,citationCount,externalIds,year",
        "minCitationCount": int(min_citations),
        "limit": page_size,
    }
    if cursor:
        params["token"] = cursor

    response = SESSION.get(
        f"{S2_API}/paper/search/bulk",
        params=params,
        timeout=timeout_seconds,
    )
    if response.status_code != 200:
        logger.warning(
            "fetch_top_cited_cs non-200",
            extra={
                "event": "sources.top_cited.non_200",
                "status_code": response.status_code,
                "min_citations": min_citations,
                "has_cursor": cursor is not None,
            },
        )
        response.raise_for_status()

    payload = response.json() or {}
    next_cursor = payload.get("token") or None
    raw_results = payload.get("data") or []

    candidates: list[TopCitedCandidate] = []
    for item in raw_results:
        external_ids = item.get("externalIds") or {}
        raw_arxiv = external_ids.get("ArXiv")
        if not raw_arxiv:
            continue
        candidates.append(
            TopCitedCandidate(
                arxiv_id=canonical_arxiv_id(raw_arxiv),
                semantic_scholar_id=item.get("paperId") or "",
                title=item.get("title") or "",
                citation_count=int(item.get("citationCount") or 0),
                # S2 doesn't expose arxiv primary_category on bulk search;
                # the planner does the per-arxiv-id category lookup downstream
                # if a tighter category gate is needed. None here means
                # "not yet checked".
                primary_category=None,
                year=item.get("year"),
            )
        )
    return candidates, next_cursor


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


# --------------------------------------------------------------------------- #
# Papers With Code crosslink (#46 layer 1)
# --------------------------------------------------------------------------- #


PWC_API = "https://paperswithcode.com/api/v1/papers"


def fetch_papers_with_code_repo(
    arxiv_id: str,
    *,
    session: requests.Session | None = None,
    timeout: float = 10.0,
) -> str | None:
    """Return the canonical GitHub URL that Papers With Code considers the
    paper's official implementation, or ``None`` (#46 layer 1).

    Prefers the entry whose ``is_official`` flag is true; if no official
    entry exists, returns ``None`` rather than falling back to a
    community reimplementation — that decision belongs to the admin
    override layer, not the primary signal. Network failures / rate
    limits / missing papers degrade to ``None`` so index-time callers
    can proceed with the ranker fallback.

    The endpoint is
    ``GET {PWC_API}/?arxiv_id=<id>`` which returns a paginated list;
    the first result is followed via ``{PWC_API}/{id}/repositories/``
    to enumerate repos with the ``is_official`` flag.
    """
    if not arxiv_id:
        return None
    client = session or SESSION
    try:
        paper_response = client.get(
            f"{PWC_API}/", params={"arxiv_id": arxiv_id}, timeout=timeout
        )
    except requests.RequestException as exc:
        logger.warning(
            "Papers With Code lookup failed",
            extra={"event": "pwc.lookup_failed", "arxiv_id": arxiv_id, "error": str(exc)},
        )
        return None
    if paper_response.status_code == 404:
        return None
    if paper_response.status_code != 200:
        logger.warning(
            "Papers With Code non-200",
            extra={
                "event": "pwc.non_200",
                "arxiv_id": arxiv_id,
                "status": paper_response.status_code,
            },
        )
        return None
    try:
        paper_body = paper_response.json()
    except ValueError:
        return None
    results = paper_body.get("results") or []
    if not results:
        return None
    paper_id = results[0].get("id")
    if not paper_id:
        return None
    try:
        repos_response = client.get(
            f"{PWC_API}/{paper_id}/repositories/", timeout=timeout
        )
    except requests.RequestException as exc:
        logger.warning(
            "Papers With Code repo fetch failed",
            extra={
                "event": "pwc.repos_failed",
                "arxiv_id": arxiv_id,
                "paper_id": paper_id,
                "error": str(exc),
            },
        )
        return None
    if repos_response.status_code != 200:
        return None
    try:
        repos_body = repos_response.json()
    except ValueError:
        return None
    repo_results = repos_body.get("results") or []
    for repo in repo_results:
        if repo.get("is_official") and repo.get("url"):
            return str(repo["url"])
    # No official entry: decline rather than guess — the ranker / admin
    # override is responsible for non-official cases.
    return None


# --------------------------------------------------------------------------- #
# GitHub README verifier (#46 follow-up — alternative to PWC)
# --------------------------------------------------------------------------- #


_GITHUB_API_BASE = "https://api.github.com"


# Affiliation patterns mapped to canonical GitHub org slugs (#46 follow-up
# layer — recall booster for papers whose body doesn't link the repo).
# Used by ``find_official_repo_via_affiliation`` to scope a GitHub search
# to the paper's authors' organizations. Patterns are matched
# case-insensitively against the full paper text (authors + abstract).
_LAB_AFFILIATIONS: dict[str, tuple[str, ...]] = {
    "facebookresearch": (
        "facebook ai research",
        "facebook ai",
        "fair labs",
        " fair ",
        "meta ai",
        "@fb.com",
        "@meta.com",
    ),
    "google-research": (
        "google research",
        "google brain",
        "@google.com",
    ),
    "openai": (
        "openai",
        "@openai.com",
    ),
    "microsoft": (
        "microsoft research",
        "@microsoft.com",
    ),
    "deepmind": (
        "deepmind",
        "@deepmind.com",
    ),
    "huggingface": (
        "hugging face",
        "@huggingface.co",
    ),
    "nvidia": (
        "nvidia research",
        "@nvidia.com",
    ),
    "salesforce": (
        "salesforce research",
        "@salesforce.com",
    ),
    "allenai": (
        "allen institute for ai",
        "allenai",
        "@allenai.org",
    ),
    "anthropic": (
        "anthropic",
        "@anthropic.com",
    ),
}


_TITLE_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "is", "are", "of", "for", "with", "to", "in",
        "on", "and", "or", "by", "via", "from", "as", "at", "we", "our",
        "deep", "neural", "learning", "models", "model", "networks",
        "network", "using", "based",
    }
)


def _title_keywords(title: str) -> list[str]:
    """Pull significant keyword tokens out of a paper title for searching.

    Drops stopwords + noise tokens that show up in nearly every ML title
    ("learning", "neural", "network", etc.) so the search query points at
    the paper's distinguishing concept rather than noise.
    """
    if not title:
        return []
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]+", title.lower())
    return [w for w in words if w not in _TITLE_STOPWORDS and len(w) >= 3]


_ACRONYM_RE = re.compile(r"\(([A-Za-z][A-Za-z0-9]{1,8})\)")


def _abstract_acronyms(abstract: str) -> list[str]:
    """Extract parenthetical acronyms from an abstract.

    Many ML papers introduce a model via ``Masked Autoencoders (MAE)``
    or ``Bidirectional Encoder Representations from Transformers (BERT)``.
    The acronym is what the official repo is typically named after, so
    using it as a search keyword often finds the canonical repo where a
    title-keyword search would miss it (``masked`` finds Mask2Former
    before it finds ``facebookresearch/mae``).
    """
    if not abstract:
        return []
    return [match.lower() for match in _ACRONYM_RE.findall(abstract) if len(match) >= 2]


def _significant_repo_tokens(repo: str) -> list[str]:
    """Tokenize a repo name into searchable parts. ``vision_transformer``
    splits to ``["vision", "transformer"]``; ``LoRA`` stays as one token."""
    if not repo:
        return []
    parts = re.findall(r"[a-zA-Z][a-zA-Z0-9]+", repo.lower())
    return [p for p in parts if len(p) >= 3]


def detect_paper_affiliations(text: str) -> list[str]:
    """Return GitHub org slugs the paper authors are affiliated with,
    based on substring matches against ``_LAB_AFFILIATIONS``. Used both
    by the verifier (as an additional evidence signal) and by the
    affiliation-based recall booster.
    """
    if not text:
        return []
    lower = text.lower()
    matched: list[str] = []
    for org, patterns in _LAB_AFFILIATIONS.items():
        for pattern in patterns:
            if pattern in lower:
                matched.append(org)
                break
    return matched


# Markers that indicate a README's main heading describes a third-party
# reimplementation rather than the paper's own code. Used by
# verify_repo_owns_paper to gate the heading-match signal — a heading
# like "Unofficial PyTorch implementation of <paper title>" otherwise
# fires the same +2 as the official repo's heading.
_UNOFFICIAL_HEADING_RE = re.compile(
    r"^\s*(?:un[\s\-]?official|re[\s\-]?implementation|third[\s\-]?party"
    r"|reimpl|reproduction|port\b|fork\b|implementation\s+of)",
    re.IGNORECASE,
)


def _first_h1(readme: str) -> str:
    """Return the first ``# `` (level-1) heading line of a README, or
    the empty string when the README has no H1. Used by the verifier's
    heading-match signal."""
    for line in readme.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return ""


def _fetch_readme(
    owner: str,
    repo: str,
    *,
    token: str | None,
    session: requests.Session | None,
    timeout: float,
) -> str | None:
    """Fetch the README text from GitHub or return None on any failure."""
    if not owner or not repo:
        return None
    client = session or SESSION
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "paperbase-code-verify",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = client.get(
            f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/readme",
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning(
            "GitHub README fetch failed",
            extra={
                "event": "code_verify.fetch_failed",
                "owner": owner,
                "repo": repo,
                "error": str(exc),
            },
        )
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    content_b64 = payload.get("content", "")
    if not content_b64:
        return None
    import base64

    try:
        return base64.b64decode(content_b64).decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover — malformed base64
        return None


def verify_repo_owns_paper(
    owner: str,
    repo: str,
    arxiv_id: str,
    *,
    title: str | None = None,
    authors: str | None = None,
    abstract: str | None = None,
    token: str | None = None,
    session: requests.Session | None = None,
    timeout: float = 10.0,
    threshold: int = 2,
) -> bool:
    """Score-based "is this repo the paper's official code?" check.

    Replaces the original binary verifier with a multi-evidence scoring
    rule so a repo whose README focuses on usage rather than citation
    (LLaMA, Llama 2) can still be accepted when other corroborating
    signals fire. Each signal contributes points; ``score >= threshold``
    accepts.

    Signals:
    - ``+3`` arxiv id verbatim or as ``arxiv.org/abs/<id>`` in README
    - ``+2`` paper title (≥ 20 chars) appears verbatim in README
    - ``+1`` repo name token appears in title (e.g. repo ``llama`` and
      title contains "LLaMA"), 4+ char tokens only to avoid noise
    - ``+1`` repo owner is a known ML lab (``_LAB_AFFILIATIONS``) AND
      that lab is detected in the paper authors / abstract
    - ``-∞`` (False) if the README request 404s — the repo doesn't
      exist (e.g. ``openai/gpt-3supplemental`` from a text-layout bug)

    The default ``threshold=2`` keeps precision high — any single weak
    signal alone (just a repo-name match) does not accept; arxiv id
    alone does (worth 3 by itself).
    """
    if not owner or not repo or not arxiv_id:
        return False
    readme = _fetch_readme(owner, repo, token=token, session=session, timeout=timeout)
    if readme is None:
        return False
    score = 0
    readme_lower = readme.lower()
    if arxiv_id in readme:
        score += 3
    elif f"arxiv.org/abs/{arxiv_id}" in readme_lower:
        score += 3
    title_in_readme = bool(
        title and len(title) >= 20 and title.lower() in readme_lower
    )
    # Repo-name affinity. The strongest version is when the repo name
    # IS the paper's primary keyword (LLaMA repo for "LLaMA: Open and
    # Efficient ...") or matches a parenthetical acronym from the
    # abstract (MAE repo for "Masked Autoencoders (MAE) Are Scalable
    # ..."). Those signals are worth +2 because repos named after the
    # paper's distinguishing concept are almost certainly the official
    # implementation. A weaker overlap-anywhere check still earns +1.
    repo_overlap_strong = False
    repo_overlap_weak = False
    if title:
        primary_keywords: list[str] = _title_keywords(title)
        acronyms = _abstract_acronyms(abstract or "")
        repo_tokens = set(_significant_repo_tokens(repo))
        # Also include the raw lowercased repo name (no token split), so
        # concatenated repo names like ``barlowtwins`` participate in
        # prefix-match against title keywords like ``barlow``.
        repo_full = repo.lower()

        def _matches(candidate: str) -> bool:
            """Match a candidate token to the repo name. Token-membership
            check fires only for single-token repos (``mae`` matches
            ``mae``); multi-token repos (``intrinsic-private-sgd``)
            require the candidate to either equal the full repo name or
            prefix-match it from the start. Without this restriction,
            generic acronyms like ``SGD`` would match any repo whose
            name happens to contain ``-sgd-`` as one of several tokens
            (the ResNet → ``microsoft/intrinsic-private-sgd`` false
            positive from the 30-paper benchmark)."""
            if not candidate or len(candidate) < 3:
                return False
            cand = candidate.lower()
            # Token-membership only when the repo is a single token —
            # the candidate IS the entire repo name.
            if len(repo_tokens) == 1 and cand in repo_tokens:
                return True
            # Full repo-name equality.
            if cand == repo_full:
                return True
            # Prefix-match either direction with a 4-char floor on the
            # shorter side so ``se`` doesn't match ``senet`` accidentally
            # but ``barlow`` matches ``barlowtwins``.
            if len(cand) >= 4 and repo_full.startswith(cand):
                return True
            if len(repo_full) >= 4 and cand.startswith(repo_full):
                return True
            return False

        primary_set = set(primary_keywords[:1]) | set(acronyms)
        repo_overlap_strong = any(_matches(t) for t in primary_set)
        repo_overlap_weak = any(
            _matches(t) for t in primary_keywords if len(t) >= 4
        )
        if repo_overlap_strong:
            score += 2
        elif repo_overlap_weak:
            score += 1
    # Title-in-README is only credited when corroborated by repo-name
    # overlap. A bare title-in-README is too generous: famous papers
    # (ResNet, BERT, GPT) get cited in countless unrelated repos'
    # READMEs, and we don't want ``microsoft/intrinsic-privacy`` to
    # accept just because its README mentions ResNet's title as a
    # baseline. Pairing with repo-name overlap separates authorship
    # ("repo IS named after this paper, AND README has the title")
    # from citation ("README mentions paper but repo is unrelated").
    if title_in_readme and (repo_overlap_strong or repo_overlap_weak):
        score += 2
    # README first-heading match. A repo's main README heading reflects
    # its identity. When the heading contains the full paper title and
    # is not marked as a third-party reimplementation, that is strong
    # standalone evidence the repo IS the paper's implementation —
    # independent of repo-name overlap. Closes the SENet
    # (``hujie-frank/senet``) recall gap: the repo name is an acronym
    # the verifier can't relate to the title tokens "Squeeze",
    # "Excitation", "Networks", but the README heading IS the title.
    # Does not re-introduce the ResNet → ``microsoft/intrinsic-priva``
    # false positive: that repo's first heading is "Introduction", not
    # the ResNet title.
    if title and len(title) >= 20:
        first_heading = _first_h1(readme)
        if first_heading and title.lower() in first_heading.lower():
            if not _UNOFFICIAL_HEADING_RE.match(first_heading):
                score += 2
    # Owner-affiliation affinity: repo owner is a known ML lab AND that
    # lab appears in the paper authors/abstract.
    if owner.lower() in _LAB_AFFILIATIONS:
        paper_text = " ".join(filter(None, (authors, abstract)))
        if owner.lower() in detect_paper_affiliations(paper_text):
            score += 1
    return score >= threshold


def find_official_repo_via_affiliation(
    arxiv_id: str,
    *,
    title: str,
    authors: str = "",
    abstract: str = "",
    token: str | None = None,
    session: requests.Session | None = None,
    timeout: float = 15.0,
) -> str | None:
    """Recall booster (#46 follow-up layer 3) for papers whose body
    doesn't link a GitHub repo. Detects the paper's lab affiliation
    from author/abstract text, searches that lab's GitHub org for a
    repo whose name matches the paper's primary keyword, and verifies
    the top hit's README references the paper.

    Returns the canonical GitHub URL or ``None`` when:
    - no affiliation could be detected
    - the search returns no candidates
    - no candidate passes ``verify_repo_owns_paper``

    Costs one GitHub Search API call per detected affiliation plus one
    README fetch per top-N search hit. Off the on-demand index path —
    only fires when the ranker has nothing.
    """
    if not arxiv_id or not title:
        return None
    paper_text = " ".join(filter(None, (authors, abstract)))
    affiliations = detect_paper_affiliations(paper_text)
    if not affiliations:
        return None
    # Search query candidates, ordered by specificity. Acronyms first
    # (e.g. ``MAE`` finds ``facebookresearch/mae`` directly where the
    # title keyword ``masked`` finds Mask2Former / Mask-Predict before
    # the actual repo). Title's first non-stopword keyword as the
    # fallback.
    queries: list[str] = []
    queries.extend(_abstract_acronyms(abstract or ""))
    keywords = _title_keywords(title)
    if keywords:
        queries.append(keywords[0])
    if not queries:
        return None
    # Dedupe while preserving order.
    seen: set[str] = set()
    unique_queries = [q for q in queries if not (q in seen or seen.add(q))]
    client = session or SESSION
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "paperbase-code-verify",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for org in affiliations:
        for query in unique_queries:
            try:
                response = client.get(
                    f"{_GITHUB_API_BASE}/search/repositories",
                    params={
                        "q": f"{query} org:{org} in:name,description",
                        "sort": "stars",
                        "order": "desc",
                        "per_page": 5,
                    },
                    headers=headers,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                logger.warning(
                    "GitHub search by affiliation failed",
                    extra={
                        "event": "code_verify.search_failed",
                        "org": org,
                        "query": query,
                        "error": str(exc),
                    },
                )
                continue
            if response.status_code != 200:
                # 403 with X-RateLimit-Remaining: 0 means we burned the
                # search-API budget (10 req/min anonymous, 30 with a
                # token). Surface this distinctly so the benchmark and
                # operators can tell rate-limit misses from genuine
                # "no matching repo" misses.
                rate_remaining = response.headers.get("X-RateLimit-Remaining")
                rate_reset = response.headers.get("X-RateLimit-Reset")
                logger.warning(
                    "GitHub search by affiliation returned non-200",
                    extra={
                        "event": "code_verify.search_non_200",
                        "org": org,
                        "query": query,
                        "status": response.status_code,
                        "rate_remaining": rate_remaining,
                        "rate_reset": rate_reset,
                    },
                )
                continue
            try:
                items = response.json().get("items", [])[:5]
            except ValueError:
                continue
            for item in items:
                owner = item.get("owner", {}).get("login")
                repo = item.get("name")
                if not owner or not repo:
                    continue
                # Affiliation search casts a wide net — any repo at the
                # lab matching a keyword. Require ``threshold=4`` so
                # the weak combo "primary-keyword prefix-match (+2) +
                # affiliation (+1)" cannot accept on its own. With a
                # GitHub token raising the rate-limit ceiling, this
                # combo started firing on the BatchNorm →
                # ``google-research/batch-ppo`` and ResNet →
                # ``microsoft/ResiDual`` cases — both are different
                # papers from the same lab whose names happen to
                # share a generic ML word with the searched title.
                # Threshold 4 needs at least one strong, paper-
                # specific signal: arxiv id in README (+3, the case
                # that recovers MAE), or title-in-README corroborated
                # by repo-name overlap (+2 + +2), or first-heading
                # match (+2) on top of the keyword overlap.
                if verify_repo_owns_paper(
                    owner,
                    repo,
                    arxiv_id,
                    title=title,
                    authors=authors,
                    abstract=abstract,
                    token=token,
                    session=session,
                    timeout=timeout,
                    threshold=4,
                ):
                    return f"https://github.com/{owner}/{repo}"
    return None
