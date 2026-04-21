"""One-shot migration to widen paperbase embeddings from 384d to 768d.

Run this once against an existing paperbase Postgres before deploying the
SPECTER2/BGE version of the service.

Assumes:
- pgvector extension already enabled
- existing `papers` table may carry a vector(384) embedding column
- existing `text_chunks` table has no embedding column

This migration is DESTRUCTIVE for existing embeddings (sets them to NULL)
because the old 384d vectors cannot be reused with a 768d model. Paper rows
and chunks themselves are preserved; re-run the embed step afterwards.

Usage:
    python scripts/migrate_to_specter2.py --database-url "$PAPERBASE_DATABASE_URL"
"""

from __future__ import annotations

import argparse
import sys
from urllib.parse import urlparse

try:
    from sqlalchemy import create_engine, text
except ImportError:
    print("sqlalchemy is required. Run `make install` first.", file=sys.stderr)
    sys.exit(1)


MIGRATIONS = [
    # Prereqs
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE EXTENSION IF NOT EXISTS vector",
    # Papers: add tldr if missing
    "ALTER TABLE papers ADD COLUMN IF NOT EXISTS tldr TEXT DEFAULT ''",
    # Papers: drop old HNSW index if it exists (we must re-create after altering dim)
    "DROP INDEX IF EXISTS papers_embedding_hnsw",
    # Papers: null out the old 384d vectors, then widen
    "UPDATE papers SET embedding = NULL WHERE embedding IS NOT NULL",
    "ALTER TABLE papers ALTER COLUMN embedding TYPE vector(768) USING NULL",
    # Papers: HNSW + trigram indexes
    (
        "CREATE INDEX IF NOT EXISTS papers_embedding_hnsw "
        "ON papers USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS papers_search_document_trgm "
        "ON papers USING gin (search_document gin_trgm_ops)"
    ),
    # text_chunks: add embedding column + indexes
    "ALTER TABLE text_chunks ADD COLUMN IF NOT EXISTS embedding vector(768)",
    (
        "CREATE INDEX IF NOT EXISTS text_chunks_embedding_hnsw "
        "ON text_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS text_chunks_content_trgm "
        "ON text_chunks USING gin (content gin_trgm_ops)"
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Widen paperbase embeddings to 768d")
    parser.add_argument("--database-url", required=True, help="PAPERBASE_DATABASE_URL value")
    args = parser.parse_args()

    parsed = urlparse(args.database_url)
    print(
        f"Connecting to {parsed.scheme} @ {parsed.hostname}"
        + (f":{parsed.port}" if parsed.port else "")
        + (parsed.path or "")
    )
    engine = create_engine(args.database_url, future=True)
    applied = 0
    with engine.begin() as connection:
        for statement in MIGRATIONS:
            print(f"  → {statement[:90]}{'...' if len(statement) > 90 else ''}")
            connection.execute(text(statement))
            applied += 1
    print(f"Applied {applied} migration statements.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
