"""Metadata, result-set, and job persistence for paperbase."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import Settings
from .models import (
    GrepPaperMatch,
    IntegrationProbeComponent,
    JobResponse,
    PaperMetadata,
    PaperResponse,
    ResultSetResponse,
    SearchPaperResult,
    SearchSectionResult,
)
from .utils import canonical_arxiv_id, canonical_paper_id, literal_hint_from_pattern, new_id, utcnow


class Base(DeclarativeBase):
    pass


class PaperORM(Base):
    __tablename__ = "papers"

    paper_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    arxiv_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    authors: Mapped[str] = mapped_column(Text, default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    summary_short: Mapped[str] = mapped_column(Text, default="")
    summary_eli5: Mapped[str] = mapped_column(Text, default="")
    categories: Mapped[str] = mapped_column(Text, default="")
    primary_category: Mapped[str] = mapped_column(String(64), default="")
    published: Mapped[str] = mapped_column(String(32), default="")
    updated: Mapped[str] = mapped_column(String(32), default="")
    doi: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    journal_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_url: Mapped[str] = mapped_column(Text, default="")
    html_url: Mapped[str] = mapped_column(Text, default="")
    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    influential_citation_count: Mapped[int] = mapped_column(Integer, default=0)
    semantic_scholar_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    code_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    index_status: Mapped[str] = mapped_column(String(32), default="discovered")
    search_document: Mapped[str] = mapped_column(Text, default="")
    tldr: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    indexed_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    chunks: Mapped[list["TextChunkORM"]] = relationship(back_populates="paper", cascade="all, delete-orphan")


class TextChunkORM(Base):
    __tablename__ = "text_chunks"

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.paper_id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(Text)
    chunk_kind: Mapped[str] = mapped_column(String(32), default="section")
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    section_depth: Mapped[int] = mapped_column(Integer, default=1)
    section_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)

    paper: Mapped[PaperORM] = relationship(back_populates="chunks")


class PaperRefORM(Base):
    __tablename__ = "paper_refs"

    src_paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.paper_id", ondelete="CASCADE"),
        primary_key=True,
    )
    dst_arxiv_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dst_paper_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ref_type: Mapped[str] = mapped_column(String(32), default="citation")
    rank: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResultSetORM(Base):
    __tablename__ = "result_sets"

    result_set_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_result_set_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pattern: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[Any] = mapped_column(DateTime(timezone=True), index=True)


class ResultItemORM(Base):
    __tablename__ = "result_items"

    result_item_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    result_set_id: Mapped[str] = mapped_column(ForeignKey("result_sets.result_set_id", ondelete="CASCADE"), index=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.paper_id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer, index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tldr: Mapped[str | None] = mapped_column(Text, nullable=True)


class JobORM(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _paper_from_orm(row: PaperORM) -> PaperResponse:
    return PaperResponse(
        paper_id=row.paper_id,
        arxiv_id=row.arxiv_id,
        title=row.title,
        authors=row.authors,
        abstract=row.abstract,
        tldr=row.tldr,
        summary_short=row.summary_short,
        summary_eli5=row.summary_eli5,
        categories=row.categories,
        primary_category=row.primary_category,
        published=row.published,
        updated=row.updated,
        doi=row.doi,
        comment=row.comment,
        journal_ref=row.journal_ref,
        pdf_url=row.pdf_url,
        html_url=row.html_url,
        citation_count=row.citation_count,
        influential_citation_count=row.influential_citation_count,
        semantic_scholar_id=row.semantic_scholar_id,
        code_url=row.code_url,
        source_type=row.source_type,
        index_status=row.index_status,
        indexed_at=row.indexed_at,
    )


def _paper_needs_summary_after(paper: PaperResponse, after: PaperResponse) -> bool:
    if paper.influential_citation_count != after.influential_citation_count:
        return paper.influential_citation_count < after.influential_citation_count
    if paper.citation_count != after.citation_count:
        return paper.citation_count < after.citation_count
    if paper.published != after.published:
        return paper.published < after.published
    return paper.paper_id > after.paper_id


class PaperRepository(ABC):
    @abstractmethod
    def init(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def probe(self) -> IntegrationProbeComponent:
        raise NotImplementedError

    @abstractmethod
    def upsert_paper(
        self,
        paper: PaperMetadata,
        *,
        search_document: str = "",
        embedding: list[float] | None = None,
    ) -> PaperResponse:
        raise NotImplementedError

    @abstractmethod
    def upsert_paper_shallow(self, paper: PaperMetadata) -> PaperResponse:
        """Write-through for live-source hits: creates new rows if missing and
        enriches citation/tldr/s2 id fields for existing rows without ever
        touching indexed artifacts (index_status, indexed_at, search_document,
        embedding).
        """
        raise NotImplementedError

    @abstractmethod
    def get_paper(self, paper_id: str) -> PaperResponse | None:
        raise NotImplementedError

    @abstractmethod
    def get_paper_by_arxiv_id(self, arxiv_id: str) -> PaperResponse | None:
        raise NotImplementedError

    @abstractmethod
    def replace_text_chunks(self, paper_id: str, chunks: list[dict[str, Any]]) -> None:
        raise NotImplementedError

    @abstractmethod
    def update_summaries(self, paper_id: str, *, summary_short: str, summary_eli5: str) -> PaperResponse | None:
        raise NotImplementedError

    @abstractmethod
    def list_papers_needing_summary(self, *, limit: int, after: PaperResponse | None = None) -> list[PaperResponse]:
        raise NotImplementedError

    @abstractmethod
    def list_papers_needing_embedding_backfill(
        self,
        *,
        limit: int,
        include_chunks: bool = True,
    ) -> list[PaperResponse]:
        """Return papers whose paper-level embedding is missing OR, when
        ``include_chunks`` is true, papers that have at least one chunk with a
        missing embedding.

        Always returns the current frontier — callers should fetch without an
        offset and rely on the predicate to drop rows as they get repaired.
        """
        raise NotImplementedError

    @abstractmethod
    def update_paper_embedding(self, paper_id: str, embedding: list[float]) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_chunks_missing_embedding(self, paper_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def update_chunk_embedding(self, chunk_id: str, embedding: list[float]) -> None:
        raise NotImplementedError

    @abstractmethod
    def best_chunk_for_papers(
        self,
        query: str,
        paper_ids: list[str],
        *,
        query_embedding: list[float] | None = None,
    ) -> dict[str, str]:
        """Return {paper_id: best_chunk_content} for each paper id."""
        raise NotImplementedError

    @abstractmethod
    def suggest_papers(self, q: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Typeahead-style lookup over title/authors trigram match."""
        raise NotImplementedError

    @abstractmethod
    def stats(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def replace_paper_refs(self, paper_id: str, refs: list[dict[str, Any]]) -> None:
        """Replace the outgoing citation set for a paper."""
        raise NotImplementedError

    @abstractmethod
    def get_paper_refs(self, paper_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def search_local(
        self,
        query: str,
        *,
        limit: int,
        query_embedding: list[float] | None = None,
        rrf_k: int = 60,
        citation_boost_scale: float = 0.003,
        citation_boost_max: float = 0.015,
    ) -> list[SearchPaperResult]:
        raise NotImplementedError

    @abstractmethod
    def search_sections(
        self,
        query: str,
        *,
        limit: int,
        result_set_id: str | None = None,
        paper_ids: list[str] | None = None,
        query_embedding: list[float] | None = None,
        rrf_k: int = 60,
    ) -> list[SearchSectionResult]:
        raise NotImplementedError

    @abstractmethod
    def save_result_set(
        self,
        *,
        query: str | None,
        papers: list[SearchPaperResult],
        parent_result_set_id: str | None = None,
        pattern: str | None = None,
        ttl_days: int = 7,
    ) -> ResultSetResponse:
        raise NotImplementedError

    @abstractmethod
    def get_result_set(self, result_set_id: str) -> ResultSetResponse | None:
        raise NotImplementedError

    @abstractmethod
    def grep_result_set(self, result_set_id: str, pattern: str, *, limit: int) -> list[GrepPaperMatch]:
        raise NotImplementedError

    @abstractmethod
    def create_job(self, job_type: str, payload: dict[str, Any]) -> JobResponse:
        raise NotImplementedError

    @abstractmethod
    def get_job(self, job_id: str) -> JobResponse | None:
        raise NotImplementedError

    @abstractmethod
    def claim_pending_job(self) -> JobResponse | None:
        raise NotImplementedError

    @abstractmethod
    def mark_job_running(self, job_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def mark_job_completed(self, job_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def mark_job_failed(self, job_id: str, error: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def cleanup_expired_results(self) -> None:
        raise NotImplementedError


class SqlAlchemyPaperRepository(PaperRepository):
    def __init__(self, settings: Settings) -> None:
        self._engine = create_engine(settings.database_url, future=True)
        self._session_factory = sessionmaker(self._engine, expire_on_commit=False)

    @contextmanager
    def _session(self) -> Iterable[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def init(self) -> None:
        with self._engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.execute(text("ALTER TABLE IF EXISTS papers ADD COLUMN IF NOT EXISTS summary_short TEXT DEFAULT ''"))
            connection.execute(text("ALTER TABLE IF EXISTS papers ADD COLUMN IF NOT EXISTS summary_eli5 TEXT DEFAULT ''"))
            connection.execute(text("ALTER TABLE IF EXISTS papers ADD COLUMN IF NOT EXISTS tldr TEXT DEFAULT ''"))
            connection.execute(text("ALTER TABLE IF EXISTS text_chunks ADD COLUMN IF NOT EXISTS chunk_index INTEGER DEFAULT 0"))
            connection.execute(text("ALTER TABLE IF EXISTS text_chunks ADD COLUMN IF NOT EXISTS section_depth INTEGER DEFAULT 1"))
            connection.execute(text("ALTER TABLE IF EXISTS text_chunks ADD COLUMN IF NOT EXISTS section_order INTEGER DEFAULT 0"))
        Base.metadata.create_all(self._engine)
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS papers_embedding_hnsw "
                    "ON papers USING hnsw (embedding vector_cosine_ops) "
                    "WITH (m = 16, ef_construction = 64)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS text_chunks_embedding_hnsw "
                    "ON text_chunks USING hnsw (embedding vector_cosine_ops) "
                    "WITH (m = 16, ef_construction = 64)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS papers_search_document_trgm "
                    "ON papers USING gin (search_document gin_trgm_ops)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS text_chunks_content_trgm "
                    "ON text_chunks USING gin (content gin_trgm_ops)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS paper_refs_dst_arxiv_id "
                    "ON paper_refs (dst_arxiv_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS paper_refs_dst_paper_id "
                    "ON paper_refs (dst_paper_id) WHERE dst_paper_id IS NOT NULL"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS papers_title_trgm "
                    "ON papers USING gin (title gin_trgm_ops)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS papers_authors_trgm "
                    "ON papers USING gin (authors gin_trgm_ops)"
                )
            )

    def probe(self) -> IntegrationProbeComponent:
        result = 0
        with self._engine.connect() as connection:
            result = int(connection.execute(text("SELECT 1")).scalar_one())
        return IntegrationProbeComponent(
            ok=result == 1,
            backend="sqlalchemy",
            detail="SELECT 1 succeeded" if result == 1 else f"Unexpected probe result: {result}",
            target=self._engine.url.get_backend_name(),
            metadata={"driver": self._engine.url.get_driver_name()},
        )

    def upsert_paper(
        self,
        paper: PaperMetadata,
        *,
        search_document: str = "",
        embedding: list[float] | None = None,
    ) -> PaperResponse:
        canon_arxiv = canonical_arxiv_id(paper.arxiv_id)
        statement = insert(PaperORM).values(
            paper_id=canonical_paper_id(paper.arxiv_id),
            arxiv_id=canon_arxiv,
            title=paper.title,
            authors=paper.authors,
            abstract=paper.abstract,
            tldr=paper.tldr,
            summary_short=paper.summary_short,
            summary_eli5=paper.summary_eli5,
            categories=paper.categories,
            primary_category=paper.primary_category,
            published=paper.published,
            updated=paper.updated,
            doi=paper.doi,
            comment=paper.comment,
            journal_ref=paper.journal_ref,
            pdf_url=paper.pdf_url,
            html_url=paper.html_url,
            citation_count=paper.citation_count,
            influential_citation_count=paper.influential_citation_count,
            semantic_scholar_id=paper.semantic_scholar_id,
            code_url=paper.code_url,
            source_type=paper.source_type,
            index_status=paper.index_status,
            search_document=search_document,
            embedding=embedding,
            indexed_at=paper.indexed_at,
        )
        update_values = {
            "title": paper.title,
            "authors": paper.authors,
            "abstract": paper.abstract,
            "tldr": paper.tldr,
            "summary_short": paper.summary_short,
            "summary_eli5": paper.summary_eli5,
            "categories": paper.categories,
            "primary_category": paper.primary_category,
            "published": paper.published,
            "updated": paper.updated,
            "doi": paper.doi,
            "comment": paper.comment,
            "journal_ref": paper.journal_ref,
            "pdf_url": paper.pdf_url,
            "html_url": paper.html_url,
            "citation_count": paper.citation_count,
            "influential_citation_count": paper.influential_citation_count,
            "semantic_scholar_id": paper.semantic_scholar_id,
            "code_url": paper.code_url,
            "source_type": paper.source_type,
            "index_status": paper.index_status,
            "search_document": search_document,
            "embedding": embedding,
            "indexed_at": paper.indexed_at,
            "updated_at": utcnow(),
        }
        statement = statement.on_conflict_do_update(index_elements=[PaperORM.paper_id], set_=update_values)
        with self._session() as session:
            session.execute(statement)
            row = session.get(PaperORM, canonical_paper_id(paper.arxiv_id))
            assert row is not None
            return _paper_from_orm(row)

    def upsert_paper_shallow(self, paper: PaperMetadata) -> PaperResponse:
        paper_id = canonical_paper_id(paper.arxiv_id)
        canon_arxiv = canonical_arxiv_id(paper.arxiv_id)
        statement = insert(PaperORM).values(
            paper_id=paper_id,
            arxiv_id=canon_arxiv,
            title=paper.title,
            authors=paper.authors,
            abstract=paper.abstract,
            tldr=paper.tldr,
            categories=paper.categories,
            primary_category=paper.primary_category,
            published=paper.published,
            updated=paper.updated,
            doi=paper.doi,
            comment=paper.comment,
            journal_ref=paper.journal_ref,
            pdf_url=paper.pdf_url,
            html_url=paper.html_url,
            citation_count=paper.citation_count,
            influential_citation_count=paper.influential_citation_count,
            semantic_scholar_id=paper.semantic_scholar_id,
            code_url=paper.code_url,
            # Fresh row discovered via a live search — not yet indexed.
            index_status="discovered",
        )
        # On conflict, refresh only metadata that live sources are authoritative for.
        # Never touch index_status, indexed_at, search_document, embedding, or
        # any summary the local indexer has already filled in.
        shallow_update: dict[str, Any] = {
            "citation_count": func.greatest(PaperORM.citation_count, statement.excluded.citation_count),
            "influential_citation_count": func.greatest(
                PaperORM.influential_citation_count, statement.excluded.influential_citation_count
            ),
            "semantic_scholar_id": func.coalesce(PaperORM.semantic_scholar_id, statement.excluded.semantic_scholar_id),
            "tldr": func.coalesce(func.nullif(PaperORM.tldr, ""), statement.excluded.tldr, ""),
            "doi": func.coalesce(PaperORM.doi, statement.excluded.doi),
            "code_url": func.coalesce(PaperORM.code_url, statement.excluded.code_url),
            "updated_at": utcnow(),
        }
        statement = statement.on_conflict_do_update(index_elements=[PaperORM.paper_id], set_=shallow_update)
        with self._session() as session:
            session.execute(statement)
            row = session.get(PaperORM, paper_id)
            assert row is not None
            return _paper_from_orm(row)

    def get_paper(self, paper_id: str) -> PaperResponse | None:
        with self._session() as session:
            row = session.get(PaperORM, paper_id)
            return _paper_from_orm(row) if row else None

    def get_paper_by_arxiv_id(self, arxiv_id: str) -> PaperResponse | None:
        with self._session() as session:
            row = session.scalar(select(PaperORM).where(PaperORM.arxiv_id == arxiv_id))
            return _paper_from_orm(row) if row else None

    def replace_text_chunks(self, paper_id: str, chunks: list[dict[str, Any]]) -> None:
        with self._session() as session:
            session.execute(delete(TextChunkORM).where(TextChunkORM.paper_id == paper_id))
            for chunk in chunks:
                session.add(
                    TextChunkORM(
                        chunk_id=new_id("chunk"),
                        paper_id=paper_id,
                        path=chunk["path"],
                        chunk_kind=chunk["chunk_kind"],
                        content=chunk["content"],
                        embedding=chunk.get("embedding"),
                        chunk_index=int(chunk.get("chunk_index", 0)),
                        section_depth=int(chunk.get("section_depth", 1)),
                        section_order=int(chunk.get("section_order", 0)),
                    )
                )

    def update_summaries(self, paper_id: str, *, summary_short: str, summary_eli5: str) -> PaperResponse | None:
        with self._session() as session:
            row = session.get(PaperORM, paper_id)
            if row is None:
                return None
            row.summary_short = summary_short
            row.summary_eli5 = summary_eli5
            row.updated_at = utcnow()
            session.flush()
            return _paper_from_orm(row)

    def list_papers_needing_summary(self, *, limit: int, after: PaperResponse | None = None) -> list[PaperResponse]:
        query = (
            select(PaperORM)
            .where((PaperORM.summary_eli5 == "") | (PaperORM.summary_eli5.is_(None)))
            .where(PaperORM.index_status == "indexed")
        )
        if after is not None:
            query = query.where(
                (PaperORM.influential_citation_count < after.influential_citation_count)
                | (
                    (PaperORM.influential_citation_count == after.influential_citation_count)
                    & (PaperORM.citation_count < after.citation_count)
                )
                | (
                    (PaperORM.influential_citation_count == after.influential_citation_count)
                    & (PaperORM.citation_count == after.citation_count)
                    & (PaperORM.published < after.published)
                )
                | (
                    (PaperORM.influential_citation_count == after.influential_citation_count)
                    & (PaperORM.citation_count == after.citation_count)
                    & (PaperORM.published == after.published)
                    & (PaperORM.paper_id > after.paper_id)
                )
            )
        query = query.order_by(
            PaperORM.influential_citation_count.desc(),
            PaperORM.citation_count.desc(),
            PaperORM.published.desc(),
            PaperORM.paper_id.asc(),
        ).limit(limit)
        with self._session() as session:
            rows = session.scalars(query).all()
        return [_paper_from_orm(row) for row in rows]

    def list_papers_needing_embedding_backfill(
        self,
        *,
        limit: int,
        include_chunks: bool = True,
    ) -> list[PaperResponse]:
        base_sql = """
            SELECT papers.*
            FROM papers
            WHERE papers.index_status = 'indexed'
              AND (
                papers.embedding IS NULL
                {chunk_predicate}
              )
            ORDER BY
              papers.influential_citation_count DESC NULLS LAST,
              papers.citation_count DESC NULLS LAST,
              papers.published DESC,
              papers.paper_id ASC
            LIMIT :limit
        """
        chunk_predicate = (
            "OR EXISTS (SELECT 1 FROM text_chunks WHERE text_chunks.paper_id = papers.paper_id "
            "AND text_chunks.embedding IS NULL)"
            if include_chunks
            else ""
        )
        sql = text(base_sql.format(chunk_predicate=chunk_predicate))
        with self._session() as session:
            rows = session.scalars(
                select(PaperORM).from_statement(sql).params(limit=limit)
            ).all()
        return [_paper_from_orm(row) for row in rows]

    def update_paper_embedding(self, paper_id: str, embedding: list[float]) -> None:
        with self._session() as session:
            row = session.get(PaperORM, paper_id)
            if row is not None:
                row.embedding = embedding
                row.updated_at = utcnow()

    def list_chunks_missing_embedding(self, paper_id: str) -> list[dict[str, Any]]:
        with self._session() as session:
            rows = session.execute(
                select(TextChunkORM).where(
                    TextChunkORM.paper_id == paper_id,
                    TextChunkORM.embedding.is_(None),
                )
            ).scalars().all()
        return [
            {
                "chunk_id": row.chunk_id,
                "chunk_kind": row.chunk_kind,
                "content": row.content,
            }
            for row in rows
        ]

    def update_chunk_embedding(self, chunk_id: str, embedding: list[float]) -> None:
        with self._session() as session:
            row = session.get(TextChunkORM, chunk_id)
            if row is not None:
                row.embedding = embedding

    def replace_paper_refs(self, paper_id: str, refs: list[dict[str, Any]]) -> None:
        with self._session() as session:
            session.execute(delete(PaperRefORM).where(PaperRefORM.src_paper_id == paper_id))
            seen: set[str] = set()
            for ref in refs:
                dst_arxiv = canonical_arxiv_id(ref.get("arxiv_id") or "")
                if not dst_arxiv or dst_arxiv in seen:
                    continue
                seen.add(dst_arxiv)
                session.add(
                    PaperRefORM(
                        src_paper_id=paper_id,
                        dst_arxiv_id=dst_arxiv,
                        dst_paper_id=f"arxiv:{dst_arxiv}",
                        ref_type=ref.get("ref_type", "citation"),
                        rank=int(ref.get("rank", 0)),
                    )
                )

    def get_paper_refs(self, paper_id: str) -> list[dict[str, Any]]:
        with self._session() as session:
            rows = session.scalars(
                select(PaperRefORM)
                .where(PaperRefORM.src_paper_id == paper_id)
                .order_by(PaperRefORM.rank.asc())
            ).all()
        return [
            {
                "src_paper_id": row.src_paper_id,
                "dst_arxiv_id": row.dst_arxiv_id,
                "dst_paper_id": row.dst_paper_id,
                "ref_type": row.ref_type,
                "rank": row.rank,
            }
            for row in rows
        ]

    def best_chunk_for_papers(
        self,
        query: str,
        paper_ids: list[str],
        *,
        query_embedding: list[float] | None = None,
    ) -> dict[str, str]:
        if not paper_ids:
            return {}
        params: dict[str, Any] = {"query": query, "paper_ids": paper_ids}
        order_clause = "lexical_score DESC, kind_score DESC"
        select_vector_score = "NULL AS vector_score"
        if query_embedding:
            embedding_literal = "[" + ",".join(f"{v:.6f}" for v in query_embedding) + "]"
            params["query_embedding"] = embedding_literal
            select_vector_score = "1 - (embedding <=> CAST(:query_embedding AS vector)) AS vector_score"
            order_clause = "(0.5 * coalesce(vector_score,0) + 0.5 * lexical_score) DESC, kind_score DESC"
        sql = text(
            f"""
            SELECT DISTINCT ON (paper_id)
                paper_id,
                content
            FROM (
                SELECT
                    tc.paper_id,
                    tc.content,
                    tc.chunk_kind,
                    tc.section_depth,
                    ts_rank_cd(
                        to_tsvector('english', coalesce(tc.content, '')),
                        websearch_to_tsquery('english', :query)
                    ) AS lexical_score,
                    CASE tc.chunk_kind
                        WHEN 'section' THEN 0.30
                        WHEN 'table' THEN 0.20
                        WHEN 'figure_caption' THEN 0.15
                        WHEN 'full_text' THEN 0.05
                        ELSE 0.0
                    END AS kind_score,
                    {select_vector_score}
                FROM text_chunks tc
                WHERE tc.paper_id = ANY(CAST(:paper_ids AS text[]))
                  AND tc.chunk_kind <> 'full_text'
            ) scored
            ORDER BY paper_id, {order_clause}
            """
        )
        with self._session() as session:
            rows = session.execute(sql, params).mappings().all()
        return {row["paper_id"]: (row["content"] or "")[:2000] for row in rows}

    def suggest_papers(self, q: str, *, limit: int = 10) -> list[dict[str, Any]]:
        q = (q or "").strip()
        if not q:
            return []
        sql = text(
            """
            WITH scored AS (
                SELECT
                    paper_id,
                    arxiv_id,
                    title,
                    authors,
                    summary_short,
                    tldr,
                    citation_count,
                    published,
                    similarity(coalesce(title, ''), :q) AS title_sim,
                    similarity(coalesce(authors, ''), :q) AS authors_sim
                FROM papers
                WHERE title % :q
                   OR authors % :q
                   OR title ILIKE :prefix_like
            )
            SELECT
                paper_id, arxiv_id, title, authors, summary_short, tldr,
                citation_count, published,
                title_sim, authors_sim,
                GREATEST(title_sim, authors_sim) AS score,
                CASE
                    WHEN title ILIKE :prefix_like THEN 'title_prefix'
                    WHEN title_sim >= authors_sim THEN 'title'
                    ELSE 'authors'
                END AS matched_field
            FROM scored
            ORDER BY
                (CASE WHEN title ILIKE :prefix_like THEN 1 ELSE 0 END) DESC,
                score DESC,
                citation_count DESC NULLS LAST,
                published DESC
            LIMIT :limit
            """
        )
        with self._session() as session:
            rows = session.execute(
                sql,
                {"q": q, "prefix_like": f"{q}%", "limit": limit},
            ).mappings().all()
        return [
            {
                "paper_id": row["paper_id"],
                "arxiv_id": row["arxiv_id"],
                "title": row["title"],
                "authors": row["authors"],
                "summary_short": row["summary_short"] or row["tldr"] or "",
                "citation_count": row["citation_count"] or 0,
                "published": row["published"] or "",
                "score": float(row["score"] or 0.0),
                "matched_field": row["matched_field"],
            }
            for row in rows
        ]

    def stats(self) -> dict[str, Any]:
        sql = text(
            """
            SELECT
                COUNT(*) AS total_papers,
                COUNT(*) FILTER (WHERE index_status = 'indexed') AS indexed_papers,
                COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS papers_with_embedding,
                COUNT(*) FILTER (WHERE summary_eli5 IS NOT NULL AND summary_eli5 <> '') AS papers_with_summary,
                MAX(indexed_at) AS last_indexed_at
            FROM papers
            """
        )
        chunk_sql = text(
            """
            SELECT
                COUNT(*) AS total_chunks,
                COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS chunks_with_embedding
            FROM text_chunks
            """
        )
        with self._session() as session:
            paper_row = session.execute(sql).mappings().one()
            chunk_row = session.execute(chunk_sql).mappings().one()
        return {
            "total_papers": int(paper_row["total_papers"] or 0),
            "indexed_papers": int(paper_row["indexed_papers"] or 0),
            "papers_with_embedding": int(paper_row["papers_with_embedding"] or 0),
            "papers_with_summary": int(paper_row["papers_with_summary"] or 0),
            "total_chunks": int(chunk_row["total_chunks"] or 0),
            "chunks_with_embedding": int(chunk_row["chunks_with_embedding"] or 0),
            "last_indexed_at": paper_row["last_indexed_at"],
        }

    _SELECT_PAPER_COLUMNS = """
        paper_id,
        arxiv_id,
        title,
        authors,
        abstract,
        tldr,
        summary_short,
        summary_eli5,
        categories,
        primary_category,
        published,
        updated,
        doi,
        comment,
        journal_ref,
        pdf_url,
        html_url,
        citation_count,
        influential_citation_count,
        semantic_scholar_id,
        code_url,
        source_type,
        index_status,
        indexed_at
    """

    @staticmethod
    def _row_to_paper_result(row: Any, *, score: float, source: str) -> SearchPaperResult:
        return SearchPaperResult(
            paper_id=row["paper_id"],
            arxiv_id=row["arxiv_id"],
            title=row["title"],
            authors=row["authors"],
            abstract=row["abstract"],
            tldr=row["tldr"] or "",
            summary_short=row["summary_short"],
            summary_eli5=row["summary_eli5"],
            categories=row["categories"],
            primary_category=row["primary_category"],
            published=row["published"],
            updated=row["updated"],
            doi=row["doi"],
            comment=row["comment"],
            journal_ref=row["journal_ref"],
            pdf_url=row["pdf_url"],
            html_url=row["html_url"],
            citation_count=row["citation_count"],
            influential_citation_count=row["influential_citation_count"],
            semantic_scholar_id=row["semantic_scholar_id"],
            code_url=row["code_url"],
            source_type=row["source_type"],
            index_status=row["index_status"],
            indexed_at=row["indexed_at"],
            score=score,
            source=source,
        )

    def search_local(
        self,
        query: str,
        *,
        limit: int,
        query_embedding: list[float] | None = None,
        rrf_k: int = 60,
        citation_boost_scale: float = 0.003,
        citation_boost_max: float = 0.015,
    ) -> list[SearchPaperResult]:
        """Hybrid retrieval: lexical (websearch_to_tsquery + weighted tsvector) RRF-fused
        with vector cosine similarity, plus a log-citation boost.

        Returns candidates already fused — the caller can RRF again against live sources.
        """
        candidate_pool = max(limit * 4, 50)
        weighted_document_sql = """
            setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(tldr, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(summary_short, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(abstract, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(summary_eli5, '')), 'B') ||
            setweight(to_tsvector('simple', coalesce(authors, '')), 'C') ||
            setweight(to_tsvector('english', coalesce(search_document, '')), 'C')
        """
        lexical_sql = text(
            f"""
            SELECT
                {self._SELECT_PAPER_COLUMNS},
                ts_rank_cd({weighted_document_sql}, websearch_to_tsquery('english', :query)) AS lexical_score
            FROM papers
            WHERE
                {weighted_document_sql} @@ websearch_to_tsquery('english', :query)
                OR similarity(coalesce(title, '') || ' ' || coalesce(tldr, ''), :query) > 0.15
            ORDER BY lexical_score DESC, citation_count DESC, published DESC
            LIMIT :candidate_pool
            """
        )
        lexical_params = {"query": query, "candidate_pool": candidate_pool}

        vector_rows: list[Any] = []
        if query_embedding:
            embedding_literal = "[" + ",".join(f"{value:.6f}" for value in query_embedding) + "]"
            vector_sql = text(
                f"""
                SELECT
                    {self._SELECT_PAPER_COLUMNS},
                    1 - (embedding <=> CAST(:query_embedding AS vector)) AS vector_score
                FROM papers
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:query_embedding AS vector)
                LIMIT :candidate_pool
                """
            )
            with self._session() as session:
                vector_rows = session.execute(
                    vector_sql,
                    {"query_embedding": embedding_literal, "candidate_pool": candidate_pool},
                ).mappings().all()

        with self._session() as session:
            lexical_rows = session.execute(lexical_sql, lexical_params).mappings().all()

        row_by_id: dict[str, Any] = {}
        rrf: dict[str, float] = {}
        for rank, row in enumerate(lexical_rows):
            pid = row["paper_id"]
            row_by_id[pid] = row
            rrf[pid] = rrf.get(pid, 0.0) + 1.0 / (rrf_k + rank)
        for rank, row in enumerate(vector_rows):
            pid = row["paper_id"]
            row_by_id.setdefault(pid, row)
            # slight weighting towards semantic hits
            rrf[pid] = rrf.get(pid, 0.0) + 1.2 / (rrf_k + rank)

        from math import log10

        fused: list[tuple[float, Any]] = []
        for pid, score in rrf.items():
            row = row_by_id[pid]
            citation_boost = min(
                citation_boost_scale * log10(1 + float(row.get("citation_count") or 0)),
                citation_boost_max,
            )
            recency_boost = 0.0
            published = (row.get("published") or "")[:4]
            if published.isdigit():
                age_years = max(0, utcnow().year - int(published))
                recency_boost = 0.02 * max(0.0, 1.0 - age_years / 10.0)
            fused.append((score + citation_boost + recency_boost, row))

        fused.sort(key=lambda item: item[0], reverse=True)
        return [self._row_to_paper_result(row, score=score, source="local") for score, row in fused[:limit]]

    def search_sections(
        self,
        query: str,
        *,
        limit: int,
        result_set_id: str | None = None,
        paper_ids: list[str] | None = None,
        query_embedding: list[float] | None = None,
        rrf_k: int = 60,
    ) -> list[SearchSectionResult]:
        candidate_pool = max(limit * 4, 50)
        params: dict[str, Any] = {"query": query, "candidate_pool": candidate_pool}
        joins = ["JOIN papers p ON p.paper_id = tc.paper_id"]
        filters = [
            "(to_tsvector('english', coalesce(tc.content, '')) @@ websearch_to_tsquery('english', :query) "
            "OR similarity(coalesce(tc.content, ''), :query) > 0.1)"
        ]
        if result_set_id:
            joins.append("JOIN result_items ri ON ri.paper_id = tc.paper_id")
            filters.append("ri.result_set_id = :result_set_id")
            params["result_set_id"] = result_set_id
        if paper_ids:
            filters.append("tc.paper_id = ANY(CAST(:paper_ids AS text[]))")
            params["paper_ids"] = paper_ids

        lexical_sql = text(
            f"""
            SELECT
                tc.chunk_id,
                tc.paper_id,
                p.arxiv_id,
                p.title,
                tc.path,
                tc.chunk_kind,
                tc.content,
                tc.section_depth,
                p.citation_count,
                p.published,
                ts_rank_cd(
                    to_tsvector('english', coalesce(tc.content, '')),
                    websearch_to_tsquery('english', :query)
                ) AS lexical_score,
                CASE tc.chunk_kind
                    WHEN 'section' THEN 0.30
                    WHEN 'table' THEN 0.20
                    WHEN 'figure_caption' THEN 0.15
                    WHEN 'full_text' THEN 0.05
                    ELSE 0.0
                END AS kind_score,
                CASE
                    WHEN tc.section_depth >= 3 THEN 0.5
                    WHEN tc.section_depth = 2 THEN 0.85
                    ELSE 1.0
                END AS depth_weight
            FROM text_chunks tc
            {' '.join(joins)}
            WHERE {' AND '.join(filters)}
            ORDER BY lexical_score DESC, depth_weight DESC, kind_score DESC, p.citation_count DESC, p.published DESC
            LIMIT :candidate_pool
            """
        )

        with self._session() as session:
            lexical_rows = session.execute(lexical_sql, params).mappings().all()

        vector_rows: list[Any] = []
        if query_embedding:
            embedding_literal = "[" + ",".join(f"{value:.6f}" for value in query_embedding) + "]"
            vector_joins = ["JOIN papers p ON p.paper_id = tc.paper_id"]
            vector_filters = ["tc.embedding IS NOT NULL"]
            vector_params: dict[str, Any] = {
                "query_embedding": embedding_literal,
                "candidate_pool": candidate_pool,
            }
            if result_set_id:
                vector_joins.append("JOIN result_items ri ON ri.paper_id = tc.paper_id")
                vector_filters.append("ri.result_set_id = :result_set_id")
                vector_params["result_set_id"] = result_set_id
            if paper_ids:
                vector_filters.append("tc.paper_id = ANY(CAST(:paper_ids AS text[]))")
                vector_params["paper_ids"] = paper_ids
            vector_sql = text(
                f"""
                SELECT
                    tc.chunk_id,
                    tc.paper_id,
                    p.arxiv_id,
                    p.title,
                    tc.path,
                    tc.chunk_kind,
                    tc.content,
                    tc.section_depth,
                    p.citation_count,
                    p.published,
                    CASE tc.chunk_kind
                        WHEN 'section' THEN 0.30
                        WHEN 'table' THEN 0.20
                        WHEN 'figure_caption' THEN 0.15
                        WHEN 'full_text' THEN 0.05
                        ELSE 0.0
                    END AS kind_score,
                    CASE
                        WHEN tc.section_depth >= 3 THEN 0.5
                        WHEN tc.section_depth = 2 THEN 0.85
                        ELSE 1.0
                    END AS depth_weight,
                    1 - (tc.embedding <=> CAST(:query_embedding AS vector)) AS vector_score
                FROM text_chunks tc
                {' '.join(vector_joins)}
                WHERE {' AND '.join(vector_filters)}
                ORDER BY tc.embedding <=> CAST(:query_embedding AS vector)
                LIMIT :candidate_pool
                """
            )
            with self._session() as session:
                vector_rows = session.execute(vector_sql, vector_params).mappings().all()

        row_by_id: dict[str, Any] = {}
        rrf: dict[str, float] = {}
        for rank, row in enumerate(lexical_rows):
            cid = row["chunk_id"]
            row_by_id[cid] = row
            depth_w = float(row.get("depth_weight") or 1.0)
            rrf[cid] = rrf.get(cid, 0.0) + depth_w * (1.0 / (rrf_k + rank)) + float(row["kind_score"] or 0) * 0.05
        for rank, row in enumerate(vector_rows):
            cid = row["chunk_id"]
            row_by_id.setdefault(cid, row)
            depth_w = float(row.get("depth_weight") or 1.0)
            rrf[cid] = rrf.get(cid, 0.0) + depth_w * (1.2 / (rrf_k + rank))

        fused = sorted(
            ((score, row_by_id[cid]) for cid, score in rrf.items()),
            key=lambda pair: pair[0],
            reverse=True,
        )
        results: list[SearchSectionResult] = []
        for score, row in fused[:limit]:
            content = row["content"] or ""
            results.append(
                SearchSectionResult(
                    paper_id=row["paper_id"],
                    arxiv_id=row["arxiv_id"],
                    title=row["title"],
                    path=row["path"],
                    chunk_kind=row["chunk_kind"],
                    preview=content[:320].strip(),
                    score=score,
                )
            )
        return results

    def save_result_set(
        self,
        *,
        query: str | None,
        papers: list[SearchPaperResult],
        parent_result_set_id: str | None = None,
        pattern: str | None = None,
        ttl_days: int = 7,
    ) -> ResultSetResponse:
        result_set_id = new_id("rs")
        now = utcnow()
        expires_at = now + timedelta(days=ttl_days)
        with self._session() as session:
            session.add(
                ResultSetORM(
                    result_set_id=result_set_id,
                    query=query,
                    parent_result_set_id=parent_result_set_id,
                    pattern=pattern,
                    created_at=now,
                    expires_at=expires_at,
                )
            )
            session.flush()
            for rank, paper in enumerate(papers):
                session.add(
                    ResultItemORM(
                        result_item_id=new_id("ri"),
                        result_set_id=result_set_id,
                        paper_id=paper.paper_id,
                        rank=rank,
                        score=paper.score,
                        source=paper.source,
                        tldr=paper.tldr,
                    )
                )
        return ResultSetResponse(
            result_set_id=result_set_id,
            query=query,
            created_at=now,
            expires_at=expires_at,
            papers=papers,
        )

    def get_result_set(self, result_set_id: str) -> ResultSetResponse | None:
        with self._session() as session:
            result_set = session.get(ResultSetORM, result_set_id)
            if result_set is None:
                return None
            rows = session.execute(
                select(ResultItemORM, PaperORM)
                .join(PaperORM, ResultItemORM.paper_id == PaperORM.paper_id)
                .where(ResultItemORM.result_set_id == result_set_id)
                .order_by(ResultItemORM.rank)
            ).all()
        papers: list[SearchPaperResult] = []
        for result_item, paper in rows:
            papers.append(
                SearchPaperResult(
                    **_paper_from_orm(paper).model_dump(),
                    score=result_item.score,
                    source=result_item.source,
                    tldr=result_item.tldr,
                )
            )
        return ResultSetResponse(
            result_set_id=result_set.result_set_id,
            query=result_set.query,
            created_at=result_set.created_at,
            expires_at=result_set.expires_at,
            papers=papers,
        )

    def grep_result_set(self, result_set_id: str, pattern: str, *, limit: int) -> list[GrepPaperMatch]:
        literal = literal_hint_from_pattern(pattern)
        params = {"result_set_id": result_set_id, "pattern": pattern, "limit": limit * 10}
        similarity_clause = ""
        if literal:
            params["literal"] = literal
            similarity_clause = " OR similarity(tc.content, :literal) > 0.05"
        sql = text(
            f"""
            SELECT p.paper_id, p.arxiv_id, p.title, tc.path, tc.content
            FROM text_chunks tc
            JOIN result_items ri ON ri.paper_id = tc.paper_id
            JOIN papers p ON p.paper_id = tc.paper_id
            WHERE ri.result_set_id = :result_set_id
              AND (tc.content ~* :pattern{similarity_clause})
            LIMIT :limit
            """
        )
        regex = re.compile(pattern, re.IGNORECASE)
        with self._session() as session:
            rows = session.execute(sql, params).mappings().all()
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            matches = regex.findall(row["content"])
            if not matches and not regex.search(row["content"]):
                continue
            state = grouped.setdefault(
                row["paper_id"],
                {
                    "arxiv_id": row["arxiv_id"],
                    "title": row["title"],
                    "match_count": 0,
                    "previews": [],
                },
            )
            match_count = max(1, len(matches))
            state["match_count"] += match_count
            if len(state["previews"]) < 5:
                state["previews"].append(f"{row['path']}: {row['content'][:180]}")
        ordered = sorted(grouped.items(), key=lambda item: item[1]["match_count"], reverse=True)[:limit]
        return [
            GrepPaperMatch(
                paper_id=paper_id,
                arxiv_id=data["arxiv_id"],
                title=data["title"],
                match_count=data["match_count"],
                previews=data["previews"],
            )
            for paper_id, data in ordered
        ]

    def create_job(self, job_type: str, payload: dict[str, Any]) -> JobResponse:
        job = JobORM(
            job_id=new_id("job"),
            job_type=job_type,
            status="pending",
            payload=payload,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        with self._session() as session:
            session.add(job)
        return self.get_job(job.job_id)  # type: ignore[return-value]

    def get_job(self, job_id: str) -> JobResponse | None:
        with self._session() as session:
            row = session.get(JobORM, job_id)
        if row is None:
            return None
        return JobResponse(
            job_id=row.job_id,
            job_type=row.job_type,
            status=row.status,
            payload=row.payload,
            error=row.error,
            created_at=row.created_at,
            updated_at=row.updated_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    def claim_pending_job(self) -> JobResponse | None:
        with self._session() as session:
            row = session.scalar(
                select(JobORM)
                .where(JobORM.status == "pending")
                .order_by(JobORM.created_at.asc())
                .limit(1)
            )
            if row is None:
                return None
            row.status = "running"
            row.started_at = utcnow()
            row.updated_at = utcnow()
            job_id = row.job_id
        return self.get_job(job_id)

    def mark_job_running(self, job_id: str) -> None:
        with self._session() as session:
            row = session.get(JobORM, job_id)
            if row:
                row.status = "running"
                row.started_at = row.started_at or utcnow()
                row.updated_at = utcnow()

    def mark_job_completed(self, job_id: str) -> None:
        with self._session() as session:
            row = session.get(JobORM, job_id)
            if row:
                row.status = "completed"
                row.finished_at = utcnow()
                row.updated_at = utcnow()

    def mark_job_failed(self, job_id: str, error: str) -> None:
        with self._session() as session:
            row = session.get(JobORM, job_id)
            if row:
                row.status = "failed"
                row.error = error
                row.finished_at = utcnow()
                row.updated_at = utcnow()

    def cleanup_expired_results(self) -> None:
        with self._session() as session:
            expired_ids = session.scalars(select(ResultSetORM.result_set_id).where(ResultSetORM.expires_at < utcnow())).all()
            if expired_ids:
                session.execute(delete(ResultItemORM).where(ResultItemORM.result_set_id.in_(expired_ids)))
                session.execute(delete(ResultSetORM).where(ResultSetORM.result_set_id.in_(expired_ids)))


class MemoryPaperRepository(PaperRepository):
    def __init__(self) -> None:
        self.papers: dict[str, PaperResponse] = {}
        self.search_documents: dict[str, str] = {}
        self.text_chunks: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.result_sets: dict[str, ResultSetResponse] = {}
        self.jobs: dict[str, JobResponse] = {}
        self.paper_refs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        # Paper-level embeddings, tracked out-of-band because PaperResponse
        # doesn't carry them on the wire.
        self.paper_embeddings: dict[str, list[float] | None] = {}

    def init(self) -> None:
        return None

    def probe(self) -> IntegrationProbeComponent:
        return IntegrationProbeComponent(
            ok=True,
            backend="memory",
            detail="In-memory repository is reachable",
            target="memory://",
        )

    def upsert_paper(
        self,
        paper: PaperMetadata,
        *,
        search_document: str = "",
        embedding: list[float] | None = None,
    ) -> PaperResponse:
        paper_id = canonical_paper_id(paper.arxiv_id)
        canonical = paper.model_copy(update={"arxiv_id": canonical_arxiv_id(paper.arxiv_id)})
        response = PaperResponse(paper_id=paper_id, **canonical.model_dump())
        self.papers[paper_id] = response
        self.search_documents[paper_id] = search_document
        if embedding is not None:
            self.paper_embeddings[paper_id] = embedding
        return response

    def upsert_paper_shallow(self, paper: PaperMetadata) -> PaperResponse:
        paper_id = canonical_paper_id(paper.arxiv_id)
        existing = self.papers.get(paper_id)
        if existing is None:
            discovered = paper.model_copy(
                update={
                    "index_status": "discovered",
                    "arxiv_id": canonical_arxiv_id(paper.arxiv_id),
                }
            )
            response = PaperResponse(paper_id=paper_id, **discovered.model_dump())
            self.papers[paper_id] = response
            return response
        # Never overwrite indexed artifacts; only refresh metadata that live
        # sources are authoritative for.
        updates: dict[str, Any] = {}
        if (paper.citation_count or 0) > (existing.citation_count or 0):
            updates["citation_count"] = paper.citation_count
        if (paper.influential_citation_count or 0) > (existing.influential_citation_count or 0):
            updates["influential_citation_count"] = paper.influential_citation_count
        if not existing.semantic_scholar_id and paper.semantic_scholar_id:
            updates["semantic_scholar_id"] = paper.semantic_scholar_id
        if not existing.tldr and paper.tldr:
            updates["tldr"] = paper.tldr
        if not existing.doi and paper.doi:
            updates["doi"] = paper.doi
        if not existing.code_url and paper.code_url:
            updates["code_url"] = paper.code_url
        if updates:
            refreshed = existing.model_copy(update=updates)
            self.papers[paper_id] = refreshed
            return refreshed
        return existing

    def get_paper(self, paper_id: str) -> PaperResponse | None:
        return self.papers.get(paper_id)

    def get_paper_by_arxiv_id(self, arxiv_id: str) -> PaperResponse | None:
        return self.papers.get(canonical_paper_id(arxiv_id))

    def replace_text_chunks(self, paper_id: str, chunks: list[dict[str, Any]]) -> None:
        # Match the SQL path: assign a stable chunk_id so downstream updates
        # like update_chunk_embedding can find the row again.
        stamped: list[dict[str, Any]] = []
        for chunk in chunks:
            stamped.append({**chunk, "chunk_id": chunk.get("chunk_id") or new_id("chunk")})
        self.text_chunks[paper_id] = stamped

    def update_summaries(self, paper_id: str, *, summary_short: str, summary_eli5: str) -> PaperResponse | None:
        existing = self.papers.get(paper_id)
        if existing is None:
            return None
        updated = existing.model_copy(update={"summary_short": summary_short, "summary_eli5": summary_eli5})
        self.papers[paper_id] = updated
        return updated

    def list_papers_needing_summary(self, *, limit: int, after: PaperResponse | None = None) -> list[PaperResponse]:
        candidates = [
            paper
            for paper in self.papers.values()
            if not paper.summary_eli5 and paper.index_status == "indexed"
        ]
        candidates.sort(
            key=lambda p: (
                -p.influential_citation_count,
                -p.citation_count,
                -int((p.published or "").replace("-", "") or 0),
                p.paper_id,
            ),
        )
        if after is not None:
            candidates = [paper for paper in candidates if _paper_needs_summary_after(paper, after)]
        return candidates[:limit]

    def list_papers_needing_embedding_backfill(
        self,
        *,
        limit: int,
        include_chunks: bool = True,
    ) -> list[PaperResponse]:
        def needs_backfill(paper: PaperResponse) -> bool:
            if paper.index_status != "indexed":
                return False
            if self.paper_embeddings.get(paper.paper_id) is None:
                return True
            if include_chunks:
                return any(
                    not chunk.get("embedding")
                    for chunk in self.text_chunks.get(paper.paper_id, [])
                )
            return False

        candidates = [paper for paper in self.papers.values() if needs_backfill(paper)]
        candidates.sort(
            key=lambda p: (p.influential_citation_count, p.citation_count, p.published, p.paper_id),
            reverse=True,
        )
        return candidates[:limit]

    def update_paper_embedding(self, paper_id: str, embedding: list[float]) -> None:
        if paper_id in self.papers:
            self.paper_embeddings[paper_id] = embedding

    def list_chunks_missing_embedding(self, paper_id: str) -> list[dict[str, Any]]:
        return [chunk for chunk in self.text_chunks.get(paper_id, []) if not chunk.get("embedding")]

    def update_chunk_embedding(self, chunk_id: str, embedding: list[float]) -> None:
        for chunks in self.text_chunks.values():
            for chunk in chunks:
                if chunk.get("chunk_id") == chunk_id:
                    chunk["embedding"] = embedding
                    return

    def replace_paper_refs(self, paper_id: str, refs: list[dict[str, Any]]) -> None:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for ref in refs:
            dst_arxiv = canonical_arxiv_id(ref.get("arxiv_id") or "")
            if not dst_arxiv or dst_arxiv in seen:
                continue
            seen.add(dst_arxiv)
            normalized.append(
                {
                    "src_paper_id": paper_id,
                    "dst_arxiv_id": dst_arxiv,
                    "dst_paper_id": f"arxiv:{dst_arxiv}",
                    "ref_type": ref.get("ref_type", "citation"),
                    "rank": int(ref.get("rank", 0)),
                }
            )
        self.paper_refs[paper_id] = normalized

    def get_paper_refs(self, paper_id: str) -> list[dict[str, Any]]:
        return list(self.paper_refs.get(paper_id, []))

    def best_chunk_for_papers(
        self,
        query: str,
        paper_ids: list[str],
        *,
        query_embedding: list[float] | None = None,
    ) -> dict[str, str]:
        query_lower = (query or "").lower()
        out: dict[str, str] = {}
        for paper_id in paper_ids:
            best = ""
            best_score = -1
            for chunk in self.text_chunks.get(paper_id, []):
                if chunk.get("chunk_kind") == "full_text":
                    continue
                content = chunk.get("content", "")
                score = content.lower().count(query_lower)
                if score > best_score:
                    best = content
                    best_score = score
            if best:
                out[paper_id] = best[:2000]
        return out

    def suggest_papers(self, q: str, *, limit: int = 10) -> list[dict[str, Any]]:
        q = (q or "").strip()
        if not q:
            return []
        q_lower = q.lower()
        hits: list[dict[str, Any]] = []
        for paper in self.papers.values():
            title = (paper.title or "")
            authors = (paper.authors or "")
            title_prefix = title.lower().startswith(q_lower)
            title_match = q_lower in title.lower()
            authors_match = q_lower in authors.lower()
            if not (title_prefix or title_match or authors_match):
                continue
            if title_prefix:
                matched_field = "title_prefix"
                score = 1.0
            elif title_match:
                matched_field = "title"
                score = 0.8
            else:
                matched_field = "authors"
                score = 0.6
            hits.append(
                {
                    "paper_id": paper.paper_id,
                    "arxiv_id": paper.arxiv_id,
                    "title": paper.title,
                    "authors": paper.authors,
                    "summary_short": paper.summary_short or paper.tldr or "",
                    "citation_count": paper.citation_count or 0,
                    "published": paper.published or "",
                    "score": score,
                    "matched_field": matched_field,
                }
            )
        hits.sort(
            key=lambda item: (item["score"], item["citation_count"], item["published"]),
            reverse=True,
        )
        return hits[:limit]

    def stats(self) -> dict[str, Any]:
        total = len(self.papers)
        indexed = sum(1 for p in self.papers.values() if p.index_status == "indexed")
        with_summary = sum(1 for p in self.papers.values() if p.summary_eli5)
        with_embedding = sum(1 for v in self.paper_embeddings.values() if v is not None)
        total_chunks = sum(len(chunks) for chunks in self.text_chunks.values())
        chunks_with_embedding = sum(
            1
            for chunks in self.text_chunks.values()
            for chunk in chunks
            if chunk.get("embedding")
        )
        last_indexed = max(
            (p.indexed_at for p in self.papers.values() if p.indexed_at),
            default=None,
        )
        return {
            "total_papers": total,
            "indexed_papers": indexed,
            "papers_with_embedding": with_embedding,
            "papers_with_summary": with_summary,
            "total_chunks": total_chunks,
            "chunks_with_embedding": chunks_with_embedding,
            "last_indexed_at": last_indexed,
        }

    def search_local(
        self,
        query: str,
        *,
        limit: int,
        query_embedding: list[float] | None = None,
        rrf_k: int = 60,
        citation_boost_scale: float = 0.003,
        citation_boost_max: float = 0.015,
    ) -> list[SearchPaperResult]:
        query_lower = query.lower()
        results: list[SearchPaperResult] = []
        for paper_id, paper in self.papers.items():
            score = 0.0
            if query_lower in paper.title.lower():
                score += 4.0
            if query_lower in paper.tldr.lower():
                score += 3.5
            if query_lower in paper.summary_short.lower():
                score += 3.5
            if query_lower in paper.abstract.lower():
                score += 2.5
            if query_lower in paper.summary_eli5.lower():
                score += 2.0
            if query_lower in self.search_documents.get(paper_id, "").lower():
                score += 1.0
            if score > 0:
                results.append(SearchPaperResult(**paper.model_dump(), score=score, source="local"))
        results.sort(key=lambda item: ((item.score or 0), item.citation_count, item.published), reverse=True)
        return results[:limit]

    def search_sections(
        self,
        query: str,
        *,
        limit: int,
        result_set_id: str | None = None,
        paper_ids: list[str] | None = None,
        query_embedding: list[float] | None = None,
        rrf_k: int = 60,
    ) -> list[SearchSectionResult]:
        query_lower = query.lower()
        allowed_paper_ids = set(paper_ids or [])
        if result_set_id:
            allowed_paper_ids.update(paper.paper_id for paper in self.result_sets[result_set_id].papers)
        results: list[SearchSectionResult] = []
        for paper_id, chunks in self.text_chunks.items():
            if allowed_paper_ids and paper_id not in allowed_paper_ids:
                continue
            paper = self.papers.get(paper_id)
            if paper is None:
                continue
            for chunk in chunks:
                content = chunk["content"]
                if query_lower not in content.lower():
                    continue
                kind_score = {
                    "section": 0.30,
                    "table": 0.20,
                    "figure_caption": 0.15,
                    "full_text": 0.05,
                }.get(chunk["chunk_kind"], 0.0)
                score = len(query_lower) / max(len(content), 1) + kind_score
                results.append(
                    SearchSectionResult(
                        paper_id=paper.paper_id,
                        arxiv_id=paper.arxiv_id,
                        title=paper.title,
                        path=chunk["path"],
                        chunk_kind=chunk["chunk_kind"],
                        preview=content[:320],
                        score=score,
                    )
                )
        results.sort(key=lambda item: (item.score or 0, self.papers[item.paper_id].citation_count), reverse=True)
        return results[:limit]

    def save_result_set(
        self,
        *,
        query: str | None,
        papers: list[SearchPaperResult],
        parent_result_set_id: str | None = None,
        pattern: str | None = None,
        ttl_days: int = 7,
    ) -> ResultSetResponse:
        result_set = ResultSetResponse(
            result_set_id=new_id("rs"),
            query=query,
            created_at=utcnow(),
            expires_at=utcnow() + timedelta(days=ttl_days),
            papers=papers,
        )
        self.result_sets[result_set.result_set_id] = result_set
        return result_set

    def get_result_set(self, result_set_id: str) -> ResultSetResponse | None:
        return self.result_sets.get(result_set_id)

    def grep_result_set(self, result_set_id: str, pattern: str, *, limit: int) -> list[GrepPaperMatch]:
        regex = re.compile(pattern, re.IGNORECASE)
        result_set = self.result_sets[result_set_id]
        matches: list[GrepPaperMatch] = []
        for paper in result_set.papers:
            previews = []
            count = 0
            for chunk in self.text_chunks.get(paper.paper_id, []):
                if regex.search(chunk["content"]):
                    count += len(regex.findall(chunk["content"])) or 1
                    previews.append(f"{chunk['path']}: {chunk['content'][:180]}")
            if count:
                matches.append(
                    GrepPaperMatch(
                        paper_id=paper.paper_id,
                        arxiv_id=paper.arxiv_id,
                        title=paper.title,
                        match_count=count,
                        previews=previews[:5],
                    )
                )
        return matches[:limit]

    def create_job(self, job_type: str, payload: dict[str, Any]) -> JobResponse:
        job = JobResponse(
            job_id=new_id("job"),
            job_type=job_type,
            status="pending",
            payload=payload,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self.jobs[job.job_id] = job
        return job

    def get_job(self, job_id: str) -> JobResponse | None:
        return self.jobs.get(job_id)

    def claim_pending_job(self) -> JobResponse | None:
        for job_id, job in self.jobs.items():
            if job.status == "pending":
                claimed = job.model_copy(update={"status": "running", "started_at": utcnow(), "updated_at": utcnow()})
                self.jobs[job_id] = claimed
                return claimed
        return None

    def mark_job_running(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job:
            self.jobs[job_id] = job.model_copy(update={"status": "running", "updated_at": utcnow(), "started_at": job.started_at or utcnow()})

    def mark_job_completed(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job:
            self.jobs[job_id] = job.model_copy(update={"status": "completed", "updated_at": utcnow(), "finished_at": utcnow()})

    def mark_job_failed(self, job_id: str, error: str) -> None:
        job = self.jobs.get(job_id)
        if job:
            self.jobs[job_id] = job.model_copy(update={"status": "failed", "error": error, "updated_at": utcnow(), "finished_at": utcnow()})

    def cleanup_expired_results(self) -> None:
        now = utcnow()
        for result_set_id in [key for key, value in self.result_sets.items() if value.expires_at < now]:
            del self.result_sets[result_set_id]


def build_repository(settings: Settings) -> PaperRepository:
    if settings.database_url.startswith("memory://"):
        return MemoryPaperRepository()
    return SqlAlchemyPaperRepository(settings)
