"""Data source clients for arXiv, Semantic Scholar, and Papers With Code."""

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

ARXIV_API = "http://export.arxiv.org/api/query"
S2_API = "https://api.semanticscholar.org/graph/v1"
PWC_API = "https://paperswithcode.com/api/v1"

AI_CATEGORIES = [
    "cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.NE", "cs.MA", "cs.RO",
    "stat.ML", "cs.IR", "cs.SD", "eess.AS", "cs.HC", "cs.CR",
]

ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ai-paperclip/0.1.0 (research tool)"})


def _extract_arxiv_id(entry):
    raw = entry.findtext("atom:id", "", ARXIV_NS)
    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?$", raw)
    return m.group(1) if m else raw.split("/abs/")[-1]


def _parse_arxiv_entry(entry) -> dict:
    authors = [a.findtext("atom:name", "", ARXIV_NS) for a in entry.findall("atom:author", ARXIV_NS)]
    categories = [c.get("term", "") for c in entry.findall("atom:category", ARXIV_NS)]
    primary = entry.find("arxiv:primary_category", ARXIV_NS)
    links = {l.get("title", "alternate"): l.get("href", "") for l in entry.findall("atom:link", ARXIV_NS)}

    return {
        "arxiv_id": _extract_arxiv_id(entry),
        "title": re.sub(r"\s+", " ", entry.findtext("atom:title", "", ARXIV_NS)).strip(),
        "authors": ", ".join(authors),
        "abstract": re.sub(r"\s+", " ", entry.findtext("atom:summary", "", ARXIV_NS)).strip(),
        "categories": ", ".join(categories),
        "primary_category": primary.get("term", "") if primary is not None else (categories[0] if categories else ""),
        "published": entry.findtext("atom:published", "", ARXIV_NS)[:10],
        "updated": entry.findtext("atom:updated", "", ARXIV_NS)[:10],
        "doi": entry.findtext("arxiv:doi", None, ARXIV_NS),
        "comment": entry.findtext("arxiv:comment", None, ARXIV_NS),
        "journal_ref": entry.findtext("arxiv:journal_ref", None, ARXIV_NS),
        "pdf_url": links.get("pdf", ""),
        "semantic_scholar_id": None,
        "citation_count": 0,
        "influential_citation_count": 0,
        "code_url": None,
    }


def search_arxiv(query: str, max_results: int = 50, category: str = None,
                 sort_by: str = "relevance", start: int = 0) -> list[dict]:
    search_query = query
    if category:
        search_query = f"cat:{category} AND ({query})"
    elif not any(f"cat:{c}" in query for c in AI_CATEGORIES):
        cat_filter = " OR ".join(f"cat:{c}" for c in AI_CATEGORIES[:7])
        search_query = f"({cat_filter}) AND ({query})"

    sort_map = {"relevance": "relevance", "date": "submittedDate", "citations": "relevance"}
    params = {
        "search_query": search_query,
        "start": start,
        "max_results": min(max_results, 200),
        "sortBy": sort_map.get(sort_by, "relevance"),
        "sortOrder": "descending",
    }
    resp = SESSION.get(ARXIV_API, params=params, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    entries = root.findall("atom:entry", ARXIV_NS)
    return [_parse_arxiv_entry(e) for e in entries]


def fetch_recent(category: str = "cs.AI", days: int = 7, max_results: int = 100) -> list[dict]:
    query = f"cat:{category}"
    params = {
        "search_query": query,
        "start": 0,
        "max_results": min(max_results, 200),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    resp = SESSION.get(ARXIV_API, params=params, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    entries = root.findall("atom:entry", ARXIV_NS)
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    papers = []
    for e in entries:
        p = _parse_arxiv_entry(e)
        if p["published"] >= cutoff:
            papers.append(p)
    return papers


def enrich_with_s2(papers: list[dict], batch_size: int = 20) -> list[dict]:
    """Enrich papers with Semantic Scholar citation data."""
    for i in range(0, len(papers), batch_size):
        batch = papers[i:i + batch_size]
        ids = [f"ArXiv:{p['arxiv_id']}" for p in batch]
        try:
            resp = SESSION.post(
                f"{S2_API}/paper/batch",
                json={"ids": ids},
                params={"fields": "citationCount,influentialCitationCount,externalIds,tldr"},
                timeout=15,
            )
            if resp.status_code == 200:
                results = resp.json()
                for paper, s2 in zip(batch, results):
                    if s2:
                        paper["citation_count"] = s2.get("citationCount", 0) or 0
                        paper["influential_citation_count"] = s2.get("influentialCitationCount", 0) or 0
                        paper["semantic_scholar_id"] = s2.get("paperId")
            elif resp.status_code == 429:
                time.sleep(2)
        except requests.RequestException:
            pass
        if i + batch_size < len(papers):
            time.sleep(1)
    return papers


def search_s2(query: str, limit: int = 30, year: str = None, fields_of_study: list[str] = None) -> list[dict]:
    """Search Semantic Scholar directly."""
    params = {
        "query": query,
        "limit": min(limit, 100),
        "fields": "externalIds,title,abstract,authors,year,citationCount,influentialCitationCount,tldr,publicationDate",
    }
    if year:
        params["year"] = year
    if fields_of_study:
        params["fieldsOfStudy"] = ",".join(fields_of_study)

    resp = SESSION.get(f"{S2_API}/paper/search", params=params, timeout=15)
    if resp.status_code != 200:
        return []

    papers = []
    for item in resp.json().get("data", []):
        ext = item.get("externalIds", {}) or {}
        arxiv_id = ext.get("ArXiv", "")
        if not arxiv_id:
            continue
        authors = ", ".join(a.get("name", "") for a in (item.get("authors") or []))
        tldr = item.get("tldr")
        abstract = (tldr.get("text", "") if tldr else "") or (item.get("abstract") or "")
        papers.append({
            "arxiv_id": arxiv_id,
            "title": item.get("title", ""),
            "authors": authors,
            "abstract": abstract,
            "categories": "",
            "primary_category": "",
            "published": (item.get("publicationDate") or "")[:10],
            "updated": "",
            "doi": ext.get("DOI"),
            "comment": None,
            "journal_ref": None,
            "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else "",
            "semantic_scholar_id": item.get("paperId"),
            "citation_count": item.get("citationCount", 0) or 0,
            "influential_citation_count": item.get("influentialCitationCount", 0) or 0,
            "code_url": None,
        })
    return papers


def get_paper_code(arxiv_id: str) -> str | None:
    """Check Papers With Code for associated repository."""
    try:
        resp = SESSION.get(f"{PWC_API}/papers", params={"arxiv_id": arxiv_id}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results:
                paper_id = results[0].get("id")
                if paper_id:
                    repo_resp = SESSION.get(f"{PWC_API}/papers/{paper_id}/repositories/", timeout=10)
                    if repo_resp.status_code == 200:
                        repos = repo_resp.json().get("results", [])
                        if repos:
                            official = [r for r in repos if r.get("is_official")]
                            chosen = official[0] if official else repos[0]
                            return chosen.get("url")
    except requests.RequestException:
        pass
    return None


def get_paper_details(arxiv_id: str) -> dict | None:
    """Get full details for a single paper from arXiv."""
    params = {"id_list": arxiv_id, "max_results": 1}
    resp = SESSION.get(ARXIV_API, params=params, timeout=15)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    entries = root.findall("atom:entry", ARXIV_NS)
    if not entries:
        return None
    return _parse_arxiv_entry(entries[0])
