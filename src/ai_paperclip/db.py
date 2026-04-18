"""Local SQLite cache for indexed AI papers."""

import sqlite3
import json
import os
from pathlib import Path

from . import CACHE_DIR

DB_PATH = CACHE_DIR / "papers.db"


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS papers (
            arxiv_id TEXT PRIMARY KEY,
            title TEXT,
            authors TEXT,
            abstract TEXT,
            categories TEXT,
            primary_category TEXT,
            published TEXT,
            updated TEXT,
            doi TEXT,
            comment TEXT,
            journal_ref TEXT,
            pdf_url TEXT,
            semantic_scholar_id TEXT,
            citation_count INTEGER DEFAULT 0,
            influential_citation_count INTEGER DEFAULT 0,
            code_url TEXT,
            indexed_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_papers_published ON papers(published);
        CREATE INDEX IF NOT EXISTS idx_papers_category ON papers(primary_category);
        CREATE INDEX IF NOT EXISTS idx_papers_citations ON papers(citation_count);

        CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
            arxiv_id, title, abstract, authors, categories
        );

        CREATE TABLE IF NOT EXISTS results (
            results_id TEXT,
            arxiv_id TEXT,
            rank INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (results_id, arxiv_id)
        );

        CREATE TABLE IF NOT EXISTS sync_log (
            category TEXT,
            last_synced TEXT,
            paper_count INTEGER,
            PRIMARY KEY (category)
        );
    """)
    conn.commit()


def upsert_paper(conn, paper: dict):
    conn.execute("""
        INSERT INTO papers (arxiv_id, title, authors, abstract, categories,
            primary_category, published, updated, doi, comment, journal_ref,
            pdf_url, semantic_scholar_id, citation_count,
            influential_citation_count, code_url)
        VALUES (:arxiv_id, :title, :authors, :abstract, :categories,
            :primary_category, :published, :updated, :doi, :comment, :journal_ref,
            :pdf_url, :semantic_scholar_id, :citation_count,
            :influential_citation_count, :code_url)
        ON CONFLICT(arxiv_id) DO UPDATE SET
            title=excluded.title, authors=excluded.authors, abstract=excluded.abstract,
            categories=excluded.categories, primary_category=excluded.primary_category,
            updated=excluded.updated, doi=excluded.doi, comment=excluded.comment,
            journal_ref=excluded.journal_ref, pdf_url=excluded.pdf_url,
            semantic_scholar_id=excluded.semantic_scholar_id,
            citation_count=excluded.citation_count,
            influential_citation_count=excluded.influential_citation_count,
            code_url=excluded.code_url
    """, paper)


def rebuild_fts(conn):
    conn.execute("DELETE FROM papers_fts")
    conn.execute("""
        INSERT INTO papers_fts(rowid, arxiv_id, title, abstract, authors, categories)
        SELECT rowid, arxiv_id, title, abstract, authors, categories FROM papers
    """)
    conn.commit()


def save_results(conn, results_id: str, arxiv_ids: list[str]):
    conn.executemany(
        "INSERT OR REPLACE INTO results (results_id, arxiv_id, rank) VALUES (?, ?, ?)",
        [(results_id, aid, i) for i, aid in enumerate(arxiv_ids)]
    )
    conn.commit()


def get_results(conn, results_id: str) -> list[dict]:
    rows = conn.execute("""
        SELECT p.* FROM papers p
        JOIN results r ON p.arxiv_id = r.arxiv_id
        WHERE r.results_id = ?
        ORDER BY r.rank
    """, (results_id,)).fetchall()
    return [dict(r) for r in rows]


def search_fts(conn, query: str, limit: int = 30) -> list[dict]:
    rows = conn.execute("""
        SELECT p.*, rank FROM papers_fts
        JOIN papers p ON papers_fts.arxiv_id = p.arxiv_id
        WHERE papers_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """, (query, limit)).fetchall()
    return [dict(r) for r in rows]


def run_sql(conn, query: str) -> list[dict]:
    if any(kw in query.upper() for kw in ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE"]):
        raise ValueError("Only SELECT queries are allowed")
    rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def stats(conn) -> dict:
    row = conn.execute("SELECT COUNT(*) as total, MIN(published) as earliest, MAX(published) as latest FROM papers").fetchone()
    cats = conn.execute("SELECT primary_category, COUNT(*) as cnt FROM papers GROUP BY primary_category ORDER BY cnt DESC LIMIT 20").fetchall()
    return {
        "total_papers": row["total"],
        "earliest": row["earliest"],
        "latest": row["latest"],
        "categories": {r["primary_category"]: r["cnt"] for r in cats}
    }
