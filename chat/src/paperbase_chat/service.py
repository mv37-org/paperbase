"""Thin service layer that grounds chat replies in indexed paperbase artifacts."""

from __future__ import annotations

import re
from functools import lru_cache

from .client import PaperbaseChatClient
from .config import ChatSettings, get_settings
from .models import ChatRequest, ChatResponse, EvidenceItem, PaperCard


QUESTION_PREFIXES = (
    "what do you know about",
    "tell me about",
    "what is",
    "what are",
    "why is",
    "why does",
    "how do",
    "how does",
    "how is",
    "explain",
    "summarize",
)

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "do",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "tell",
    "the",
    "this",
    "to",
    "what",
    "why",
    "with",
}


def _normalize_query(text: str) -> str:
    cleaned = text.strip().lower()
    cleaned = re.sub(r"[^\w\s-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for prefix in QUESTION_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned.removeprefix(prefix).strip()
            break
    return cleaned


def _keyword_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for token in _normalize_query(text).split():
        if token in STOPWORDS or len(token) <= 2:
            continue
        tokens.append(token)
    return tokens


def _rewrite_queries(question: str, history: list[str], *, max_queries: int = 4) -> list[str]:
    variants: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            return
        lowered = value.lower()
        if lowered not in seen:
            seen.add(lowered)
            variants.append(value)

    add(question)
    add(_normalize_query(question))
    keywords = _keyword_tokens(question)
    if keywords:
        add(" ".join(keywords[:6]))
    if history:
        recent_context = " ".join(history[-2:])
        add(f"{recent_context} {question}")
    return variants[:max_queries]


def _split_sentences(text: str) -> list[str]:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed:
        return []
    return [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", collapsed) if segment.strip()]


def _best_excerpt(text: str, question: str, *, max_sentences: int = 2, max_chars: int = 360) -> str:
    sentences = _split_sentences(text)
    if not sentences:
        collapsed = re.sub(r"\s+", " ", text).strip()
        return collapsed[:max_chars].rstrip()

    keywords = set(_keyword_tokens(question))
    scored: list[tuple[int, int, str]] = []
    for index, sentence in enumerate(sentences):
        sentence_tokens = set(_keyword_tokens(sentence))
        overlap = len(keywords & sentence_tokens)
        position_bonus = max(0, 3 - index)
        scored.append((overlap * 10 + position_bonus, index, sentence))

    chosen = sorted(scored, key=lambda item: (item[0], -item[1]), reverse=True)[:max_sentences]
    ordered = [sentence for _, _, sentence in sorted(chosen, key=lambda item: item[1])]
    excerpt = " ".join(ordered).strip()
    return excerpt[:max_chars].rstrip()


def _build_answer(paper: PaperCard, question: str, evidence: list[EvidenceItem]) -> str:
    if not evidence:
        fallback = paper.summary_short or paper.abstract
        if fallback:
            return (
                f"I did not find strong section-level matches for '{question}'. "
                f"The best fallback summary for {paper.title} is: {fallback}"
            )
        return f"I did not find grounded evidence inside {paper.title} for '{question}'."

    lines = [f"Best evidence from {paper.title} for '{question}':"]
    for item in evidence[:3]:
        lines.append(f"- {item.excerpt}")
    lines.append("")
    lines.append("Sections used:")
    for item in evidence[:3]:
        lines.append(f"- {item.path}")
    return "\n".join(lines)


class ChatService:
    def __init__(self, settings: ChatSettings | None = None, client: PaperbaseChatClient | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client or PaperbaseChatClient(self._settings)

    def search_papers(self, query: str) -> list[PaperCard]:
        query = query.strip()
        if not query:
            return []
        response = self._client.search_papers(
            query=query,
            limit=self._settings.search_limit,
            include_live_sources=False,
        )
        papers = [
            PaperCard.model_validate(paper)
            for paper in response.get("papers", [])
            if paper.get("index_status") == "indexed"
        ]
        return papers

    def get_paper(self, paper_id: str) -> PaperCard:
        return PaperCard.model_validate(self._client.get_paper(paper_id))

    def answer(self, request: ChatRequest) -> ChatResponse:
        paper = self.get_paper(request.paper_id)
        history = [turn.content for turn in request.history if turn.role == "user"]
        queries = _rewrite_queries(request.question, history)

        aggregated: dict[str, dict[str, object]] = {}
        for index, query in enumerate(queries):
            response = self._client.search_sections(
                query=query,
                limit=self._settings.section_limit,
                paper_ids=[request.paper_id],
            )
            weight = 1.0 / (index + 1)
            for hit in response.get("sections", []):
                path = str(hit["path"])
                item = aggregated.setdefault(
                    path,
                    {
                        "path": path,
                        "chunk_kind": str(hit.get("chunk_kind", "section")),
                        "score": 0.0,
                        "matching_queries": [],
                    },
                )
                item["score"] = float(item["score"]) + float(hit.get("score") or 0.0) * weight
                matching_queries = item["matching_queries"]
                if isinstance(matching_queries, list) and query not in matching_queries:
                    matching_queries.append(query)

        ranked_hits = sorted(
            aggregated.values(),
            key=lambda item: float(item["score"]),
            reverse=True,
        )

        evidence: list[EvidenceItem] = []
        seen_excerpts: set[str] = set()
        for hit in ranked_hits:
            if len(evidence) >= self._settings.evidence_limit:
                break
            path = str(hit["path"])
            if not path.endswith((".md", ".txt")):
                continue
            artifact = self._client.read_paper_file(request.paper_id, path)
            excerpt = _best_excerpt(str(artifact.get("content", "")), request.question)
            normalized_excerpt = excerpt.lower()
            if not excerpt or normalized_excerpt in seen_excerpts:
                continue
            seen_excerpts.add(normalized_excerpt)
            evidence.append(
                EvidenceItem(
                    path=path,
                    chunk_kind=str(hit["chunk_kind"]),
                    score=float(hit["score"]),
                    excerpt=excerpt,
                    matching_queries=list(hit["matching_queries"]),
                )
            )

        return ChatResponse(
            paper=paper,
            answer=_build_answer(paper, request.question, evidence),
            evidence=evidence,
            rewritten_queries=queries,
        )


@lru_cache(maxsize=1)
def get_chat_service() -> ChatService:
    return ChatService()
