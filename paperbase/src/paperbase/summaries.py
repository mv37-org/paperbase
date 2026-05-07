"""Summary helpers for indexed papers and search."""

from __future__ import annotations

import logging
import re

from .config import Settings
from .extractors import ExtractionOutput
from .models import PaperMetadata


logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE = re.compile(r"\s+")
_HEADING_LINE = re.compile(r"^#+\s+.*$", re.MULTILINE)

def _normalize_text(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _clean_markdown_text(text: str) -> str:
    text = _HEADING_LINE.sub(" ", text)
    return _normalize_text(text)


def _split_sentences(text: str) -> list[str]:
    cleaned = _normalize_text(text)
    if not cleaned:
        return []
    return [sentence.strip() for sentence in _SENTENCE_SPLIT.split(cleaned) if sentence.strip()]


def _first_sentences(text: str, count: int) -> list[str]:
    return _split_sentences(text)[:count]


def _top_section_titles(extraction: ExtractionOutput, *, limit: int = 4) -> list[str]:
    ignored = {"abstract", "references", "appendix"}
    titles: list[str] = []
    for section in extraction.sections:
        title = _normalize_text(section.title)
        if not title or title.lower() in ignored or title in titles:
            continue
        titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def _build_technical_summary(paper: PaperMetadata, extraction: ExtractionOutput) -> str:
    if paper.tldr:
        return paper.tldr.strip()
    primary_sentences = _first_sentences(paper.abstract, 2)
    if not primary_sentences:
        primary_sentences = _first_sentences(_clean_markdown_text(extraction.text_markdown), 2)

    parts = primary_sentences[:]
    section_titles = _top_section_titles(extraction, limit=3)
    if section_titles:
        parts.append(f"Key sections cover {', '.join(section_titles)}.")
    elif extraction.figures or extraction.tables:
        parts.append(
            f"The extraction captured {len(extraction.figures)} figures and {len(extraction.tables)} tables."
        )

    if not parts:
        parts.append(f"{paper.title or paper.arxiv_id} is an indexed AI research paper.")
    return " ".join(parts).strip()


def build_paper_summary(
    paper: PaperMetadata,
    extraction: ExtractionOutput,
    settings: Settings | None = None,
) -> str:
    del settings
    return _build_technical_summary(paper, extraction)


def expand_query_with_gemini(query: str, settings: Settings, *, variants: int = 4) -> list[str]:
    """Ask Gemini to rewrite the query into diverse retrieval variants.

    Returns [] on any failure (import, unconfigured project, API error) so the
    caller can transparently fall back to the original query. Trivially short
    queries are returned unchanged since rewriting is wasteful for keyword hits.
    """
    query = (query or "").strip()
    if not query:
        return []
    if settings.query_expansion_provider != "vertex" or not settings.vertex_project:
        return []
    if len(query.split()) < 3:
        return [query]
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        return []
    variants = max(1, min(variants, 8))
    prompt = (
        "You rewrite research questions into diverse search queries for an AI "
        "paper retrieval system.\n\n"
        f"Produce up to {variants} DIFFERENT short queries (max ~10 words each) "
        "that together cover the question from complementary angles (technical "
        "terms, method names, synonymous phrasings, narrower/broader scopes).\n"
        "Return ONLY the queries, one per line, no numbering, no commentary.\n\n"
        f"Question: {query}\n"
    )
    try:
        client = genai.Client(
            vertexai=True,
            project=settings.vertex_project,
            location=settings.vertex_location,
        )
        response = client.models.generate_content(
            model=settings.query_expansion_model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=400,
                temperature=0.4,
            ),
        )
        text = (response.text or "").strip()
    except Exception as exc:
        logger.warning(
            "Vertex query expansion failed",
            extra={"event": "query_expand.failed", "error": str(exc)},
        )
        return []
    expanded: list[str] = []
    seen: set[str] = {query.lower()}
    for line in text.splitlines():
        candidate = line.strip().lstrip("-*0123456789. ").strip()
        if not candidate:
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        expanded.append(candidate)
        if len(expanded) >= variants:
            break
    # Always keep the original query at position 0.
    return [query, *expanded]
