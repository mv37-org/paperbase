"""Summary builders for indexed papers."""

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

_ELI5_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("sequence transduction", "turning one sequence into another"),
    ("encoder-decoder", "input-output"),
    ("attention mechanism", "way of deciding what to focus on"),
    ("attention mechanisms", "ways of deciding what to focus on"),
    ("self-attention", "a way for a model to compare parts of the input to each other"),
    ("parallelizable", "able to run many parts at the same time"),
    ("state-of-the-art", "best so far"),
    ("architecture", "design"),
    ("neural network", "AI model"),
    ("neural networks", "AI models"),
    ("benchmark", "test"),
    ("benchmarks", "tests"),
)


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


def _simplify_text(text: str) -> str:
    simplified = _normalize_text(text)
    for source, target in _ELI5_REPLACEMENTS:
        simplified = re.sub(rf"\b{re.escape(source)}\b", target, simplified, flags=re.IGNORECASE)
    simplified = simplified.replace(" i.e. ", " that is, ")
    simplified = simplified.replace(" e.g. ", " for example ")
    return simplified


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


def _build_eli5_summary(paper: PaperMetadata, extraction: ExtractionOutput, technical_summary: str) -> str:
    source_sentences = _first_sentences(paper.abstract, 2)
    if not source_sentences:
        source_sentences = _first_sentences(_clean_markdown_text(extraction.text_markdown), 2)
    if not source_sentences:
        source_sentences = _first_sentences(technical_summary, 2)

    simple_parts = [_simplify_text(sentence) for sentence in source_sentences[:2]]
    section_titles = _top_section_titles(extraction, limit=3)
    if section_titles:
        simple_parts.append(f"It mainly talks about {', '.join(section_titles)}.")

    body = " ".join(part for part in simple_parts if part).strip()
    if not body:
        body = f"{paper.title or paper.arxiv_id} explains an AI idea in a simpler way."
    if not body.lower().startswith("in simple terms"):
        body = f"In simple terms, {body[0].lower() + body[1:] if len(body) > 1 else body.lower()}"
    return body


def _heuristic_summaries(paper: PaperMetadata, extraction: ExtractionOutput) -> tuple[str, str]:
    technical_summary = _build_technical_summary(paper, extraction)
    eli5_summary = _build_eli5_summary(paper, extraction, technical_summary)
    return technical_summary, eli5_summary


def build_summary_prompt(paper: PaperMetadata, *, context_override: str | None = None, section_titles: str = "") -> str:
    context = context_override or paper.tldr or paper.abstract or ""
    context = context[:4000]
    return (
        "You summarize AI/ML research papers. Given the paper metadata below, "
        "produce two summaries.\n\n"
        "Output EXACTLY two lines, each prefixed with a label:\n"
        "  TECH: <one-to-two-sentence technical summary, faithful to the abstract>\n"
        "  ELI5: <one-to-two-sentence plain-language summary an undergraduate could read>\n"
        "No extra commentary.\n\n"
        f"Title: {paper.title}\n"
        f"Sections: {section_titles}\n"
        f"Abstract/TLDR:\n{context}\n"
    )


def parse_summary_response(text: str) -> tuple[str, str] | None:
    tech_line = ""
    eli5_line = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("TECH:"):
            tech_line = stripped.split(":", 1)[1].strip()
        elif stripped.upper().startswith("ELI5:"):
            eli5_line = stripped.split(":", 1)[1].strip()
    if not tech_line or not eli5_line:
        return None
    return tech_line, eli5_line


def _vertex_summaries(
    paper: PaperMetadata,
    extraction: ExtractionOutput | None,
    settings: Settings,
) -> tuple[str, str] | None:
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        return None
    if not settings.vertex_project:
        return None

    section_titles = ", ".join(_top_section_titles(extraction, limit=5)) if extraction else ""
    context_override = None
    if extraction is not None and not paper.tldr and not paper.abstract:
        context_override = _clean_markdown_text(extraction.text_markdown)
    prompt = build_summary_prompt(paper, context_override=context_override, section_titles=section_titles)

    try:
        client = genai.Client(
            vertexai=True,
            project=settings.vertex_project,
            location=settings.vertex_location,
        )
        response = client.models.generate_content(
            model=settings.summary_model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=400,
                temperature=0.2,
            ),
        )
        text = (response.text or "").strip()
    except Exception as exc:
        logger.warning(
            "Vertex summary failed, falling back to heuristic",
            extra={"event": "summary.vertex_failed", "error": str(exc)},
        )
        return None

    return parse_summary_response(text)


def build_paper_summaries(
    paper: PaperMetadata,
    extraction: ExtractionOutput,
    settings: Settings | None = None,
) -> tuple[str, str]:
    if settings is not None and settings.summary_provider == "vertex":
        llm = _vertex_summaries(paper, extraction, settings)
        if llm is not None:
            return llm
    if settings is not None and settings.summary_provider == "none":
        # Skip any heuristic elaboration too — use tldr or first abstract sentence.
        first_sentence = paper.abstract.split(". ", 1)[0] if paper.abstract else ""
        tech = paper.tldr or first_sentence
        return tech, ""
    return _heuristic_summaries(paper, extraction)


def generate_summary_on_demand(paper: PaperMetadata, settings: Settings) -> tuple[str, str] | None:
    """Single-paper online Gemini call for lazy summarization in get_paper."""
    return _vertex_summaries(paper, None, settings)
