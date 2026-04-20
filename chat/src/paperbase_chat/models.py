"""Pydantic models for the chat app."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PaperCard(BaseModel):
    paper_id: str
    arxiv_id: str = ""
    title: str = ""
    authors: str = ""
    abstract: str = ""
    summary_short: str = ""
    summary_eli5: str = ""
    index_status: str = "unknown"
    published: str = ""
    citation_count: int = 0


class PaperSearchRequest(BaseModel):
    query: str = Field(min_length=1)


class PaperSearchResponse(BaseModel):
    papers: list[PaperCard]


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    paper_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    history: list[ChatTurn] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    path: str
    chunk_kind: str
    score: float
    excerpt: str
    matching_queries: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    paper: PaperCard
    answer: str
    evidence: list[EvidenceItem]
    rewritten_queries: list[str] = Field(default_factory=list)
