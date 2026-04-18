"""Bulk ingestion pipeline for building a comprehensive AI paper database.

Three data sources:
1. arXiv OAI-PMH — harvests ALL metadata for CS papers (~800K+)
2. Semantic Scholar — batch citation enrichment
3. Local embeddings — vector search via sentence-transformers

Usage:
    aipaper ingest arxiv           # harvest all CS paper metadata from arXiv
    aipaper ingest arxiv --resume  # resume interrupted harvest
    aipaper ingest s2              # enrich with Semantic Scholar citations
    aipaper ingest embed           # build vector embeddings for search
"""

import json
import math
import os
import struct
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import numpy as np
import requests

from . import db, CACHE_DIR

OAI_ENDPOINT = "http://export.arxiv.org/oai2"
S2_API = "https://api.semanticscholar.org/graph/v1"
EMBED_DIM = 384
EMBED_DB = CACHE_DIR / "embeddings.npy"
EMBED_IDS = CACHE_DIR / "embed_ids.json"
STATE_FILE = CACHE_DIR / "harvest_state.json"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ai-paperclip/0.1.0 (bulk harvester)"})

AI_SETS = [
    "cs",
]

OAI_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "arx": "http://arxiv.org/OAI/arXivRaw/",
}

AI_CATEGORIES = {
    "cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.NE", "cs.MA", "cs.RO",
    "stat.ML", "cs.IR", "cs.SD", "eess.AS", "cs.HC", "cs.CR",
    "cs.SE", "cs.DC", "cs.PL", "cs.DB", "cs.GT", "cs.MM",
}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def _parse_oai_record(record) -> dict | None:
    """Parse an OAI-PMH record into a paper dict."""
    header = record.find("oai:header", OAI_NS)
    if header is None:
        return None

    status = header.get("status", "")
    if status == "deleted":
        return None

    identifier = header.findtext("oai:identifier", "", OAI_NS)
    arxiv_id = identifier.replace("oai:arXiv.org:", "")

    metadata = record.find("oai:metadata", OAI_NS)
    if metadata is None:
        return None

    arx = metadata.find("arx:arXivRaw", OAI_NS)
    if arx is None:
        return None

    categories_str = arx.findtext("arx:categories", "", OAI_NS)
    categories = categories_str.split()
    primary = categories[0] if categories else ""

    if not any(c in AI_CATEGORIES for c in categories):
        return None

    authors_el = arx.findtext("arx:authors", "", OAI_NS)
    abstract = arx.findtext("arx:abstract", "", OAI_NS)
    if abstract:
        abstract = " ".join(abstract.split())

    title = arx.findtext("arx:title", "", OAI_NS)
    if title:
        title = " ".join(title.split())

    doi = arx.findtext("arx:doi", None, OAI_NS)
    comments = arx.findtext("arx:comments", None, OAI_NS)
    journal_ref = arx.findtext("arx:journal-ref", None, OAI_NS)

    datestamp = header.findtext("oai:datestamp", "", OAI_NS)

    versions = arx.findall("arx:version", OAI_NS)
    published = datestamp
    updated = datestamp
    if versions:
        v1 = versions[0]
        pub_date = v1.findtext("arx:date", "", OAI_NS)
        if pub_date:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub_date)
                published = dt.strftime("%Y-%m-%d")
            except Exception:
                pass
        if len(versions) > 1:
            last = versions[-1]
            upd_date = last.findtext("arx:date", "", OAI_NS)
            if upd_date:
                try:
                    dt = parsedate_to_datetime(upd_date)
                    updated = dt.strftime("%Y-%m-%d")
                except Exception:
                    pass

    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "authors": authors_el,
        "abstract": abstract,
        "categories": ", ".join(categories),
        "primary_category": primary,
        "published": published[:10],
        "updated": updated[:10],
        "doi": doi,
        "comment": comments,
        "journal_ref": journal_ref,
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        "semantic_scholar_id": None,
        "citation_count": 0,
        "influential_citation_count": 0,
        "code_url": None,
    }


def harvest_arxiv(callback=None, resume=True):
    """Harvest all AI/ML paper metadata from arXiv via OAI-PMH.

    Uses the arXivRaw metadata format which includes abstracts.
    Filters to AI-related categories.

    Yields progress updates. Stores resumption state for crash recovery.
    """
    conn = db.get_conn()
    state = _load_state() if resume else {}
    token = state.get("resumption_token")
    total_harvested = state.get("total_harvested", 0)
    total_ai = state.get("total_ai", 0)

    batch = 0
    while True:
        params = {"verb": "ListRecords"}
        if token:
            params["resumptionToken"] = token
        else:
            params["metadataPrefix"] = "arXivRaw"
            params["set"] = "cs"

        retries = 0
        while retries < 5:
            try:
                resp = SESSION.get(OAI_ENDPOINT, params=params, timeout=60)
                if resp.status_code == 503:
                    retry_after = int(resp.headers.get("Retry-After", 30))
                    if callback:
                        callback("rate_limited", {"wait": retry_after})
                    time.sleep(retry_after)
                    retries += 1
                    continue
                resp.raise_for_status()
                break
            except requests.RequestException as e:
                retries += 1
                if callback:
                    callback("error", {"error": str(e), "retry": retries})
                time.sleep(min(30 * retries, 120))

        if retries >= 5:
            if callback:
                callback("failed", {"message": "Max retries exceeded"})
            break

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            if callback:
                callback("parse_error", {"error": str(e)})
            break

        error = root.find("oai:error", OAI_NS)
        if error is not None:
            if callback:
                callback("oai_error", {"code": error.get("code"), "message": error.text})
            break

        list_records = root.find("oai:ListRecords", OAI_NS)
        if list_records is None:
            break

        records = list_records.findall("oai:record", OAI_NS)
        batch_ai = 0
        for record in records:
            total_harvested += 1
            paper = _parse_oai_record(record)
            if paper:
                total_ai += 1
                batch_ai += 1
                db.upsert_paper(conn, paper)

        conn.commit()
        batch += 1

        resumption = list_records.find("oai:resumptionToken", OAI_NS)
        if resumption is not None and resumption.text:
            token = resumption.text
            complete_size = resumption.get("completeListSize", "?")
            cursor = resumption.get("cursor", "?")

            _save_state({
                "resumption_token": token,
                "total_harvested": total_harvested,
                "total_ai": total_ai,
                "complete_list_size": complete_size,
                "cursor": cursor,
                "last_batch": datetime.utcnow().isoformat(),
            })

            if callback:
                callback("batch", {
                    "batch": batch,
                    "records": len(records),
                    "ai_papers": batch_ai,
                    "total_harvested": total_harvested,
                    "total_ai": total_ai,
                    "complete_size": complete_size,
                    "cursor": cursor,
                })
        else:
            token = None

        if not token:
            if callback:
                callback("complete", {
                    "total_harvested": total_harvested,
                    "total_ai": total_ai,
                })
            STATE_FILE.unlink(missing_ok=True)
            break

        time.sleep(3)

    db.rebuild_fts(conn)
    return {"total_harvested": total_harvested, "total_ai": total_ai}


def enrich_s2_batch(batch_size: int = 500, callback=None):
    """Enrich all papers lacking S2 data with citation counts.

    Processes papers in batches of 500 (S2 API limit).
    """
    conn = db.get_conn()

    total = conn.execute("SELECT COUNT(*) FROM papers WHERE semantic_scholar_id IS NULL").fetchone()[0]
    if callback:
        callback("start", {"total_unenriched": total})

    offset = 0
    enriched = 0
    failed = 0

    while True:
        rows = conn.execute(
            "SELECT arxiv_id FROM papers WHERE semantic_scholar_id IS NULL LIMIT ? OFFSET ?",
            (batch_size, offset)
        ).fetchall()

        if not rows:
            break

        ids = [f"ArXiv:{r['arxiv_id']}" for r in rows]

        try:
            resp = SESSION.post(
                f"{S2_API}/paper/batch",
                json={"ids": ids},
                params={"fields": "citationCount,influentialCitationCount,externalIds,tldr"},
                timeout=30,
            )

            if resp.status_code == 200:
                results = resp.json()
                for row_data, s2 in zip(rows, results):
                    if s2:
                        conn.execute("""
                            UPDATE papers SET
                                semantic_scholar_id = ?,
                                citation_count = ?,
                                influential_citation_count = ?
                            WHERE arxiv_id = ?
                        """, (
                            s2.get("paperId"),
                            s2.get("citationCount", 0) or 0,
                            s2.get("influentialCitationCount", 0) or 0,
                            row_data["arxiv_id"],
                        ))
                        enriched += 1
                    else:
                        conn.execute(
                            "UPDATE papers SET semantic_scholar_id = 'NOT_FOUND' WHERE arxiv_id = ?",
                            (row_data["arxiv_id"],)
                        )
                        failed += 1
                conn.commit()
            elif resp.status_code == 429:
                if callback:
                    callback("rate_limited", {"wait": 60})
                time.sleep(60)
                continue
            else:
                failed += len(rows)
        except requests.RequestException as e:
            if callback:
                callback("error", {"error": str(e)})
            failed += len(rows)

        offset += batch_size

        if callback:
            callback("batch", {
                "enriched": enriched,
                "failed": failed,
                "total": total,
                "progress": min(100, int((offset / max(total, 1)) * 100)),
            })

        time.sleep(1)

    if callback:
        callback("complete", {"enriched": enriched, "failed": failed})

    return {"enriched": enriched, "failed": failed}


def build_embeddings(batch_size: int = 256, model_name: str = "all-MiniLM-L6-v2", callback=None):
    """Build vector embeddings for all papers using sentence-transformers.

    Embeds title + abstract for each paper. Stores as a memory-mapped
    numpy array for fast similarity search.
    """
    from sentence_transformers import SentenceTransformer

    conn = db.get_conn()
    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]

    if callback:
        callback("loading_model", {"model": model_name, "total_papers": total})

    model = SentenceTransformer(model_name)

    all_ids = []
    all_texts = []

    offset = 0
    chunk = 10000
    while True:
        rows = conn.execute(
            "SELECT arxiv_id, title, abstract FROM papers ORDER BY arxiv_id LIMIT ? OFFSET ?",
            (chunk, offset)
        ).fetchall()
        if not rows:
            break
        for r in rows:
            all_ids.append(r["arxiv_id"])
            text = f"{r['title'] or ''} {r['abstract'] or ''}"
            all_texts.append(text[:512])
        offset += chunk

    if callback:
        callback("encoding", {"total": len(all_texts)})

    all_embeddings = []
    for i in range(0, len(all_texts), batch_size):
        batch_texts = all_texts[i:i + batch_size]
        embeddings = model.encode(batch_texts, show_progress_bar=False, normalize_embeddings=True)
        all_embeddings.append(embeddings)

        if callback and (i // batch_size) % 10 == 0:
            callback("progress", {
                "encoded": min(i + batch_size, len(all_texts)),
                "total": len(all_texts),
                "progress": min(100, int(((i + batch_size) / len(all_texts)) * 100)),
            })

    embeddings_array = np.vstack(all_embeddings).astype(np.float32)
    embeddings_array = np.nan_to_num(embeddings_array, nan=0.0)

    EMBED_DB.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(EMBED_DB), embeddings_array)
    EMBED_IDS.write_text(json.dumps(all_ids))

    if callback:
        callback("complete", {
            "total_embedded": len(all_ids),
            "embedding_dim": embeddings_array.shape[1],
            "file_size_mb": round(EMBED_DB.stat().st_size / 1024 / 1024, 1),
        })

    return {
        "total_embedded": len(all_ids),
        "embedding_dim": embeddings_array.shape[1],
    }


def vector_search(query: str, top_k: int = 30, model_name: str = "all-MiniLM-L6-v2") -> list[dict]:
    """Search papers by semantic similarity using pre-built embeddings."""
    if not EMBED_DB.exists() or not EMBED_IDS.exists():
        raise FileNotFoundError("Embeddings not built. Run: aipaper ingest embed")

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)
    query_embedding = model.encode([query], normalize_embeddings=True).astype(np.float32)

    embeddings = np.load(str(EMBED_DB))
    ids = json.loads(EMBED_IDS.read_text())

    scores = embeddings @ query_embedding.T
    scores = scores.flatten()

    top_indices = np.argsort(scores)[::-1][:top_k]

    conn = db.get_conn()
    results = []
    for idx in top_indices:
        arxiv_id = ids[idx]
        score = float(scores[idx])
        row = conn.execute("SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,)).fetchone()
        if row:
            paper = dict(row)
            paper["similarity_score"] = round(score, 4)
            results.append(paper)

    return results
