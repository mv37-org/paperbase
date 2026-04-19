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
from .models import GrepPaperMatch, IntegrationProbeComponent, JobResponse, PaperMetadata, PaperResponse, ResultSetResponse, SearchPaperResult
from .utils import canonical_paper_id, literal_hint_from_pattern, new_id, utcnow


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
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
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
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utcnow)

    paper: Mapped[PaperORM] = relationship(back_populates="chunks")


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
    def get_paper(self, paper_id: str) -> PaperResponse | None:
        raise NotImplementedError

    @abstractmethod
    def get_paper_by_arxiv_id(self, arxiv_id: str) -> PaperResponse | None:
        raise NotImplementedError

    @abstractmethod
    def replace_text_chunks(self, paper_id: str, chunks: list[dict[str, str]]) -> None:
        raise NotImplementedError

    @abstractmethod
    def search_local(self, query: str, *, limit: int, query_embedding: list[float] | None = None) -> list[SearchPaperResult]:
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
        Base.metadata.create_all(self._engine)

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
        statement = insert(PaperORM).values(
            paper_id=canonical_paper_id(paper.arxiv_id),
            arxiv_id=paper.arxiv_id,
            title=paper.title,
            authors=paper.authors,
            abstract=paper.abstract,
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

    def get_paper(self, paper_id: str) -> PaperResponse | None:
        with self._session() as session:
            row = session.get(PaperORM, paper_id)
            return _paper_from_orm(row) if row else None

    def get_paper_by_arxiv_id(self, arxiv_id: str) -> PaperResponse | None:
        with self._session() as session:
            row = session.scalar(select(PaperORM).where(PaperORM.arxiv_id == arxiv_id))
            return _paper_from_orm(row) if row else None

    def replace_text_chunks(self, paper_id: str, chunks: list[dict[str, str]]) -> None:
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
                    )
                )

    def search_local(self, query: str, *, limit: int, query_embedding: list[float] | None = None) -> list[SearchPaperResult]:
        embedding_literal = None
        vector_sql = "0.0"
        params: dict[str, Any] = {"query": query, "limit": limit}
        weighted_document_sql = """
            setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(summary_short, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(abstract, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(summary_eli5, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(search_document, '')), 'C')
        """
        similarity_document_sql = """
            coalesce(title, '') || ' ' || coalesce(summary_short, '') || ' ' ||
            coalesce(abstract, '') || ' ' || coalesce(summary_eli5, '') || ' ' ||
            coalesce(search_document, '')
        """
        if query_embedding:
            embedding_literal = "[" + ",".join(f"{value:.6f}" for value in query_embedding) + "]"
            vector_sql = "CASE WHEN embedding IS NULL THEN 0 ELSE 1 - (embedding <=> CAST(:query_embedding AS vector)) END"
            params["query_embedding"] = embedding_literal
        sql = text(
            f"""
            SELECT
                paper_id,
                arxiv_id,
                title,
                authors,
                abstract,
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
                indexed_at,
                ts_rank_cd(
                    {weighted_document_sql},
                    plainto_tsquery('english', :query)
                ) AS lexical_score,
                GREATEST(
                    similarity(coalesce(title, ''), :query),
                    similarity(coalesce(summary_short, ''), :query),
                    similarity(coalesce(summary_eli5, ''), :query)
                ) AS summary_score,
                {vector_sql} AS vector_score
            FROM papers
            WHERE
                {weighted_document_sql}
                    @@ plainto_tsquery('english', :query)
                OR similarity({similarity_document_sql}, :query) > 0.05
                OR GREATEST(
                    similarity(coalesce(title, ''), :query),
                    similarity(coalesce(summary_short, ''), :query),
                    similarity(coalesce(summary_eli5, ''), :query)
                ) > 0.05
            ORDER BY lexical_score DESC, summary_score DESC, vector_score DESC, citation_count DESC, published DESC
            LIMIT :limit
            """
        )
        with self._session() as session:
            rows = session.execute(sql, params).mappings().all()
        results: list[SearchPaperResult] = []
        for row in rows:
            score = float(row["lexical_score"] or 0) + float(row["summary_score"] or 0) + float(row["vector_score"] or 0)
            results.append(
                SearchPaperResult(
                    paper_id=row["paper_id"],
                    arxiv_id=row["arxiv_id"],
                    title=row["title"],
                    authors=row["authors"],
                    abstract=row["abstract"],
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
                    source="local",
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
        self.text_chunks: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.result_sets: dict[str, ResultSetResponse] = {}
        self.jobs: dict[str, JobResponse] = {}

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
        response = PaperResponse(paper_id=paper_id, **paper.model_dump())
        self.papers[paper_id] = response
        self.search_documents[paper_id] = search_document
        return response

    def get_paper(self, paper_id: str) -> PaperResponse | None:
        return self.papers.get(paper_id)

    def get_paper_by_arxiv_id(self, arxiv_id: str) -> PaperResponse | None:
        return self.papers.get(canonical_paper_id(arxiv_id))

    def replace_text_chunks(self, paper_id: str, chunks: list[dict[str, str]]) -> None:
        self.text_chunks[paper_id] = chunks

    def search_local(self, query: str, *, limit: int, query_embedding: list[float] | None = None) -> list[SearchPaperResult]:
        query_lower = query.lower()
        results: list[SearchPaperResult] = []
        for paper_id, paper in self.papers.items():
            score = 0.0
            if query_lower in paper.title.lower():
                score += 4.0
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
