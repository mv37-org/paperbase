"""Deterministic research-QA orchestration over indexed paperbase content."""

from __future__ import annotations

import re
from typing import Any

from .client import PaperbaseClient


QUESTION_PREFIXES = (
    "what do you know about",
    "tell me about",
    "what is",
    "what are",
    "how do you",
    "how can i",
    "how should i",
    "explain",
    "summarize",
)

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "about",
    "as",
    "at",
    "be",
    "build",
    "building",
    "can",
    "do",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "should",
    "the",
    "to",
    "what",
    "with",
    "you",
}

QUERY_EXPANSIONS = {
    "agent": ["ai agent", "llm agent", "tool use planning acting", "reasoning and acting"],
    "agents": ["ai agent", "llm agent", "tool use planning acting", "reasoning and acting"],
    "tool": ["tool use", "function calling"],
    "tools": ["tool use", "function calling"],
    "planning": ["planning action selection", "task decomposition"],
    "reasoning": ["reasoning acting", "chain of thought planning"],
    "memory": ["long term memory retrieval", "agent memory"],
    "retrieval": ["retrieval augmented generation", "knowledge retrieval"],
    "web": ["web agent", "browser agent"],
    "coding": ["coding agent", "software engineering agent"],
    "multimodal": ["vision language model", "multimodal agent"],
    "evaluation": ["agent benchmark", "benchmark evaluation"],
}

MAX_EVIDENCE_CHARS = 4000


def _normalize_query(question: str) -> str:
    cleaned = question.strip().lower()
    cleaned = re.sub(r"[^\w\s-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for prefix in QUESTION_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned.removeprefix(prefix).strip()
            break
    return cleaned


def _keyword_tokens(question: str) -> list[str]:
    normalized = _normalize_query(question)
    tokens = []
    for token in normalized.split():
        if token in STOPWORDS:
            continue
        if len(token) <= 2:
            continue
        tokens.append(token)
    return tokens


def rewrite_queries(question: str, *, max_queries: int = 6) -> list[str]:
    variants: list[str] = []
    seen: set[str] = set()
    normalized = _normalize_query(question)
    keywords = _keyword_tokens(question)

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            return
        lowered = value.lower()
        if lowered not in seen:
            seen.add(lowered)
            variants.append(value)

    add(question.strip())
    add(normalized)
    if keywords:
        add(" ".join(keywords[:6]))
    if len(keywords) >= 2:
        add(" ".join(keywords[:4]))
    for token in keywords:
        for expansion in QUERY_EXPANSIONS.get(token, []):
            if keywords and keywords[0] not in expansion:
                add(f"{keywords[0]} {expansion}")
            add(expansion)
            if len(variants) >= max_queries:
                return variants[:max_queries]
    return variants[:max_queries]


def _first_sentence(text: str, *, max_chars: int = 240) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed:
        return ""
    match = re.search(r"(.+?[.!?])(\s|$)", collapsed)
    sentence = match.group(1) if match else collapsed
    return sentence[:max_chars].rstrip()


def _aggregate_papers(
    client: PaperbaseClient,
    queries: list[str],
    *,
    paper_limit: int,
    include_live_sources: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    aggregated: dict[str, dict[str, Any]] = {}
    query_runs: list[dict[str, Any]] = []
    search_limit = max(paper_limit * 2, paper_limit)
    for index, query in enumerate(queries):
        response = client.search_papers(
            query=query,
            limit=search_limit,
            include_live_sources=include_live_sources,
        )
        papers = response.get("papers", [])
        query_runs.append(
            {
                "query": query,
                "result_set_id": response.get("result_set_id"),
                "paper_hits": len(papers),
            }
        )
        weight = 1.0 / (index + 1)
        for rank, paper in enumerate(papers):
            paper_id = paper["paper_id"]
            rank_bonus = max(search_limit - rank, 1) / search_limit
            base_score = float(paper.get("score") or 0.0) + rank_bonus
            citation_bonus = min(int(paper.get("citation_count") or 0), 20000) / 20000
            item = aggregated.setdefault(
                paper_id,
                {
                    "paper_id": paper_id,
                    "arxiv_id": paper.get("arxiv_id", ""),
                    "title": paper.get("title", ""),
                    "summary_short": paper.get("summary_short", ""),
                    "summary_eli5": paper.get("summary_eli5", ""),
                    "published": paper.get("published", ""),
                    "citation_count": int(paper.get("citation_count") or 0),
                    "matching_queries": [],
                    "aggregate_score": 0.0,
                },
            )
            item["aggregate_score"] += (base_score + citation_bonus) * weight
            if query not in item["matching_queries"]:
                item["matching_queries"].append(query)
    ordered = sorted(
        aggregated.values(),
        key=lambda item: (item["aggregate_score"], item["citation_count"], item["published"]),
        reverse=True,
    )
    return ordered[:paper_limit], query_runs


def _aggregate_sections(
    client: PaperbaseClient,
    queries: list[str],
    *,
    paper_ids: list[str],
    section_limit: int,
    query_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    aggregated: dict[tuple[str, str], dict[str, Any]] = {}
    for index, (query_run, query) in enumerate(zip(query_runs, queries, strict=False)):
        response = client.search_sections(
            query=query,
            limit=section_limit,
            paper_ids=paper_ids,
        )
        sections = response.get("sections", [])
        query_run["section_hits"] = len(sections)
        weight = 1.0 / (index + 1)
        for hit in sections:
            key = (hit["paper_id"], hit["path"])
            item = aggregated.setdefault(
                key,
                {
                    "paper_id": hit["paper_id"],
                    "arxiv_id": hit.get("arxiv_id", ""),
                    "title": hit.get("title", ""),
                    "path": hit["path"],
                    "chunk_kind": hit.get("chunk_kind", "section"),
                    "preview": hit.get("preview", ""),
                    "matching_queries": [],
                    "score": 0.0,
                },
            )
            item["score"] += float(hit.get("score") or 0.0) * weight
            if query not in item["matching_queries"]:
                item["matching_queries"].append(query)
    ordered = sorted(aggregated.values(), key=lambda item: item["score"], reverse=True)
    return ordered[:section_limit]


def _build_evidence(client: PaperbaseClient, section_hits: list[dict[str, Any]], *, evidence_limit: int) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for hit in section_hits:
        if len(evidence) >= evidence_limit:
            break
        if not (hit["path"].endswith(".md") or hit["path"].endswith(".txt")):
            continue
        artifact = client.read_paper_file(hit["paper_id"], hit["path"])
        content = artifact.get("content", "")
        truncated = len(content) > MAX_EVIDENCE_CHARS
        evidence.append(
            {
                "paper_id": hit["paper_id"],
                "title": hit["title"],
                "path": hit["path"],
                "chunk_kind": hit["chunk_kind"],
                "matching_queries": hit["matching_queries"],
                "content": content[:MAX_EVIDENCE_CHARS].rstrip(),
                "truncated": truncated,
            }
        )
    if evidence:
        return evidence
    for hit in section_hits[:evidence_limit]:
        evidence.append(
            {
                "paper_id": hit["paper_id"],
                "title": hit["title"],
                "path": hit["path"],
                "chunk_kind": hit["chunk_kind"],
                "matching_queries": hit["matching_queries"],
                "content": hit["preview"],
                "truncated": False,
            }
        )
    return evidence


def _build_answer(
    question: str,
    shortlisted_papers: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> str:
    if not shortlisted_papers:
        return f"Paperbase did not find indexed papers that look relevant to: {question}"

    lead_titles = ", ".join(paper["title"] for paper in shortlisted_papers[:3])
    lines = [
        f"Paperbase shortlisted {len(shortlisted_papers)} indexed papers for: {question}",
        f"Strongest paper matches: {lead_titles}.",
    ]
    if evidence:
        lines.append("Most relevant evidence:")
        for item in evidence[:3]:
            snippet = _first_sentence(item["content"])
            lines.append(f"- {item['title']} [{item['path']}]: {snippet}")
    else:
        lines.append("No section-level evidence was retrieved, so the shortlist is based on title, abstract, and summary ranking only.")
    return "\n".join(lines)


def run_research_qa(
    client: PaperbaseClient,
    *,
    question: str,
    query_limit: int = 6,
    paper_limit: int = 8,
    section_limit: int = 12,
    evidence_limit: int = 5,
    include_live_sources: bool = False,
) -> dict[str, Any]:
    # Prefer server-side Gemini-generated variants (covers arbitrary domains).
    # A truthy server response is authoritative — including a single-item
    # [question] list, which the server returns intentionally for short
    # queries. Fall back to the heuristic rewriter only when the server
    # actually declined (empty response / transport failure).
    server_variants = client.expand_query(question, variants=query_limit)
    if server_variants:
        rewritten_queries = server_variants[:query_limit]
    else:
        rewritten_queries = rewrite_queries(question, max_queries=query_limit)
    shortlisted_papers, query_runs = _aggregate_papers(
        client,
        rewritten_queries,
        paper_limit=paper_limit,
        include_live_sources=include_live_sources,
    )
    paper_ids = [paper["paper_id"] for paper in shortlisted_papers]
    section_hits = _aggregate_sections(
        client,
        rewritten_queries,
        paper_ids=paper_ids,
        section_limit=section_limit,
        query_runs=query_runs,
    ) if paper_ids else []
    evidence = _build_evidence(client, section_hits, evidence_limit=evidence_limit)
    return {
        "question": question,
        "rewritten_queries": rewritten_queries,
        "query_runs": query_runs,
        "shortlisted_papers": shortlisted_papers,
        "relevant_sections": section_hits,
        "evidence": evidence,
        "answer": _build_answer(question, shortlisted_papers, evidence),
    }
