"""Trigram index for sub-second regex search across all paper content.

Stores trigrams extracted from paper text in SQLite, enabling fast
candidate filtering before running actual regex.
"""

import re
import sqlite3
from pathlib import Path

from . import CACHE_DIR

DB_PATH = CACHE_DIR / "trigrams.db"


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS content (
            arxiv_id TEXT PRIMARY KEY,
            full_text TEXT,
            line_count INTEGER
        );
        CREATE TABLE IF NOT EXISTS trigrams (
            trigram TEXT,
            arxiv_id TEXT,
            UNIQUE(trigram, arxiv_id)
        );
        CREATE INDEX IF NOT EXISTS idx_trigrams_tri ON trigrams(trigram);
    """)
    conn.commit()


def _extract_trigrams(text: str) -> set[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    words = text.split()
    trigrams = set()
    for word in words:
        if len(word) >= 3:
            for i in range(len(word) - 2):
                trigrams.add(word[i:i+3])
    return trigrams


def _extract_trigrams_from_pattern(pattern: str) -> set[str]:
    """Extract literal trigrams from a regex pattern for candidate filtering."""
    literal = re.sub(r"\\[wWdDsS.*+?^$|(){}[\]]", "", pattern)
    literal = re.sub(r"[.*+?^$|(){}[\]\\]", "", literal)
    literal = literal.lower()
    trigrams = set()
    if len(literal) >= 3:
        for i in range(len(literal) - 2):
            tri = literal[i:i+3]
            if tri.isalnum() or " " not in tri:
                trigrams.add(tri)
    return trigrams


def index_paper(conn, arxiv_id: str, text: str):
    """Index a paper's full text for trigram search."""
    lines = text.split("\n")
    conn.execute(
        "INSERT OR REPLACE INTO content (arxiv_id, full_text, line_count) VALUES (?, ?, ?)",
        (arxiv_id, text, len(lines))
    )
    trigrams = _extract_trigrams(text)
    conn.executemany(
        "INSERT OR IGNORE INTO trigrams (trigram, arxiv_id) VALUES (?, ?)",
        [(tri, arxiv_id) for tri in trigrams]
    )


def index_batch(conn, papers: list[tuple[str, str]]):
    """Index a batch of (arxiv_id, text) pairs."""
    for arxiv_id, text in papers:
        index_paper(conn, arxiv_id, text)
    conn.commit()


def grep(conn, pattern: str, max_results: int = 50) -> list[dict]:
    """Fast regex search using trigram candidate filtering.

    1. Extract literal trigrams from the regex pattern
    2. Find candidate papers that contain ALL trigrams
    3. Run actual regex only on candidates
    """
    trigrams = _extract_trigrams_from_pattern(pattern)
    regex = re.compile(pattern, re.IGNORECASE)

    if trigrams:
        placeholders = ",".join("?" * len(trigrams))
        query = f"""
            SELECT c.arxiv_id, c.full_text
            FROM content c
            WHERE c.arxiv_id IN (
                SELECT arxiv_id FROM trigrams
                WHERE trigram IN ({placeholders})
                GROUP BY arxiv_id
                HAVING COUNT(DISTINCT trigram) >= ?
            )
        """
        params = list(trigrams) + [len(trigrams)]
        rows = conn.execute(query, params).fetchall()
    else:
        rows = conn.execute("SELECT arxiv_id, full_text FROM content").fetchall()

    results = []
    for row in rows:
        lines = row["full_text"].split("\n")
        matches = [line for line in lines if regex.search(line)]
        if matches:
            results.append({
                "arxiv_id": row["arxiv_id"],
                "match_count": len(matches),
                "matches": matches[:10],
            })
            if len(results) >= max_results:
                break

    results.sort(key=lambda r: r["match_count"], reverse=True)
    return results


def grep_in_paper(conn, arxiv_id: str, pattern: str) -> list[str]:
    """Regex search within a single paper."""
    row = conn.execute("SELECT full_text FROM content WHERE arxiv_id = ?", (arxiv_id,)).fetchone()
    if not row:
        return []
    regex = re.compile(pattern, re.IGNORECASE)
    return [line for line in row["full_text"].split("\n") if regex.search(line)]


def index_all_abstracts(papers_conn, callback=None) -> int:
    """Index abstracts from the papers DB into the trigram index.

    Only indexes papers not already in the trigram content table.
    """
    conn = get_conn()

    existing = {r[0] for r in conn.execute("SELECT arxiv_id FROM content").fetchall()}

    rows = papers_conn.execute(
        "SELECT arxiv_id, title, abstract FROM papers WHERE abstract IS NOT NULL AND abstract != ''"
    ).fetchall()

    to_index = [(r["arxiv_id"], f"{r['title']}\n\n{r['abstract']}") for r in rows if r["arxiv_id"] not in existing]

    if callback:
        callback("start", {"total": len(to_index), "already_indexed": len(existing)})

    batch_size = 1000
    indexed = 0
    for i in range(0, len(to_index), batch_size):
        batch = to_index[i:i + batch_size]
        index_batch(conn, batch)
        indexed += len(batch)
        if callback and indexed % 5000 == 0:
            callback("progress", {"indexed": indexed, "total": len(to_index)})

    conn.commit()
    if callback:
        callback("complete", {"indexed": indexed})
    return indexed


def stats(conn=None) -> dict:
    if conn is None:
        conn = get_conn()
    try:
        total = conn.execute("SELECT MAX(rowid) FROM content").fetchone()[0] or 0
    except Exception:
        total = 0
    try:
        trigram_count = conn.execute("SELECT MAX(rowid) FROM trigrams").fetchone()[0] or 0
    except Exception:
        trigram_count = 0
    return {"indexed_papers": total, "unique_trigrams": trigram_count}
