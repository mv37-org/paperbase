"""Benchmark: code_url quality, old (PR #45 first-match-wins) vs new (#46
3-layer admin > PWC > ranker), against the 10-paper landmark fixture from
issue #46.

Reads paper text from the local ``.paperbase-data/papers/arxiv:{id}/`` tree
so the benchmark is fully offline EXCEPT for an optional PWC layer-1 probe
(``--with-pwc``).

Run from repo root::

    .venv/bin/python benchmarks/code_url_benchmark.py
    .venv/bin/python benchmarks/code_url_benchmark.py --with-pwc
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Make ``paperbase`` importable when the script is run with the repo root cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "paperbase" / "src"))

from paperbase.extractors import (  # noqa: E402
    extract_github_candidates,
    rank_github_candidates,
)


# Ground truth (canonical repo per paper, ``None`` = no canonical official
# code released by the authors). Lowercased for case-insensitive match —
# GitHub repo paths are case-insensitive on the server.
#
# The first 10 are the issue #46 landmark fixture. The next 20 are
# additional well-known ML papers picked for varied difficulty: clear
# single-repo cases, multi-affiliation papers (FAIR, Google, OpenAI),
# and papers that genuinely have no official repo (commonly community
# reimpls only) — useful for stress-testing the precision-first rule.
GROUND_TRUTH: dict[str, str | None] = {
    # ---------- issue #46 landmark fixture (10) ----------
    "1706.03762": "tensorflow/tensor2tensor",       # Attention Is All You Need
    "1810.04805": "google-research/bert",           # BERT
    "2005.14165": "openai/gpt-3",                   # GPT-3
    "2010.11929": "google-research/vision_transformer",  # ViT
    "2106.09685": "microsoft/lora",                 # LoRA
    "2201.11903": None,                             # CoT — no official code
    "2302.13971": "facebookresearch/llama",         # LLaMA
    "2305.10601": "princeton-nlp/tree-of-thought-llm",   # Tree of Thoughts
    "2307.09288": "facebookresearch/llama",         # Llama 2
    "1512.03385": None,                             # ResNet — community reimpls only
    # ---------- additional 20 well-known ML papers ----------
    "1506.02640": "pjreddie/darknet",               # YOLO v1
    "1611.07004": "phillipi/pix2pix",               # pix2pix
    "1703.06870": "facebookresearch/detectron",     # Mask R-CNN
    "1707.06347": "openai/baselines",               # PPO
    "1709.01507": "hujie-frank/senet",              # Squeeze-and-Excitation
    "1611.05431": "facebookresearch/resnext",       # ResNeXt
    "2002.05709": "google-research/simclr",         # SimCLR
    "2103.00020": "openai/clip",                    # CLIP
    "2105.04906": "facebookresearch/barlowtwins",   # Barlow Twins
    "2111.06377": "facebookresearch/mae",           # Masked Autoencoders (MAE)
    "2006.11239": "hojonathanho/diffusion",         # DDPM
    "2010.02502": "ermongroup/ddim",                # DDIM
    "1606.03657": "openai/infogan",                 # InfoGAN
    "1801.07829": "wangyueft/dgcnn",                # Dynamic Graph CNN
    "1706.02216": "williamleif/graphsage",          # GraphSAGE
    "1502.03167": None,                             # Batch Norm — no canonical
    "1503.02531": None,                             # Knowledge Distillation — no canonical
    "1409.0473": None,                              # Bahdanau attention — no canonical
    "1505.04597": None,                             # U-Net — no canonical official
    "1502.04623": None,                             # DRAW — no canonical
}


# --------------------------------------------------------------------------- #
# Old extractor (PR #45 first-match-wins, before #46 review fix)
# --------------------------------------------------------------------------- #


_LEGACY_RE = re.compile(
    r"https?://github\.com/[\w\.\-]+/[\w\.\-]+(?:/[\w\.\-/#?=&]+)?",
    re.IGNORECASE,
)
_LEGACY_TRAILING_JUNK = ".,;:)\"'"


def legacy_extract_github_url(*texts: str) -> str | None:
    """Verbatim copy of the PR #45 first-match-wins implementation, kept
    here so the benchmark can compare honestly against the post-#46
    behavior. Mirrors the body of ``extract_github_url`` from commit
    3720fe4 / 82bda56."""
    for text in texts:
        if not text:
            continue
        match = _LEGACY_RE.search(text)
        if not match:
            continue
        url = match.group(0)
        while url and url[-1] in _LEGACY_TRAILING_JUNK:
            url = url[:-1]
        if url.endswith(".git"):
            url = url[:-4]
        return url
    return None


# --------------------------------------------------------------------------- #
# Comparison logic
# --------------------------------------------------------------------------- #


def _paper_dir(arxiv_id: str) -> Path:
    return _REPO_ROOT / ".paperbase-data" / "papers" / f"arxiv:{arxiv_id}"


def _load_inputs(arxiv_id: str) -> tuple[str, str, str] | None:
    paper_dir = _paper_dir(arxiv_id)
    if not paper_dir.exists():
        return None
    meta_path = paper_dir / "meta.json"
    body_path = paper_dir / "text" / "full.md"
    abstract = ""
    authors = ""
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            abstract = meta.get("abstract") or ""
            authors = meta.get("authors") or ""
        except json.JSONDecodeError:
            pass
    body = body_path.read_text(encoding="utf-8") if body_path.exists() else ""
    return abstract, body, authors


def _bench_cache_dir() -> Path:
    """On-disk cache for paper text fetched live so the benchmark stays
    repeatable without re-hitting arxiv. Lives outside the indexed corpus
    so the indexer doesn't pick it up."""
    cache = _REPO_ROOT / ".paperbase-data" / "_benchmark_cache"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _fetch_live(arxiv_id: str) -> tuple[str, str, str] | None:
    """Fetch abstract + body markdown directly from arxiv when the local
    indexed corpus does not have the paper. Caches the result under
    ``.paperbase-data/_benchmark_cache/`` so re-runs are offline.

    Uses the same ``fetch_html``/``_clean_html_to_markdown`` plumbing the
    indexer relies on for HTML papers; falls back to the LaTeX source
    extractor if the HTML view isn't available. Abstract + authors come
    from arxiv's atom feed via ``sources.get_paper_details``.
    """
    cache_dir = _bench_cache_dir() / arxiv_id
    meta_path = cache_dir / "meta.json"
    body_path = cache_dir / "body.md"
    if meta_path.exists() and body_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return (
            meta.get("abstract") or "",
            body_path.read_text(encoding="utf-8"),
            meta.get("authors") or "",
        )

    # Lazy imports — keep the offline path import-cheap.
    from paperbase import sources
    from paperbase.extractors import (
        _clean_html_to_markdown,
        _extract_latex_text,
        _latex_to_markdown,
        fetch_html,
        fetch_source,
    )

    paper = sources.get_paper_details(arxiv_id)
    if paper is None:
        return None
    abstract = paper.abstract or ""
    authors = paper.authors or ""

    body = ""
    html = fetch_html(arxiv_id)
    if html:
        body = _clean_html_to_markdown(html)
    if not body:
        source_bytes = fetch_source(arxiv_id)
        if source_bytes:
            latex_text = _extract_latex_text(source_bytes)
            if latex_text:
                body = _latex_to_markdown(latex_text)

    cache_dir.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps({"abstract": abstract, "authors": authors}, indent=2),
        encoding="utf-8",
    )
    body_path.write_text(body, encoding="utf-8")
    return abstract, body, authors


def _normalize_repo(url: str | None) -> str | None:
    """Reduce a candidate URL to its ``owner/repo`` slug for comparison
    against the ground truth, lowercased + ``.git`` stripped."""
    if not url:
        return None
    cleaned = url.strip()
    cleaned = re.sub(r"^https?://(www\.)?github\.com/", "", cleaned)
    cleaned = cleaned.removesuffix(".git").rstrip("/")
    parts = cleaned.split("/")
    if len(parts) >= 2:
        return f"{parts[0].lower()}/{parts[1].lower()}"
    return cleaned.lower() or None


@dataclass
class Verdict:
    arxiv_id: str
    truth: str | None
    legacy: str | None
    new_ranker: str | None
    new_pwc: str | None  # populated only when --with-pwc
    new_verified: str | None = None  # populated only when --with-verifier

    @staticmethod
    def _classify(picked: str | None, truth: str | None) -> str:
        norm = _normalize_repo(picked)
        if truth is None:
            return "✓ None" if norm is None else "✗ wrong (should be None)"
        if norm == truth:
            return "✓ correct"
        if norm is None:
            return "○ None (truth populated)"
        return f"✗ wrong"

    def legacy_status(self) -> str:
        return self._classify(self.legacy, self.truth)

    def new_ranker_status(self) -> str:
        return self._classify(self.new_ranker, self.truth)

    def new_pwc_status(self) -> str:
        return self._classify(self.new_pwc, self.truth)

    def new_verified_status(self) -> str:
        return self._classify(self.new_verified, self.truth)


def _score(rows: list[Verdict], picker) -> dict[str, int]:
    correct = 0
    wrong = 0
    correct_none = 0
    none_when_truth_populated = 0
    for row in rows:
        norm = _normalize_repo(picker(row))
        if row.truth is None:
            if norm is None:
                correct_none += 1
            else:
                wrong += 1
        else:
            if norm == row.truth:
                correct += 1
            elif norm is None:
                none_when_truth_populated += 1
            else:
                wrong += 1
    return {
        "correct_populated": correct,
        "correct_none": correct_none,
        "wrong": wrong,
        "none_when_truth_populated": none_when_truth_populated,
        "total": len(rows),
    }


def _print_summary(label: str, scores: dict[str, int]) -> None:
    total = scores["total"]
    correct_total = scores["correct_populated"] + scores["correct_none"]
    print(f"\n{label}")
    print(f"  Strict precision (exact match):     {scores['correct_populated']}/{total}")
    print(f"  Correct-or-None (overall accuracy): {correct_total}/{total}")
    print(f"  Wrong:                              {scores['wrong']}/{total}")
    print(f"  None (where truth was populated):   {scores['none_when_truth_populated']}/{total}")


def _print_table(rows: list[Verdict], with_pwc: bool) -> None:
    header = f"{'arxiv_id':<12}  {'legacy':<46}  {'new(ranker)':<46}"
    if with_pwc:
        header += f"  {'new(+pwc)':<46}"
    header += f"  {'truth':<40}"
    print(header)
    print("-" * len(header))
    for row in rows:
        legacy = (row.legacy or "—")[:44]
        ranker = (row.new_ranker or "—")[:44]
        truth = (row.truth or "(None)")[:38]
        line = f"{row.arxiv_id:<12}  {legacy:<46}  {ranker:<46}"
        if with_pwc:
            pwc = (row.new_pwc or "—")[:44]
            line += f"  {pwc:<46}"
        line += f"  {truth:<40}"
        print(line)
        statuses = f"  └─ legacy: {row.legacy_status()}; ranker: {row.new_ranker_status()}"
        if with_pwc:
            statuses += f"; +pwc: {row.new_pwc_status()}"
        print(statuses)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-pwc",
        action="store_true",
        help="Probe Papers With Code for layer-1 results (real network call).",
    )
    parser.add_argument(
        "--fetch-missing",
        action="store_true",
        help=(
            "Fetch paper text from arXiv when missing from the local indexed "
            "corpus. Caches results under .paperbase-data/_benchmark_cache/ "
            "so re-runs stay offline."
        ),
    )
    parser.add_argument(
        "--with-verifier",
        action="store_true",
        help=(
            "Verify the ranker's pick by fetching the candidate repo's README "
            "from GitHub and checking it references the arxiv id / paper "
            "title. Demotes unverified picks to None — precision filter."
        ),
    )
    args = parser.parse_args(argv)

    rows: list[Verdict] = []
    pwc_lookup = None
    verifier = None
    github_token = os.environ.get("PAPERBASE_GITHUB_TOKEN") or os.environ.get(
        "GITHUB_TOKEN"
    )
    if args.with_pwc:
        from paperbase import sources

        pwc_lookup = sources.fetch_papers_with_code_repo
    if args.with_verifier:
        from paperbase import sources

        verifier = sources.verify_repo_owns_paper

    for arxiv_id, truth in GROUND_TRUTH.items():
        loaded = _load_inputs(arxiv_id)
        if loaded is None and args.fetch_missing:
            print(f"[fetch] {arxiv_id}: pulling from arxiv...", flush=True)
            loaded = _fetch_live(arxiv_id)
        if loaded is None:
            print(f"[skip] {arxiv_id}: no local data")
            continue
        abstract, body, authors = loaded
        legacy = legacy_extract_github_url(abstract, body)
        candidates = extract_github_candidates(abstract, body)
        new_ranker = rank_github_candidates(
            candidates, abstract=abstract, body=body, authors=authors
        )
        new_pwc: str | None = None
        if pwc_lookup is not None:
            try:
                new_pwc = pwc_lookup(arxiv_id)
            except Exception as exc:  # pragma: no cover — network flake
                new_pwc = f"<error: {type(exc).__name__}>"
            if new_pwc is None and new_ranker is not None:
                # Fall back to ranker when PWC has no official entry — the
                # production decision flow does the same.
                new_pwc = new_ranker.url

        # Verifier post-filter: take the ranker's pick and confirm via
        # GitHub README that the repo actually references the paper.
        # Demote to None when the README check fails (404 / no arxiv id /
        # no title match). Same input, different precision.
        new_verified: str | None = None
        if verifier is not None and new_ranker is not None:
            try:
                ok = verifier(
                    new_ranker.owner,
                    new_ranker.repo,
                    arxiv_id,
                    token=github_token,
                )
            except Exception:
                ok = False
            if ok:
                new_verified = new_ranker.url
        rows.append(
            Verdict(
                arxiv_id=arxiv_id,
                truth=truth,
                legacy=legacy,
                new_ranker=new_ranker.url if new_ranker else None,
                new_pwc=new_pwc,
                new_verified=new_verified,
            )
        )

    if not rows:
        print("No papers with local data — run `paperbase-dev index` first.")
        return 1

    _print_table(rows, with_pwc=args.with_pwc)

    legacy_scores = _score(rows, lambda r: r.legacy)
    ranker_scores = _score(rows, lambda r: r.new_ranker)
    _print_summary("Legacy (PR #45 first-match-wins)", legacy_scores)
    _print_summary("New ranker only (no PWC)", ranker_scores)
    if args.with_pwc:
        pwc_scores = _score(rows, lambda r: r.new_pwc)
        _print_summary("New: PWC + ranker fallback", pwc_scores)
    if args.with_verifier:
        verified_scores = _score(rows, lambda r: r.new_verified)
        _print_summary("New: ranker + GitHub README verifier", verified_scores)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
