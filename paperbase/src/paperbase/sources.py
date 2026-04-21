"""Remote source clients for arXiv and Semantic Scholar."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import requests

from .config import DEFAULT_AI_CATEGORIES
from .models import PaperMetadata
from .utils import canonical_arxiv_id


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


def get_paper_details(arxiv_id: str) -> PaperMetadata | None:
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


def enrich_with_semantic_scholar(papers: list[PaperMetadata], *, batch_size: int = 20) -> list[PaperMetadata]:
    for offset in range(0, len(papers), batch_size):
        batch = papers[offset: offset + batch_size]
        ids = [f"ArXiv:{paper.arxiv_id}" for paper in batch]
        try:
            response = SESSION.post(
                f"{S2_API}/paper/batch",
                json={"ids": ids},
                params={"fields": "citationCount,influentialCitationCount,externalIds,tldr"},
                timeout=15,
            )
            if response.status_code == 429:
                time.sleep(2)
                continue
            response.raise_for_status()
            for paper, item in zip(batch, response.json()):
                if not item:
                    continue
                paper.citation_count = item.get("citationCount", 0) or 0
                paper.influential_citation_count = item.get("influentialCitationCount", 0) or 0
                paper.semantic_scholar_id = item.get("paperId")
                tldr_payload = item.get("tldr") or {}
                tldr_text = tldr_payload.get("text") if isinstance(tldr_payload, dict) else None
                if tldr_text:
                    paper.tldr = tldr_text
        except requests.RequestException:
            continue
        if offset + batch_size < len(papers):
            time.sleep(1)
    return papers


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


def get_related_papers(arxiv_id: str, semantic_scholar_id: str | None, *, limit: int = 10) -> list[PaperMetadata]:
    resolved_s2_id = semantic_scholar_id
    if not resolved_s2_id:
        enriched = enrich_with_semantic_scholar([PaperMetadata(arxiv_id=arxiv_id)])
        resolved_s2_id = enriched[0].semantic_scholar_id
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
