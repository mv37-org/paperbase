"""AI Paperclip server — all logic lives here.

The CLI is a thin client that sends commands to POST /api/cli/execute.
This server handles search (hybrid BM25+vector+citations with RRF),
sub-second trigram-accelerated grep, virtual filesystem, map, and more.
"""

import base64
import json
import os
import re
import shlex
import subprocess
import textwrap
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from textwrap import shorten

import numpy as np
import requests as http_requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

def _get_anthropic_client():
    """Get an Anthropic client, checking config for API key if not in env."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.Anthropic()
    config_file = DATA_DIR / "config.json"
    if config_file.exists():
        try:
            cfg = json.loads(config_file.read_text())
            if cfg.get("anthropic_api_key"):
                return anthropic.Anthropic(api_key=cfg["anthropic_api_key"])
        except Exception:
            pass
    return anthropic.Anthropic()

from . import db, sources, filesystem, trigram, bulk, PAPERS_DIR as CACHE_DIR, DATA_DIR, __version__


# ── Request/Response models ──────────────────────────────────────

class ExecuteRequest(BaseModel):
    command: str
    raw: str = ""


class ExecuteResponse(BaseModel):
    output: str
    result_id: str | None = None
    elapsed_ms: int | None = None


# ── Helpers ──────────────────────────────────────────────────────

def _rid():
    return uuid.uuid4().hex[:12]


def _ms_since(start):
    return int((time.time() - start) * 1000)


def _fmt_paper(p: dict, idx: int = 0) -> str:
    cite = p.get("citation_count", 0) or 0
    cat = p.get("primary_category", "")[:8]
    date = (p.get("published") or "")[:10]
    title = shorten(p.get("title", ""), width=80, placeholder="...")
    score = p.get("_score", "")
    score_str = f"  [{score:.3f}]" if isinstance(score, float) else ""
    return f"  {idx+1}. {p['arxiv_id']}  {date}  {cat:8s}  [{cite:>5d} cites]{score_str}  {title}"


def _fmt_results(papers: list[dict], rid: str, header: str = "", show_abstract: bool = False) -> str:
    lines = []
    if header:
        lines.append(header)
    lines.append(f"  {len(papers)} papers  [saved to {rid}]\n")
    for i, p in enumerate(papers):
        lines.append(_fmt_paper(p, i))
        if show_abstract and p.get("abstract"):
            abstract = shorten(p["abstract"], width=200, placeholder="...")
            lines.append(f"    \"{abstract}\"\n")
    return "\n".join(lines)


def _ensure_paper(arxiv_id: str) -> Path:
    """Ensure paper is fetched, parsed, cached. Returns its directory."""
    d = filesystem.paper_dir(arxiv_id)
    if (d / "manifest.json").exists():
        return d

    conn = db.get_conn()
    row = conn.execute("SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,)).fetchone()
    paper_meta = dict(row) if row else None

    if not paper_meta:
        paper_meta = sources.get_paper_details(arxiv_id)
        if not paper_meta:
            raise HTTPException(status_code=404, detail=f"Paper {arxiv_id} not found on arXiv")
        sources.enrich_with_s2([paper_meta])
        db.upsert_paper(conn, paper_meta)
        conn.commit()

    filesystem.pull_paper(arxiv_id, paper_meta)

    tconn = trigram.get_conn()
    content_path = d / "content.lines"
    if content_path.exists():
        trigram.index_paper(tconn, arxiv_id, content_path.read_text())
        tconn.commit()

    return d


# ── Hybrid Search with Reciprocal Rank Fusion ────────────────────

def _hybrid_search(query: str, max_results: int = 30, category: str = None,
                   sort: str = "relevance", year: str = None) -> tuple[list[dict], str]:
    """Combine BM25 (FTS5) + vector similarity + arXiv/S2 API + citations via RRF."""
    conn = db.get_conn()

    # Source 1: FTS5 (BM25-like)
    fts_results = []
    try:
        fts_results = db.search_fts(conn, query, limit=max_results * 2)
        for i, p in enumerate(fts_results):
            p["_fts_rank"] = i
    except Exception:
        pass

    # Source 2: Vector search (if embeddings exist)
    vec_results = []
    try:
        vec_results = bulk.vector_search(query, top_k=max_results * 2)
        for i, p in enumerate(vec_results):
            p["_vec_rank"] = i
    except (FileNotFoundError, Exception):
        pass

    # Source 3: arXiv API
    arxiv_results = []
    try:
        arxiv_results = sources.search_arxiv(query, max_results=max_results, category=category, sort_by=sort)
        for i, p in enumerate(arxiv_results):
            p["_api_rank"] = i
    except Exception:
        pass

    # Source 4: Semantic Scholar
    s2_results = []
    try:
        s2_results = sources.search_s2(query, limit=max_results, year=year, fields_of_study=["Computer Science"])
        for i, p in enumerate(s2_results):
            p["_s2_rank"] = i
    except Exception:
        pass

    # Merge by arxiv_id, keeping best metadata
    merged = {}
    for source in [fts_results, vec_results, arxiv_results, s2_results]:
        for p in source:
            aid = p["arxiv_id"]
            if aid not in merged:
                merged[aid] = p.copy()
            else:
                existing = merged[aid]
                for key in ["_fts_rank", "_vec_rank", "_api_rank", "_s2_rank",
                            "citation_count", "influential_citation_count",
                            "semantic_scholar_id", "similarity_score"]:
                    if p.get(key) is not None and existing.get(key) is None:
                        existing[key] = p[key]
                if (p.get("citation_count") or 0) > (existing.get("citation_count") or 0):
                    existing["citation_count"] = p["citation_count"]
                    existing["influential_citation_count"] = p.get("influential_citation_count", 0)

    # Reciprocal Rank Fusion
    k = 60  # RRF constant
    for aid, p in merged.items():
        score = 0.0
        if "_fts_rank" in p:
            score += 1.0 / (k + p["_fts_rank"])
        if "_vec_rank" in p:
            score += 1.2 / (k + p["_vec_rank"])  # slightly higher weight for semantic
        if "_api_rank" in p:
            score += 0.8 / (k + p["_api_rank"])
        if "_s2_rank" in p:
            score += 0.8 / (k + p["_s2_rank"])

        # Citation boost (log-scaled)
        cites = p.get("citation_count", 0) or 0
        if cites > 0:
            import math
            score += 0.3 * math.log10(1 + cites) / (k + 0)

        p["_score"] = score

    papers = list(merged.values())

    if sort == "citations":
        papers.sort(key=lambda p: p.get("citation_count", 0) or 0, reverse=True)
    elif sort == "date":
        papers.sort(key=lambda p: p.get("published", ""), reverse=True)
    else:
        papers.sort(key=lambda p: p.get("_score", 0), reverse=True)

    papers = papers[:max_results]

    # Enrich unenriched
    unenriched = [p for p in papers if not p.get("semantic_scholar_id")]
    if unenriched:
        sources.enrich_with_s2(unenriched)

    # Persist to DB
    for p in papers:
        clean = {k: v for k, v in p.items() if not k.startswith("_")}
        db.upsert_paper(conn, clean)
    conn.commit()

    rid = _rid()
    db.save_results(conn, rid, [p["arxiv_id"] for p in papers])
    db.rebuild_fts(conn)

    return papers, rid


# ── Command Handlers ─────────────────────────────────────────────

def _cmd_search(raw: str) -> ExecuteResponse:
    start = time.time()
    parts = shlex.split(raw) if raw else []

    query_parts = []
    max_results = 30
    category = None
    sort = "relevance"
    year = None
    show_abstract = False
    i = 0
    while i < len(parts):
        if parts[i] in ("-n", "--max", "--limit") and i + 1 < len(parts):
            max_results = int(parts[i+1]); i += 2
        elif parts[i] in ("-c", "--category") and i + 1 < len(parts):
            category = parts[i+1]; i += 2
        elif parts[i] in ("-s", "--sort") and i + 1 < len(parts):
            sort = parts[i+1]; i += 2
        elif parts[i] in ("-y", "--year") and i + 1 < len(parts):
            year = parts[i+1]; i += 2
        elif parts[i] in ("-a", "--abstracts"):
            show_abstract = True; i += 1
        elif parts[i].startswith("-"):
            i += 1
        else:
            query_parts.append(parts[i]); i += 1

    query = " ".join(query_parts)
    if not query:
        return ExecuteResponse(output="Usage: aipaper search <query> [--sort relevance|date|citations] [-n N] [-c category]")

    papers, rid = _hybrid_search(query, max_results=max_results, category=category, sort=sort, year=year)

    output = _fmt_results(papers, rid, header=f"  search \"{query}\"", show_abstract=show_abstract)
    return ExecuteResponse(output=output, result_id=rid, elapsed_ms=_ms_since(start))


def _cmd_grep(raw: str) -> ExecuteResponse:
    start = time.time()
    parts = shlex.split(raw) if raw else []

    pattern = ""
    from_rid = None
    field = "abstract"
    i = 0
    while i < len(parts):
        if parts[i] == "--from" and i + 1 < len(parts):
            from_rid = parts[i+1]; i += 2
        elif parts[i] in ("-f", "--field") and i + 1 < len(parts):
            field = parts[i+1]; i += 2
        elif not parts[i].startswith("-"):
            pattern = parts[i]; i += 1
        else:
            i += 1

    if not pattern:
        return ExecuteResponse(output="Usage: aipaper grep <pattern> [--from <results_id>] [--field title|abstract|all|content]")

    conn = db.get_conn()

    if field == "content":
        tconn = trigram.get_conn()
        results = trigram.grep(tconn, pattern, max_results=50)

        trigram_ids = {r["arxiv_id"] for r in results}
        regex = re.compile(pattern, re.IGNORECASE)
        if from_rid:
            abstract_papers = db.get_results(conn, from_rid)
        else:
            abstract_papers = [dict(r) for r in conn.execute(
                "SELECT arxiv_id, title, abstract FROM papers WHERE abstract IS NOT NULL AND abstract != '' LIMIT 50000"
            ).fetchall()]

        abstract_matches = []
        for p in abstract_papers:
            aid = p["arxiv_id"]
            if aid in trigram_ids:
                continue
            abstract = p.get("abstract", "") or ""
            if regex.search(abstract):
                abstract_matches.append({
                    "arxiv_id": aid,
                    "match_count": len(regex.findall(abstract)),
                    "matches": [f"[abstract] {abstract[:200]}"],
                    "source": "abstract",
                })

        all_results = results + abstract_matches[:50 - len(results)]
        all_results.sort(key=lambda r: r["match_count"], reverse=True)

        if not all_results:
            return ExecuteResponse(output=f"  grep /{pattern}/ in full text + abstracts — 0 matches", elapsed_ms=_ms_since(start))

        total_matches = sum(r["match_count"] for r in all_results)
        ft_count = len(results)
        ab_count = len([r for r in all_results if r.get("source") == "abstract"])
        lines = [f"  grep /{pattern}/ — {total_matches} matches across {len(all_results)} papers "
                 f"({ft_count} full-text, {ab_count} abstract-only)\n"]
        for r in all_results:
            source_tag = " [abstract]" if r.get("source") == "abstract" else ""
            lines.append(f"  {r['arxiv_id']} ({r['match_count']} matches){source_tag}")
            for m in r["matches"][:3]:
                lines.append(f"    {m[:120]}")
            if r["match_count"] > 3:
                lines.append(f"    ... and {r['match_count'] - 3} more")
            lines.append("")

        rid = _rid()
        db.save_results(conn, rid, [r["arxiv_id"] for r in all_results])
        return ExecuteResponse(output="\n".join(lines), result_id=rid, elapsed_ms=_ms_since(start))

    # Database grep (title/abstract/authors)
    if from_rid:
        papers = db.get_results(conn, from_rid)
    else:
        papers = [dict(r) for r in conn.execute("SELECT * FROM papers").fetchall()]

    regex = re.compile(pattern, re.IGNORECASE)
    matches = []
    for p in papers:
        if field == "all":
            text = f"{p.get('title','')} {p.get('abstract','')} {p.get('authors','')}"
        else:
            text = p.get(field, "") or ""
        if regex.search(text):
            matches.append(p)

    rid = _rid()
    db.save_results(conn, rid, [p["arxiv_id"] for p in matches])

    scope = f"from {from_rid}" if from_rid else f"{len(papers):,} papers"
    output = f"  grep /{pattern}/ in {field} — {len(matches)}/{len(papers)} matched\n\n"
    output += _fmt_results(matches[:50], rid)
    return ExecuteResponse(output=output, result_id=rid, elapsed_ms=_ms_since(start))


def _cmd_cat(raw: str) -> ExecuteResponse:
    start = time.time()
    path = raw.strip()
    if not path:
        return ExecuteResponse(output="Usage: aipaper cat <arxiv_id>/<filepath>")

    arxiv_id, _, filepath = path.partition("/")
    if not filepath:
        return ExecuteResponse(output="Usage: aipaper cat <arxiv_id>/<filepath>")

    _ensure_paper(arxiv_id)
    try:
        content = filesystem.read_file(arxiv_id, filepath)
        return ExecuteResponse(output=content, elapsed_ms=_ms_since(start))
    except FileNotFoundError:
        available = filesystem.list_paper_files(arxiv_id)
        return ExecuteResponse(output=f"File not found: /papers/{arxiv_id}/{filepath}\nAvailable: {', '.join(available)}")


def _cmd_head(raw: str) -> ExecuteResponse:
    start = time.time()
    parts = shlex.split(raw) if raw else []
    n = 40
    path = ""
    i = 0
    while i < len(parts):
        if parts[i] in ("-n",) and i + 1 < len(parts):
            n = int(parts[i+1]); i += 2
        elif parts[i].startswith("-") and parts[i][1:].isdigit():
            n = int(parts[i][1:]); i += 1
        elif not parts[i].startswith("-"):
            path = parts[i]; i += 1
        else:
            i += 1

    if not path:
        return ExecuteResponse(output="Usage: aipaper head [-n N] <arxiv_id>/<filepath>")

    arxiv_id, _, filepath = path.partition("/")
    if not filepath:
        return ExecuteResponse(output="Usage: aipaper head [-n N] <arxiv_id>/<filepath>")

    _ensure_paper(arxiv_id)
    try:
        content = filesystem.head_file(arxiv_id, filepath, n=n)
        return ExecuteResponse(output=content, elapsed_ms=_ms_since(start))
    except FileNotFoundError:
        return ExecuteResponse(output=f"File not found: /papers/{arxiv_id}/{filepath}")


def _cmd_tail(raw: str) -> ExecuteResponse:
    start = time.time()
    parts = shlex.split(raw) if raw else []
    n = 40
    path = ""
    i = 0
    while i < len(parts):
        if parts[i] in ("-n",) and i + 1 < len(parts):
            n = int(parts[i+1]); i += 2
        elif not parts[i].startswith("-"):
            path = parts[i]; i += 1
        else:
            i += 1

    if not path:
        return ExecuteResponse(output="Usage: aipaper tail [-n N] <arxiv_id>/<filepath>")

    arxiv_id, _, filepath = path.partition("/")
    if not filepath:
        return ExecuteResponse(output="Usage: aipaper tail [-n N] <arxiv_id>/<filepath>")

    _ensure_paper(arxiv_id)
    try:
        content = filesystem.read_file(arxiv_id, filepath)
        lines = content.split("\n")
        return ExecuteResponse(output="\n".join(lines[-n:]), elapsed_ms=_ms_since(start))
    except FileNotFoundError:
        return ExecuteResponse(output=f"File not found: /papers/{arxiv_id}/{filepath}")


def _cmd_ls(raw: str) -> ExecuteResponse:
    start = time.time()
    path = raw.strip()
    if not path:
        return ExecuteResponse(output="Usage: aipaper ls <arxiv_id>[/subdir]")

    arxiv_id = path.split("/")[0]
    _ensure_paper(arxiv_id)

    files = filesystem.list_paper_files(arxiv_id)
    subdir = "/".join(path.split("/")[1:]) if "/" in path else ""

    d = filesystem.paper_dir(arxiv_id)
    if subdir:
        target = d / subdir
        if not target.exists():
            return ExecuteResponse(output=f"Path not found: {path}")
        if target.is_file():
            return ExecuteResponse(output=f"{target.name}  ({target.stat().st_size:,} bytes)")
        items = []
        for item in sorted(target.iterdir()):
            if item.is_dir():
                items.append(f"{item.name}/  ({len(list(item.iterdir()))} files)")
            else:
                items.append(f"{item.name}  ({item.stat().st_size:,} bytes)")
        return ExecuteResponse(output="\n".join(items), elapsed_ms=_ms_since(start))

    items = []
    for f in files:
        fp = d / f
        items.append(f"{f}  ({fp.stat().st_size:,} bytes)")
    return ExecuteResponse(output="\n".join(items), elapsed_ms=_ms_since(start))


def _cmd_scan(raw: str) -> ExecuteResponse:
    start = time.time()
    parts = shlex.split(raw) if raw else []
    if len(parts) < 2:
        return ExecuteResponse(output="Usage: aipaper scan <arxiv_id>/<filepath> <pattern1> [pattern2 ...]")

    path = parts[0]
    patterns = parts[1:]
    arxiv_id, _, filepath = path.partition("/")
    if not filepath:
        return ExecuteResponse(output="Usage: aipaper scan <arxiv_id>/<filepath> <pattern1> [pattern2 ...]")

    _ensure_paper(arxiv_id)
    try:
        content = filesystem.read_file(arxiv_id, filepath)
    except FileNotFoundError:
        return ExecuteResponse(output=f"File not found: /papers/{arxiv_id}/{filepath}")

    lines_out = []
    for p in patterns:
        regex = re.compile(p, re.IGNORECASE)
        matches = [line for line in content.split("\n") if regex.search(line)]
        lines_out.append(f"  /{p}/ — {len(matches)} matches")
        for m in matches[:20]:
            lines_out.append(f"    {m[:120]}")
        if len(matches) > 20:
            lines_out.append(f"    ... and {len(matches) - 20} more")
        lines_out.append("")

    return ExecuteResponse(output="\n".join(lines_out), elapsed_ms=_ms_since(start))


def _cmd_read(raw: str) -> ExecuteResponse:
    start = time.time()
    parts = shlex.split(raw) if raw else []
    if not parts:
        return ExecuteResponse(output="Usage: aipaper read <arxiv_id> [--code]")

    arxiv_id = parts[0].strip()
    check_code = "--code" in parts

    conn = db.get_conn()
    row = conn.execute("SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,)).fetchone()
    if row:
        paper = dict(row)
    else:
        paper = sources.get_paper_details(arxiv_id)
        if not paper:
            return ExecuteResponse(output=f"Paper {arxiv_id} not found")
        sources.enrich_with_s2([paper])
        db.upsert_paper(conn, paper)
        conn.commit()

    if check_code and not paper.get("code_url"):
        code_url = sources.get_paper_code(arxiv_id)
        if code_url:
            paper["code_url"] = code_url
            db.upsert_paper(conn, paper)
            conn.commit()

    lines = [
        f"  {paper['title']}",
        f"  {'=' * min(len(paper['title']), 80)}",
        f"  arXiv: {paper['arxiv_id']}  Published: {paper.get('published','')}  Category: {paper.get('primary_category','')}",
        f"  Authors: {paper.get('authors','')}",
        f"  Citations: {paper.get('citation_count', 0)}  Influential: {paper.get('influential_citation_count', 0)}",
        f"  PDF: {paper.get('pdf_url','')}",
    ]
    if paper.get("code_url"):
        lines.append(f"  Code: {paper['code_url']}")
    if paper.get("doi"):
        lines.append(f"  DOI: {paper['doi']}")
    if paper.get("comment"):
        lines.append(f"  Comment: {paper['comment']}")
    lines.append(f"\n  Abstract\n  {paper.get('abstract','')}")

    return ExecuteResponse(output="\n".join(lines), elapsed_ms=_ms_since(start))


def _cmd_sql(raw: str) -> ExecuteResponse:
    start = time.time()
    if not raw:
        return ExecuteResponse(output="Usage: aipaper sql \"SELECT ...\"")

    conn = db.get_conn()
    try:
        rows = db.run_sql(conn, raw.strip().strip('"').strip("'"))
    except Exception as e:
        return ExecuteResponse(output=f"SQL error: {e}")

    if not rows:
        return ExecuteResponse(output="  No rows returned.", elapsed_ms=_ms_since(start))

    cols = list(rows[0].keys())
    col_widths = {c: max(len(c), max(len(str(r.get(c, ""))[:40]) for r in rows)) for c in cols}

    header = "  ".join(c.ljust(col_widths[c]) for c in cols)
    separator = "  ".join("-" * col_widths[c] for c in cols)
    lines = [f"  {header}", f"  {separator}"]
    for r in rows[:200]:
        line = "  ".join(str(r.get(c, ""))[:40].ljust(col_widths[c]) for c in cols)
        lines.append(f"  {line}")

    output = "\n".join(lines)
    if len(rows) > 200:
        output += f"\n  ... {len(rows) - 200} more rows"

    return ExecuteResponse(output=output, elapsed_ms=_ms_since(start))


def _cmd_related(raw: str) -> ExecuteResponse:
    start = time.time()
    parts = shlex.split(raw) if raw else []
    if not parts:
        return ExecuteResponse(output="Usage: aipaper related <arxiv_id>")

    arxiv_id = parts[0].strip()
    limit = 10

    conn = db.get_conn()
    row = conn.execute("SELECT semantic_scholar_id FROM papers WHERE arxiv_id = ?", (arxiv_id,)).fetchone()
    s2_id = row["semantic_scholar_id"] if row else None

    if not s2_id:
        papers = sources.enrich_with_s2([{"arxiv_id": arxiv_id}])
        s2_id = papers[0].get("semantic_scholar_id") if papers else None

    if not s2_id:
        return ExecuteResponse(output=f"Could not find Semantic Scholar ID for {arxiv_id}")

    try:
        resp = sources.SESSION.get(
            f"{sources.S2_API}/paper/{s2_id}/recommendations",
            params={"fields": "externalIds,title,abstract,authors,year,citationCount,publicationDate", "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("recommendedPapers", [])
    except Exception as e:
        return ExecuteResponse(output=f"Failed to get recommendations: {e}")

    papers = []
    for item in data:
        ext = item.get("externalIds", {}) or {}
        aid = ext.get("ArXiv", "")
        if not aid:
            continue
        p = {
            "arxiv_id": aid,
            "title": item.get("title", ""),
            "authors": ", ".join(a.get("name", "") for a in (item.get("authors") or [])),
            "abstract": item.get("abstract") or "",
            "categories": "", "primary_category": "",
            "published": (item.get("publicationDate") or "")[:10],
            "updated": "", "doi": ext.get("DOI"), "comment": None,
            "journal_ref": None,
            "pdf_url": f"https://arxiv.org/pdf/{aid}.pdf",
            "semantic_scholar_id": item.get("paperId"),
            "citation_count": item.get("citationCount", 0) or 0,
            "influential_citation_count": 0, "code_url": None,
        }
        papers.append(p)
        db.upsert_paper(conn, p)

    conn.commit()
    rid = _rid()
    db.save_results(conn, rid, [p["arxiv_id"] for p in papers])

    output = _fmt_results(papers, rid, header=f"  related to {arxiv_id}", show_abstract=True)
    return ExecuteResponse(output=output, result_id=rid, elapsed_ms=_ms_since(start))


def _cmd_compare(raw: str) -> ExecuteResponse:
    start = time.time()
    parts = shlex.split(raw) if raw else []
    if len(parts) < 2:
        return ExecuteResponse(output="Usage: aipaper compare <arxiv_id1> <arxiv_id2> [...]")

    conn = db.get_conn()
    papers = []
    for aid in parts:
        row = conn.execute("SELECT * FROM papers WHERE arxiv_id = ?", (aid.strip(),)).fetchone()
        if row:
            papers.append(dict(row))
        else:
            p = sources.get_paper_details(aid.strip())
            if p:
                sources.enrich_with_s2([p])
                db.upsert_paper(conn, p)
                conn.commit()
                papers.append(p)

    if not papers:
        return ExecuteResponse(output="No papers found")

    fields = ["arxiv_id", "title", "published", "primary_category", "citation_count"]
    col_w = 35
    lines = [f"  {'Field':<20s}" + "".join(f"  {p['arxiv_id'][:12]:<{col_w}s}" for p in papers)]
    lines.append("  " + "-" * (20 + (col_w + 2) * len(papers)))
    for field in fields:
        vals = [str(p.get(field, ""))[:col_w] for p in papers]
        lines.append(f"  {field:<20s}" + "".join(f"  {v:<{col_w}s}" for v in vals))

    lines.append("")
    for p in papers:
        lines.append(f"  {p['arxiv_id']}: {shorten(p.get('abstract',''), 200, placeholder='...')}")
        lines.append("")

    return ExecuteResponse(output="\n".join(lines), elapsed_ms=_ms_since(start))


def _cmd_trending(raw: str) -> ExecuteResponse:
    start = time.time()
    parts = shlex.split(raw) if raw else []
    days = 7
    limit = 20
    i = 0
    while i < len(parts):
        if parts[i] in ("-d", "--days") and i + 1 < len(parts):
            days = int(parts[i+1]); i += 2
        elif parts[i] in ("-n", "--limit") and i + 1 < len(parts):
            limit = int(parts[i+1]); i += 2
        else:
            i += 1

    conn = db.get_conn()
    rows = conn.execute(f"""
        SELECT * FROM papers
        WHERE published >= date('now', '-{days} days')
        ORDER BY citation_count DESC, published DESC
        LIMIT {limit}
    """).fetchall()
    papers = [dict(r) for r in rows]

    if not papers:
        return ExecuteResponse(output=f"  No recent papers found (last {days} days). Run: aipaper sync")

    rid = _rid()
    db.save_results(conn, rid, [p["arxiv_id"] for p in papers])

    output = _fmt_results(papers, rid, header=f"  trending (last {days} days)")
    return ExecuteResponse(output=output, result_id=rid, elapsed_ms=_ms_since(start))


def _cmd_stats(raw: str) -> ExecuteResponse:
    conn = db.get_conn()
    s = db.stats(conn)
    tconn = trigram.get_conn()
    ts = trigram.stats(tconn)
    pulled = len(list(CACHE_DIR.iterdir())) if CACHE_DIR.exists() else 0

    embed_info = "not built"
    if bulk.EMBED_DB.exists():
        ids = json.loads(bulk.EMBED_IDS.read_text())
        size_mb = bulk.EMBED_DB.stat().st_size / 1024 / 1024
        embed_info = f"{len(ids):,} papers ({size_mb:.1f} MB)"

    lines = [
        f"  AI Paperclip Database",
        f"  {'=' * 40}",
        f"  Total papers:     {s['total_papers']:,}",
        f"  Date range:       {s['earliest']} → {s['latest']}",
        f"  Pulled (full text): {pulled:,}",
        f"  Trigram indexed:  {ts['indexed_papers']:,} ({ts['unique_trigrams']:,} trigrams)",
        f"  Embeddings:       {embed_info}",
        f"",
        f"  Categories:",
    ]
    for cat, cnt in list(s["categories"].items())[:15]:
        lines.append(f"    {cat:12s} {cnt:>8,}")

    return ExecuteResponse(output="\n".join(lines))


def _cmd_sync(raw: str) -> ExecuteResponse:
    start = time.time()
    parts = shlex.split(raw) if raw else []
    days = 7
    categories = ["cs.AI", "cs.LG", "cs.CL", "cs.CV"]
    i = 0
    while i < len(parts):
        if parts[i] in ("-d", "--days") and i + 1 < len(parts):
            days = int(parts[i+1]); i += 2
        elif parts[i] in ("-c", "--category") and i + 1 < len(parts):
            categories = [parts[i+1]]; i += 2
        else:
            i += 1

    conn = db.get_conn()
    total = 0
    lines = []
    for cat in categories:
        try:
            papers = sources.fetch_recent(cat, days=days, max_results=100)
            if papers:
                sources.enrich_with_s2(papers)
                for p in papers:
                    db.upsert_paper(conn, p)
                conn.commit()
            total += len(papers)
            lines.append(f"  {cat}: {len(papers)} papers")
        except Exception as e:
            lines.append(f"  {cat}: failed — {e}")

    db.rebuild_fts(conn)
    lines.insert(0, f"  Synced {total} papers (last {days} days)\n")
    s = db.stats(conn)
    lines.append(f"\n  Database: {s['total_papers']:,} papers total")

    return ExecuteResponse(output="\n".join(lines), elapsed_ms=_ms_since(start))


def _cmd_from(raw: str) -> ExecuteResponse:
    parts = shlex.split(raw) if raw else []
    if len(parts) < 2:
        return ExecuteResponse(output="Usage: aipaper from <results_id> <command> [args...]")

    rid = parts[0]
    subcmd = parts[1]
    subraw = shlex.join(parts[2:]) if len(parts) > 2 else ""

    if subcmd == "grep":
        return _cmd_grep(f"{subraw} --from {rid}")
    elif subcmd == "read":
        conn = db.get_conn()
        papers = db.get_results(conn, rid)
        return ExecuteResponse(output=_fmt_results(papers, rid, show_abstract=True))
    elif subcmd == "map":
        return _cmd_map(f"{subraw} --from {rid}")
    else:
        return ExecuteResponse(output=f"Unknown subcommand for 'from': {subcmd}")


def _cmd_map(raw: str) -> ExecuteResponse:
    """LLM-powered parallel analysis across multiple papers.

    Each paper is read by Claude which produces a structured answer to the query.
    Falls back to keyword heuristic if anthropic SDK is unavailable.
    """
    start = time.time()
    parts = shlex.split(raw) if raw else []

    query_parts = []
    from_rid = None
    limit = 10
    use_llm = HAS_ANTHROPIC
    i = 0
    while i < len(parts):
        if parts[i] == "--from" and i + 1 < len(parts):
            from_rid = parts[i+1]; i += 2
        elif parts[i] in ("-n", "--limit") and i + 1 < len(parts):
            limit = int(parts[i+1]); i += 2
        elif parts[i] == "--no-llm":
            use_llm = False; i += 1
        elif not parts[i].startswith("-"):
            query_parts.append(parts[i]); i += 1
        else:
            i += 1

    query = " ".join(query_parts)
    if not query:
        return ExecuteResponse(output="Usage: aipaper map \"<question>\" [--from <results_id>] [-n N] [--no-llm]")

    conn = db.get_conn()

    if from_rid:
        papers = db.get_results(conn, from_rid)
    else:
        last_results = conn.execute(
            "SELECT DISTINCT results_id FROM results ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if last_results:
            papers = db.get_results(conn, last_results["results_id"])
        else:
            return ExecuteResponse(output="No result set to map over. Run a search first, or use --from <results_id>.")

    papers = papers[:limit]
    if not papers:
        return ExecuteResponse(output="No papers in result set.")

    def _get_paper_content(paper: dict) -> str:
        aid = paper["arxiv_id"]
        try:
            d = _ensure_paper(aid)
            content_path = d / "content.lines"
            if content_path.exists():
                text = content_path.read_text()
                lines = text.split("\n")
                cleaned = "\n".join(re.sub(r"^L\d+:\s*", "", l) for l in lines[:300])
                return cleaned[:12000]
        except Exception:
            pass
        return paper.get("abstract", "")

    def _analyze_llm(paper: dict) -> dict:
        aid = paper["arxiv_id"]
        result = {"arxiv_id": aid, "title": shorten(paper.get("title", ""), 60, placeholder="...")}
        try:
            content = _get_paper_content(paper)
            client = _get_anthropic_client()
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                messages=[{
                    "role": "user",
                    "content": f"Paper: {paper.get('title','')}\n\n{content}\n\nQuestion: {query}\n\nAnswer in 1-2 concise sentences. Be specific about methods, results, or claims."
                }]
            )
            result["answer"] = resp.content[0].text
            result["status"] = "ok"
        except Exception as e:
            result["answer"] = f"Error: {e}"
            result["status"] = "error"
        return result

    def _analyze_keyword(paper: dict) -> dict:
        aid = paper["arxiv_id"]
        result = {"arxiv_id": aid, "title": shorten(paper.get("title", ""), 60, placeholder="...")}
        try:
            content = _get_paper_content(paper)
            query_words = re.findall(r"\w+", query.lower())
            scored = []
            for line in content.split("\n"):
                hits = sum(1 for w in query_words if w in line.lower())
                if hits > 0:
                    scored.append((hits, line))
            scored.sort(key=lambda x: x[0], reverse=True)
            relevant = [line.strip()[:100] for _, line in scored[:5]]
            result["answer"] = " | ".join(relevant) if relevant else shorten(paper.get("abstract", ""), 200, placeholder="...")
            result["status"] = "ok"
        except Exception as e:
            result["answer"] = f"Error: {e}"
            result["status"] = "error"
        return result

    analyzer = _analyze_llm if use_llm else _analyze_keyword
    mode = "LLM (Claude)" if use_llm else "keyword"

    results = []
    with ThreadPoolExecutor(max_workers=6 if use_llm else 8) as pool:
        futures = {pool.submit(analyzer, p): p for p in papers}
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda r: next(
        (i for i, p in enumerate(papers) if p["arxiv_id"] == r["arxiv_id"]), 999
    ))

    lines = [f"  map \"{query}\" across {len(papers)} papers [{mode}]\n"]
    for i, r in enumerate(results):
        title = r.get("title", "")[:60]
        answer = r.get("answer", "")
        lines.append(f"  {i+1}. {r['arxiv_id']}  {title}")
        for answer_line in textwrap.wrap(answer, width=90):
            lines.append(f"     {answer_line}")
        lines.append("")

    rid = _rid()
    db.save_results(conn, rid, [r["arxiv_id"] for r in results])

    lines.append(f"  [{_ms_since(start)}ms, saved to {rid}]")
    return ExecuteResponse(output="\n".join(lines), result_id=rid, elapsed_ms=_ms_since(start))


def _cmd_results(raw: str) -> ExecuteResponse:
    parts = shlex.split(raw) if raw else []
    conn = db.get_conn()

    if parts and parts[0] == "--list":
        rows = conn.execute("""
            SELECT results_id, COUNT(*) as cnt, MIN(created_at) as created
            FROM results GROUP BY results_id ORDER BY created DESC LIMIT 20
        """).fetchall()
        lines = ["  Recent result sets:\n"]
        for r in rows:
            lines.append(f"  {r['results_id']}  ({r['cnt']} papers)  {r['created'][:16]}")
        return ExecuteResponse(output="\n".join(lines))

    if parts:
        rid = parts[0]
        papers = db.get_results(conn, rid)
        if not papers:
            return ExecuteResponse(output=f"Result set {rid} not found")
        return ExecuteResponse(output=_fmt_results(papers, rid, show_abstract=True))

    return ExecuteResponse(output="Usage: aipaper results [--list | <results_id>]")


def _cmd_bash(raw: str) -> ExecuteResponse:
    """Execute shell commands on the paper virtual filesystem.

    Supports pipes, sed, awk, sort, cut, tr, jq, grep, wc, etc.
    Working directory is the papers root. Paper paths are auto-resolved.
    """
    start = time.time()
    cmd = raw.strip().strip("'\"")
    if not cmd:
        return ExecuteResponse(output="Usage: aipaper bash '<command>'\n\n"
            "  Examples:\n"
            "    aipaper bash 'cat 2401.12345/content.lines | grep -i attention | wc -l'\n"
            "    aipaper bash 'jq .title 2401.12345/meta.json'\n"
            "    aipaper bash 'cat 2401.12345/content.lines | sed \"s/attention/ATTENTION/g\" | head -20'\n"
            "    aipaper bash 'sort -t: -k2 -rn 2401.12345/content.lines | head -10'")

    paper_ids = set(re.findall(r'(\d{4}\.\d{4,5})', cmd))
    for aid in paper_ids:
        try:
            _ensure_paper(aid)
        except Exception:
            pass

    cmd = re.sub(r'/papers/([^\s/]+)', lambda m: str(CACHE_DIR / m.group(1)), cmd)

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=30, cwd=str(CACHE_DIR)
        )
        output = result.stdout
        if result.returncode != 0 and result.stderr:
            output += f"\n[stderr] {result.stderr.strip()}"
        if not output.strip():
            output = "(no output)"
        return ExecuteResponse(output=output.rstrip(), elapsed_ms=_ms_since(start))
    except subprocess.TimeoutExpired:
        return ExecuteResponse(output="Command timed out (30s limit)")
    except Exception as e:
        return ExecuteResponse(output=f"Error: {e}")


def _cmd_ask_image(raw: str) -> ExecuteResponse:
    """Analyze a paper figure using Claude's vision capability.

    Fetches the figure image from arXiv HTML and sends it to Claude
    for visual analysis with the user's question.
    """
    start = time.time()
    parts = shlex.split(raw) if raw else []

    if len(parts) < 2:
        return ExecuteResponse(output="Usage: aipaper ask-image <arxiv_id> \"<question>\" [--fig N]\n\n"
            "  Examples:\n"
            "    aipaper ask-image 2401.12345 \"What does this architecture diagram show?\"\n"
            "    aipaper ask-image 2401.12345 \"Describe the results\" --fig 3")

    if not HAS_ANTHROPIC:
        return ExecuteResponse(output="Error: anthropic SDK required. Install with: pip install anthropic")

    arxiv_id = parts[0]
    query_parts = []
    fig_num = 1
    i = 1
    while i < len(parts):
        if parts[i] == "--fig" and i + 1 < len(parts):
            fig_num = int(parts[i+1]); i += 2
        elif not parts[i].startswith("-"):
            query_parts.append(parts[i]); i += 1
        else:
            i += 1

    question = " ".join(query_parts)
    if not question:
        question = "Describe what this figure shows in detail."

    _ensure_paper(arxiv_id)
    d = filesystem.paper_dir(arxiv_id)
    fig_index = d / "figures" / "index.json"

    if not fig_index.exists() or json.loads(fig_index.read_text()) == []:
        return ExecuteResponse(output=f"No figures found for {arxiv_id}. Try a paper with HTML source.")

    figures = json.loads(fig_index.read_text())

    target_fig = None
    for fig in figures:
        if fig.get("figure_number") == fig_num:
            target_fig = fig
            break
    if not target_fig and figures:
        target_fig = figures[0]
        fig_num = target_fig.get("figure_number", 1)

    if not target_fig:
        return ExecuteResponse(output=f"Figure {fig_num} not found. Available: {[f['figure_number'] for f in figures]}")

    fig_url = target_fig.get("url")
    if not fig_url:
        return ExecuteResponse(output=f"No image URL for figure {fig_num}. Caption: {target_fig.get('caption', 'N/A')}")

    try:
        img_resp = http_requests.get(fig_url, timeout=15)
        img_resp.raise_for_status()
        img_data = base64.b64encode(img_resp.content).decode("utf-8")

        content_type = img_resp.headers.get("content-type", "image/png")
        if "jpeg" in content_type or "jpg" in content_type:
            media_type = "image/jpeg"
        elif "gif" in content_type:
            media_type = "image/gif"
        elif "webp" in content_type:
            media_type = "image/webp"
        else:
            media_type = "image/png"
    except Exception as e:
        return ExecuteResponse(output=f"Failed to fetch figure image: {e}\nURL: {fig_url}")

    try:
        conn_row = db.get_conn().execute("SELECT title FROM papers WHERE arxiv_id = ?", (arxiv_id,)).fetchone()
        paper_title = conn_row["title"] if conn_row else arxiv_id

        client = _get_anthropic_client()
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_data}},
                    {"type": "text", "text": f"This is Figure {fig_num} from the paper \"{paper_title}\".\n"
                        f"Caption: {target_fig.get('caption', 'N/A')}\n\n"
                        f"Question: {question}"}
                ]
            }]
        )

        analysis = resp.content[0].text
        lines = [
            f"  Figure {fig_num} from {arxiv_id}",
            f"  Caption: {shorten(target_fig.get('caption', 'N/A'), 100, placeholder='...')}",
            f"  URL: {fig_url}",
            f"  {'─' * 60}",
            "",
        ]
        for line in analysis.split("\n"):
            lines.append(f"  {line}")

        return ExecuteResponse(output="\n".join(lines), elapsed_ms=_ms_since(start))

    except Exception as e:
        return ExecuteResponse(output=f"Vision analysis failed: {e}")


# ── Command Router ───────────────────────────────────────────────

COMMANDS = {
    "search": _cmd_search,
    "grep": _cmd_grep,
    "cat": _cmd_cat,
    "head": _cmd_head,
    "tail": _cmd_tail,
    "ls": _cmd_ls,
    "scan": _cmd_scan,
    "read": _cmd_read,
    "sql": _cmd_sql,
    "related": _cmd_related,
    "compare": _cmd_compare,
    "trending": _cmd_trending,
    "stats": _cmd_stats,
    "sync": _cmd_sync,
    "from": _cmd_from,
    "map": _cmd_map,
    "results": _cmd_results,
    "bash": _cmd_bash,
    "ask-image": _cmd_ask_image,
}


# ── Background tasks ────────────────────────────────────────────

def _background_sync(interval: int = 3600):
    while True:
        try:
            conn = db.get_conn()
            for cat in ["cs.AI", "cs.LG", "cs.CL", "cs.CV"]:
                try:
                    papers = sources.fetch_recent(cat, days=3, max_results=50)
                    if papers:
                        sources.enrich_with_s2(papers)
                        for p in papers:
                            db.upsert_paper(conn, p)
                        conn.commit()
                except Exception:
                    pass
                time.sleep(2)
            db.rebuild_fts(conn)
        except Exception:
            pass
        time.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    t = threading.Thread(target=_background_sync, daemon=True)
    t.start()
    yield


# ── FastAPI App ──────────────────────────────────────────────────

app = FastAPI(
    title="AI Paperclip",
    description="Server for AI/ML research papers — hybrid search, trigram grep, virtual filesystem, map",
    version=__version__,
    lifespan=lifespan,
)


@app.post("/api/cli/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest):
    handler = COMMANDS.get(req.command)
    if not handler:
        return ExecuteResponse(output=f"Unknown command: {req.command}\nAvailable: {', '.join(sorted(COMMANDS.keys()))}")
    try:
        return handler(req.raw)
    except Exception as e:
        return ExecuteResponse(output=f"Error: {e}\n{traceback.format_exc()}")


@app.get("/health")
async def health():
    conn = db.get_conn()
    s = db.stats(conn)
    tconn = trigram.get_conn()
    ts = trigram.stats(tconn)
    return {
        "status": "ok",
        "version": __version__,
        "indexed_papers": s["total_papers"],
        "pulled_papers": len(list(CACHE_DIR.iterdir())) if CACHE_DIR.exists() else 0,
        "trigram_indexed": ts["indexed_papers"],
    }
