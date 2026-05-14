"""Extraction pipeline for converting papers into normalized artifacts."""

from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path
import tarfile
import tempfile
from dataclasses import dataclass, field
from textwrap import shorten
from typing import NamedTuple

import requests
from bs4 import BeautifulSoup

from . import code_ingest
from .models import PaperMetadata
from .preview import generate_first_page_preview
from .utils import csv_bytes, make_slug, slugify

PREVIEW_ARTIFACT_PATH = "preview/first-page-top.webp"

# GitHub URL detection for populating ``papers.code_url`` from paper text
# (abstract / body). Covers the common in-text form where authors write
# "code is available at https://github.com/owner/repo" or trailing the
# url with a period / paren / quote. ``code_ingest.parse_github_url``
# does the canonical normalization downstream so we can be lenient here.
_GITHUB_URL_RE = re.compile(
    r"https?://github\.com/[\w\.\-]+/[\w\.\-]+(?:/[\w\.\-/#?=&]+)?",
    re.IGNORECASE,
)
# Punctuation that prose commonly pastes onto the end of a URL.
_GITHUB_URL_TRAILING_JUNK = ".,;:)\"'"

# Body-text mash-up suffixes we strip from the captured repo slug.
# Arxiv HTML→markdown extraction occasionally drops the whitespace
# between a URL and the prose that follows, so ``...openai/gpt-3``
# ``Supplemental Material`` renders as ``openai/gpt-3supplemental``
# and the regex captures the whole mash-up. The regex itself can't
# tell where the URL ends, so we post-process by stripping known
# noise tokens from the trailing edge of the repo slug.
#
# Two tiers, because the unconditional version of this rule was too
# aggressive and rewrote legitimate slugs like ``interpretable`` /
# ``roundtable`` / ``release-notes`` (PR #45 review). The boundary
# tier requires the suffix to be preceded by a non-letter character
# (digit, ``-``, ``_``, ``.``) — a strong signal of prose-mash because
# real repo names that end in these words almost always do so as a
# continuous lowercase token (``interpretable``, ``roundtable``).
# The always tier covers ``http``/``https`` only, where any trailing
# instance is essentially never a repo name and is overwhelmingly a
# prose-paste artifact (``simclr`` + ``http://...`` mash).
# Order matters — longer suffixes first so ``supplementary`` doesn't
# get stripped to ``supplement`` before the longer match runs.
_REPO_SLUG_NOISE_SUFFIXES_ALWAYS: tuple[str, ...] = (
    "https",
    "http",
)
_REPO_SLUG_NOISE_SUFFIXES_REQUIRE_BOUNDARY: tuple[str, ...] = (
    "supplementary",
    "supplemental",
    "appendix",
    "material",
    "license",
    "readme",
    "section",
    "notes",
    "note",
    "figure",
    "fig",
    "table",
)


def _strip_noise_suffix(slug: str) -> str:
    """Strip body-text mash-up suffixes from a captured repo name.

    Catches the ``gpt-3supplemental`` / ``simclrhttp`` family of regex
    captures where extraction lost a whitespace boundary between the
    URL and the surrounding prose. Conservative by default — for
    suffixes that overlap with real English words (``table``, ``notes``,
    ``readme``, ...) only fires when the character before the suffix
    is a digit, which is the strongest signal of prose-paste because
    paper / model IDs (``gpt-3``, ``llama-2``, ``yolo-9``) overwhelmingly
    end in digits and prose words start with letters. Other separators
    (``-``, ``_``, ``.``) deliberately appear in real repo names like
    ``release-notes`` / ``repo_appendix`` so they don't qualify as
    mash-up evidence on their own. ``http`` / ``https`` are the only
    suffixes we strip unconditionally because they are essentially
    never legitimate trailing tokens in a repo name.
    """
    if not slug:
        return slug
    lower = slug.lower()
    for suffix in _REPO_SLUG_NOISE_SUFFIXES_ALWAYS:
        if lower.endswith(suffix) and len(slug) - len(suffix) >= 4:
            stripped = _trim_repo_slug(slug, suffix)
            if stripped is not None:
                return stripped
    for suffix in _REPO_SLUG_NOISE_SUFFIXES_REQUIRE_BOUNDARY:
        if not lower.endswith(suffix) or len(slug) - len(suffix) < 4:
            continue
        boundary_char = slug[-(len(suffix) + 1)]
        if not boundary_char.isdigit():
            # Letter or separator: assume legitimate slug (``interpretable``,
            # ``roundtable``, ``release-notes``, ``repo_appendix``).
            continue
        stripped = _trim_repo_slug(slug, suffix)
        if stripped is not None:
            return stripped
    return slug


def _trim_repo_slug(slug: str, suffix: str) -> str | None:
    """Drop ``suffix`` from the tail of ``slug`` and trim trailing
    separators (so ``gpt-3-`` / ``simclr_`` don't leak through). Returns
    ``None`` when the resulting prefix is shorter than 3 chars — that
    suggests the suffix was the dominant content and the original slug
    was probably legitimate after all."""
    stripped = slug[: -len(suffix)].rstrip("-_.")
    return stripped if len(stripped) >= 3 else None


@dataclass(frozen=True)
class GitHubUrlCandidate:
    """One normalized GitHub repo reference found in paper text (#46).

    ``url`` is the canonical ``https://github.com/{owner}/{repo}`` form,
    stripped of sub-paths (``/tree``, ``/blob``, ``/commit``) and query
    parameters — those are preserved as the ``has_subpath`` bit for the
    ranker to penalize as "less likely to be the paper's primary repo".
    """

    url: str
    owner: str
    repo: str
    has_subpath: bool = False


def extract_github_candidates(abstract: str, body: str) -> list[GitHubUrlCandidate]:
    """Return all valid, deduped GitHub repo candidates mentioned in the
    paper (#46 layer-2 input).

    Replaces the previous ``extract_github_url`` first-match-wins behavior,
    which misfired when a paper cited a dataset / baseline repo before
    mentioning its own (e.g. CoT's ``chaochun/nlu-asdiv-dataset``). The
    ranker downstream consumes this list plus the raw texts to pick the
    paper's primary repo or decline when ambiguous.

    Normalization goes through ``code_ingest.parse_github_url`` so the
    returned ``url`` field is always the canonical form — callers can
    persist it to ``papers.code_url`` directly without re-parsing.
    """
    seen: dict[str, GitHubUrlCandidate] = {}
    combined = f"{abstract or ''}\n{body or ''}"
    for match in _GITHUB_URL_RE.finditer(combined):
        raw = match.group(0)
        while raw and raw[-1] in _GITHUB_URL_TRAILING_JUNK:
            raw = raw[:-1]
        if raw.endswith(".git"):
            raw = raw[:-4]
        ref = code_ingest.parse_github_url(raw)
        if ref is None:
            continue
        # Repair body-text mash-ups like ``openai/gpt-3supplemental`` or
        # ``google-research/simclrhttp`` (regex grabbed the prose tail
        # because PDF→markdown lost a whitespace boundary). Only the
        # repo segment is repaired; owner names rarely mash with prose.
        repaired_repo = _strip_noise_suffix(ref.repo)
        if repaired_repo != ref.repo:
            from . import code_ingest as _ci

            ref = _ci.GitHubRepoRef(owner=ref.owner, repo=repaired_repo, ref=ref.ref)
        # GitHub repo paths are case-insensitive on the server, so ``Owner/Repo``
        # and ``owner/repo`` are the same resource. Dedupe on the lowercased
        # form but keep the canonical-cased string that the author used.
        key = f"{ref.owner.lower()}/{ref.repo.lower()}"
        canonical = f"https://github.com/{ref.owner}/{ref.repo}"
        had_subpath = bool(
            re.search(r"/(?:tree|blob|commit)/", raw) or "?" in raw or "#" in raw
        )
        previous = seen.get(key)
        if previous is None:
            seen[key] = GitHubUrlCandidate(
                url=canonical, owner=ref.owner, repo=ref.repo, has_subpath=had_subpath
            )
        elif previous.has_subpath and not had_subpath:
            # Upgrade the record when a later mention is the canonical form.
            seen[key] = GitHubUrlCandidate(
                url=canonical, owner=ref.owner, repo=ref.repo, has_subpath=False
            )
    return list(seen.values())


def rank_github_candidates(
    candidates: list[GitHubUrlCandidate],
    *,
    abstract: str,
    body: str,
    authors: str = "",
    min_margin: float = 0.2,
    tie_window: float = 0.05,
) -> GitHubUrlCandidate | None:
    """Pick the paper's primary repo from a candidate set, or ``None``
    when the signal is ambiguous (#46 layer 2).

    Precision-first, but with two carve-outs the original implementation
    didn't have:

    - **2-candidate tie tiebreaker.** Real ML papers cite many repos
      (BERT has 2, ViT 2, LLaMA 3) and the symmetric scoring often
      ties two candidates exactly. When exactly two candidates score
      within ``tie_window`` of the top, the one that appears earliest
      in the body wins — empirically the paper's own repo is mentioned
      first (in a page-1 footnote), with citations following later.
    - **3+ candidate tie still resolves to None.** When three or more
      candidates cluster at the top (e.g. CoT references 7 dataset
      repos at similar mention frequency), there is no honest
      tiebreaker and the precision-first rule wins.

    Scoring (cumulative):
    - ``+1.0`` canonical form (no ``/tree``, ``/blob``, ``?``, ``#``)
    - ``+1.5`` owner slug matches an author-affiliation token
      (``google-research``, ``facebookresearch``, ``microsoft``, …)
    - ``+1.0`` URL appears in abstract; ``+0.3`` body-only
    - ``+0.3`` per extra body mention beyond the first (cap ``+0.9``)

    ``min_margin=0.2`` is the post-tuning default — the original ``0.5``
    was calibrated against synthetic test fixtures and rejected real-
    paper cases like BERT (margin 0.30) where one candidate clearly
    leads but not by half a point. The 10-paper landmark benchmark
    demonstrates this margin.
    """
    if not candidates:
        return None
    author_tokens = _author_affinity_tokens(authors)
    abstract_lower = (abstract or "").lower()
    body_lower = (body or "").lower()
    scored: list[tuple[float, GitHubUrlCandidate]] = []
    for candidate in candidates:
        score = 0.0
        if not candidate.has_subpath:
            score += 1.0
        if _owner_matches_authors(candidate.owner, author_tokens):
            score += 1.5
        pattern = f"github.com/{candidate.owner.lower()}/{candidate.repo.lower()}"
        if pattern in abstract_lower:
            score += 1.0
        elif pattern in body_lower:
            score += 0.3
        body_mentions = body_lower.count(pattern)
        if body_mentions > 1:
            score += min(0.3 * (body_mentions - 1), 0.9)
        scored.append((score, candidate))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    top_score, top_candidate = scored[0]

    # Carve out the cluster of candidates that are effectively tied
    # with the top by ``tie_window``. A single-element cluster means
    # the top candidate is a clear leader; two means a 2-way tie that
    # we can break by first-mention; three+ means real ambiguity.
    tied_top = [
        candidate for score, candidate in scored if top_score - score <= tie_window
    ]
    if len(tied_top) == 1:
        if len(scored) >= 2:
            runner_up_score, _ = scored[1]
            if top_score - runner_up_score < min_margin:
                return None
        return top_candidate
    if len(tied_top) == 2:
        # Tiebreak by first appearance in the body. The paper's own
        # repo is empirically mentioned first (page-1 footnote);
        # cited / baseline repos come later in the references.
        positions: dict[str, int] = {}
        for candidate in tied_top:
            pattern = f"github.com/{candidate.owner.lower()}/{candidate.repo.lower()}"
            position = body_lower.find(pattern)
            # ``not found`` ⇒ push to the end of the ordering.
            positions[candidate.url] = position if position >= 0 else len(body_lower) + 1
        return min(tied_top, key=lambda candidate: positions[candidate.url])
    # 3+ candidates clustered at the top — genuinely ambiguous; the
    # admin override is the right escape hatch for these.
    return None


_AFFINITY_TOKEN_RE = re.compile(r"[a-z][a-z0-9\-]{2,}")


def _author_affinity_tokens(authors: str) -> set[str]:
    if not authors:
        return set()
    tokens: set[str] = set()
    for part in _AFFINITY_TOKEN_RE.findall(authors.lower()):
        if len(part) >= 4:
            tokens.add(part)
    return tokens


def _owner_matches_authors(owner: str, author_tokens: set[str]) -> bool:
    if not owner or not author_tokens:
        return False
    owner_parts = re.findall(r"[a-z]+", owner.lower())
    for part in owner_parts:
        if len(part) >= 4 and part in author_tokens:
            return True
    return False


def extract_github_url(*texts: str) -> str | None:
    """DEPRECATED shim for callers that predate #46's candidate ranker.

    Still used by service code that does not yet thread ``authors`` /
    separate abstract+body. Returns the winner of
    :func:`rank_github_candidates` on all accumulated text, or ``None``
    when ambiguous / empty.

    Prefer ``extract_github_candidates`` + ``rank_github_candidates`` in
    new code — the ranker's precision guarantees depend on separating
    abstract from body so the position bonus can fire.
    """
    joined_abstract = ""
    joined_body = "\n".join(text or "" for text in texts)
    candidates = extract_github_candidates(joined_abstract, joined_body)
    winner = rank_github_candidates(candidates, abstract=joined_abstract, body=joined_body)
    return winner.url if winner else None


logger = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "paperbase/0.1.0 (paper extractor)"})

_HTML_SECTION_CLASSES = {"ltx_section", "ltx_subsection", "ltx_subsubsection"}
_HTML_CONTENT_TAGS = ("p", "li", "figcaption", "caption")

_BARE_LATEX_SPACING_RE = re.compile(
    r"^\s*-?\d*\.?\d+(?:em|pt|ex|cm|in|mm|bp|pc|sp|mu)\s*$"
)
_PACKAGE_OPTS_RESIDUE_RE = re.compile(r"^\s*\[[^\]]*\][A-Za-z@]+\s*$")
_NEWCMD_RESIDUE_RE = re.compile(r"^\s*\[\d+\]#\d+.*$")
_LATEX_PREAMBLE_CMD_RE = re.compile(
    r"^\s*\\(?:documentclass|usepackage|RequirePackage|bibliographystyle|bibliography|"
    r"input|include|newcommand|renewcommand|providecommand|def|let|setlength|setcounter|"
    r"vspace|hspace|smallskip|medskip|bigskip|noindent|par|newpage|clearpage|"
    r"pagestyle|thispagestyle|hypersetup|graphicspath|maketitle)\b"
)
_LATEX_LEAKED_PACKAGE_TOKENS = frozenset(
    {
        "amsmath", "amssymb", "amsfonts", "amsthm", "graphicx", "graphics",
        "epsfig", "hyperref", "url", "geometry", "fontenc", "inputenc",
        "babel", "natbib", "biblatex", "color", "xcolor", "tikz", "pgfplots",
        "subcaption", "subfigure", "caption", "wrapfig", "float", "multirow",
        "multicol", "tabularx", "tabulary", "longtable", "array", "booktabs",
        "soul", "ulem", "cite", "times", "newtxtext", "newtxmath", "mathptmx",
        "helvet", "courier", "mathpazo", "lmodern", "cvpr", "iclr", "iclr2024",
        "iclr2025", "neurips", "neurips_2024", "icml", "icml2024", "aaai",
        "ijcai", "acl", "comment",
    }
)


def _sanitize_extracted_text(text: str) -> str:
    """Strip residual LaTeX preamble noise that escaped earlier extraction passes."""
    if not text:
        return text
    cleaned: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.lstrip()
        if not stripped:
            cleaned.append("")
            continue
        if stripped.startswith("%"):
            continue
        if _BARE_LATEX_SPACING_RE.match(line):
            continue
        if _PACKAGE_OPTS_RESIDUE_RE.match(line):
            continue
        if _NEWCMD_RESIDUE_RE.match(line):
            continue
        if _LATEX_PREAMBLE_CMD_RE.match(line):
            continue
        if stripped.lower() in _LATEX_LEAKED_PACKAGE_TOKENS:
            continue
        cleaned.append(line)
    out = "\n".join(cleaned)
    out = re.sub(r"\\([%&#$_])", r"\1", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


@dataclass(slots=True)
class RawArtifact:
    path: str
    data: bytes
    content_type: str | None = None


@dataclass(slots=True)
class SectionArtifact:
    title: str
    slug: str
    markdown: str


@dataclass(slots=True)
class FigureArtifact:
    figure_id: str
    caption: str
    file_extension: str
    content_type: str | None
    image_bytes: bytes | None = None
    source_url: str | None = None


@dataclass(slots=True)
class TableArtifact:
    table_id: str
    caption: str | None
    rows: list[list[str]]

    def json_payload(self) -> list[dict[str, str]]:
        if not self.rows:
            return []
        header = self.rows[0]
        records: list[dict[str, str]] = []
        for row in self.rows[1:]:
            padded = row + [""] * max(0, len(header) - len(row))
            records.append({header[index]: padded[index] for index in range(len(header))})
        return records


@dataclass(slots=True)
class ExtractionOutput:
    source_type: str
    text_markdown: str
    sections: list[SectionArtifact] = field(default_factory=list)
    figures: list[FigureArtifact] = field(default_factory=list)
    tables: list[TableArtifact] = field(default_factory=list)
    raw_artifacts: list[RawArtifact] = field(default_factory=list)


def fetch_html(arxiv_id: str) -> str | None:
    response = SESSION.get(f"https://arxiv.org/html/{arxiv_id}", timeout=30)
    if response.status_code != 200 or "<html" not in response.text[:1000].lower():
        return None
    if "ltx_section" not in response.text:
        return None
    return response.text


def fetch_source(arxiv_id: str) -> bytes | None:
    response = SESSION.get(
        f"https://arxiv.org/e-print/{arxiv_id}",
        timeout=30,
        headers={"Accept": "application/x-eprint-tar"},
    )
    if response.status_code == 200:
        return response.content
    return None


def fetch_pdf(arxiv_id: str, pdf_url: str | None = None) -> bytes | None:
    response = SESSION.get(pdf_url or f"https://arxiv.org/pdf/{arxiv_id}.pdf", timeout=30)
    if response.status_code == 200:
        return response.content
    return None


def _clean_html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [line.strip() for line in text.splitlines()]
    filtered = [line for line in lines if line]
    merged: list[str] = []
    for line in filtered:
        if not merged or len(line) < 3:
            merged.append(line)
        elif len(merged[-1]) < 60 and not merged[-1].endswith((".", ":", "!", "?")):
            merged[-1] += f" {line}"
        else:
            merged.append(line)
    return _sanitize_extracted_text("\n\n".join(merged))


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_section_title(value: str) -> str:
    title = _normalize_text(value)
    title = re.sub(r"^#{1,6}\s*", "", title)
    title = re.sub(r"^(?:\d+(?:\.\d+){0,3}\.?\s+)", "", title)
    return title.strip(" :") or "Section"


def _render_html_fragment(tag: object) -> str:
    blocks: list[str] = []
    if not hasattr(tag, "find_all"):
        return ""
    for block in tag.find_all(_HTML_CONTENT_TAGS):
        text = _normalize_text(block.get_text(" ", strip=True))
        if text:
            blocks.append(text)
    if blocks:
        return "\n\n".join(blocks)
    return _normalize_text(tag.get_text(" ", strip=True))


def _clone_html_tag(tag: object) -> object | None:
    soup = BeautifulSoup(str(tag), "html.parser")
    return soup.find() if hasattr(soup, "find") else None


def _is_html_section_tag(tag: object) -> bool:
    attrs = getattr(tag, "attrs", None)
    if not isinstance(attrs, dict):
        return False
    classes = set(attrs.get("class", []) or [])
    return bool(_HTML_SECTION_CLASSES & classes)


def _section_from_html_tag(tag: object, *, title: str, seen: set[str]) -> SectionArtifact | None:
    clone = _clone_html_tag(tag)
    if clone is None:
        return None
    for nested in list(clone.find_all("section")):
        if nested is clone:
            continue
        if _is_html_section_tag(nested):
            nested.decompose()
    heading = clone.find(re.compile(r"^h[1-6]$"))
    if heading is not None:
        heading.decompose()
    markdown = _sanitize_extracted_text(_render_html_fragment(clone))
    if not markdown:
        return None
    return SectionArtifact(title=title, slug=make_slug(title, seen), markdown=markdown)


def _extract_sections_from_html(html: str) -> list[SectionArtifact]:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article", class_=re.compile(r"\bltx_document\b")) or soup
    sections: list[SectionArtifact] = []
    seen: set[str] = set()

    abstract = article.find("div", class_=re.compile(r"\bltx_abstract\b"))
    if abstract is not None:
        abstract_section = _section_from_html_tag(abstract, title="Abstract", seen=seen)
        if abstract_section is not None:
            sections.append(abstract_section)

    for section_tag in article.find_all("section"):
        if not _is_html_section_tag(section_tag):
            continue
        heading = section_tag.find(re.compile(r"^h[1-6]$"), recursive=False)
        if heading is None:
            continue
        title = _normalize_section_title(heading.get_text(" ", strip=True))
        section = _section_from_html_tag(section_tag, title=title, seen=seen)
        if section is not None:
            sections.append(section)
    return sections


def _assemble_markdown_from_sections(paper: PaperMetadata, sections: list[SectionArtifact]) -> str:
    parts = [f"# {paper.title}"] if paper.title else []
    for section in sections:
        body = section.markdown.strip()
        if body:
            parts.append(f"## {section.title}\n\n{body}")
        else:
            parts.append(f"## {section.title}")
    return "\n\n".join(parts).strip()


_MARKDOWN_HEADER_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def _split_by_markdown_headers(markdown: str) -> list[SectionArtifact] | None:
    matches = list(_MARKDOWN_HEADER_RE.finditer(markdown))
    if not matches:
        return None
    sections: list[SectionArtifact] = []
    seen: set[str] = set()

    # Capture any text that appears before the first header (e.g. an unheaded
    # abstract or opening paragraph) as a synthetic "Preamble" section, so
    # that content is not silently dropped from indexing.
    preamble = markdown[: matches[0].start()].strip()

    # Drop a leading H1 only when its body is empty — i.e. the H1 is followed
    # immediately by another header with no prose in between. That is the
    # "paper title alone" shape (common in docling output). A mixed document
    # like `# Abstract\nWe propose...\n## Introduction` keeps the H1 because
    # its body has substantive content.
    start_index = 0
    if len(matches) > 1 and len(matches[0].group(1)) == 1:
        body = markdown[matches[0].end(): matches[1].start()].strip()
        if not body:
            start_index = 1

    if preamble:
        sections.append(
            SectionArtifact(
                title="Preamble",
                slug=make_slug("Preamble", seen),
                markdown=preamble,
            )
        )

    effective = matches[start_index:]
    if not effective:
        return sections or None
    for index, match in enumerate(effective):
        start = match.end()
        end = effective[index + 1].start() if index + 1 < len(effective) else len(markdown)
        title = _normalize_section_title(match.group(2)).title()
        sections.append(
            SectionArtifact(
                title=title,
                slug=make_slug(title, seen),
                markdown=markdown[start:end].strip(),
            )
        )
    return sections


def _extract_sections(markdown: str) -> list[SectionArtifact]:
    header_sections = _split_by_markdown_headers(markdown)
    if header_sections:
        return header_sections
    section_pattern = re.compile(
        r"^(?:#{1,6}\s*)?(?:\d+(?:\.\d+){0,3}\.?\s+)?(Abstract|Introduction|Related Work|Background|"
        r"Methodology|Method|Methods|Approach|Model|Architecture|"
        r"Experiments?|Results?|Evaluation|Analysis|Discussion|"
        r"Conclusion|Conclusions|Limitations|Future Work|"
        r"Acknowledgments?|Acknowledgements?|References|Appendix|"
        r"Supplementary|Training|Datasets?|Implementation|Setup|"
        r"Preliminaries|Experimental Setup|Experiments and Results|"
        r"Conclusion and Future Work|Materials and Methods|"
        r"Ablation|Broader Impact|Ethics)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    matches = list(section_pattern.finditer(markdown))
    if not matches:
        return [SectionArtifact(title="Full Text", slug="full-text", markdown=markdown)]
    sections: list[SectionArtifact] = []
    seen: set[str] = set()
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        title = _normalize_section_title(match.group(0)).title()
        sections.append(
            SectionArtifact(
                title=title,
                slug=make_slug(title, seen),
                markdown=markdown[start:end].strip(),
            )
        )
    return sections


def _download_image(url: str) -> tuple[bytes | None, str | None, str]:
    response = SESSION.get(url, timeout=30)
    if response.status_code != 200:
        return None, None, "bin"
    content_type = response.headers.get("content-type")
    extension = "png"
    if content_type:
        if "jpeg" in content_type or "jpg" in content_type:
            extension = "jpg"
        elif "svg" in content_type:
            extension = "svg"
        elif "gif" in content_type:
            extension = "gif"
        elif "webp" in content_type:
            extension = "webp"
    return response.content, content_type, extension


def _extract_figures_from_html(html: str, *, arxiv_id: str | None = None) -> list[FigureArtifact]:
    from urllib.parse import urljoin

    soup = BeautifulSoup(html, "html.parser")
    # arXiv's HTML comes in two shapes and the image URL pattern differs:
    #
    # 1. Pre-pathed (e.g. 1706.03762 Attention): NO ``<base>`` tag, and
    #    ``<img>`` srcs include the arxiv id + version —
    #    ``1706.03762v7/Figures/ModalNet-21.png``. Base must be
    #    ``https://arxiv.org/html/`` so urljoin produces the full URL.
    #
    # 2. Per-paper-base (e.g. 2106.09685 LoRA): a root-relative
    #    ``<base href="/html/<id>v<N>/">`` tag and bare ``<img>`` srcs
    #    like ``x1.png``. Qualifying the root-relative base against
    #    ``https://arxiv.org`` gives a full URL that correctly resolves
    #    bare srcs.
    #
    # Prior to this, a bare fallback of ``https://arxiv.org/html/`` was
    # used unconditionally which produced 404s on shape (2). An
    # arxiv-id-scoped fallback produced duplicated paths on shape (1).
    # Deriving the base from the ``<base>`` tag when present and using
    # ``/html/`` otherwise handles both.
    base_tag = soup.find("base", href=True)
    if base_tag and base_tag.get("href"):
        base_url = urljoin("https://arxiv.org/", base_tag["href"])
    else:
        base_url = "https://arxiv.org/html/"
    figures: list[FigureArtifact] = []
    for index, figure in enumerate(soup.find_all("figure"), start=1):
        image = figure.find("img")
        if image is None or not image.get("src"):
            continue
        raw_src = image["src"]
        # ``urljoin`` with an absolute base correctly handles:
        #   - full URLs (``http://...``) → passed through
        #   - root-relative (``/html/abs.png``) → joined against host
        #   - bare relative (``x1.png``) → joined against per-paper base
        source_url = urljoin(base_url, raw_src)
        caption_tag = figure.find("figcaption")
        caption = caption_tag.get_text(" ", strip=True) if caption_tag else ""
        image_bytes, content_type, extension = _download_image(source_url)
        figures.append(
            FigureArtifact(
                figure_id=f"figure-{index}",
                caption=caption[:1000],
                file_extension=extension,
                content_type=content_type,
                image_bytes=image_bytes,
                source_url=source_url,
            )
        )
    return figures


def _extract_tables_from_html(html: str) -> list[TableArtifact]:
    soup = BeautifulSoup(html, "html.parser")
    tables: list[TableArtifact] = []
    for index, table in enumerate(soup.find_all("table"), start=1):
        rows: list[list[str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if cells:
                rows.append([cell.get_text(" ", strip=True) for cell in cells])
        if not rows:
            continue
        caption_tag = table.find("caption")
        tables.append(
            TableArtifact(
                table_id=f"table-{index}",
                caption=caption_tag.get_text(" ", strip=True)[:1000] if caption_tag else None,
                rows=rows,
            )
        )
    return tables


_LATEX_TABLE_ENV_RE = re.compile(
    r"\\begin\{(table\*?)\}(.*?)\\end\{\1\}",
    re.DOTALL,
)
_LATEX_TABULAR_RE = re.compile(
    r"\\begin\{tabular\*?\}\s*(?:\{[^}]*\}\s*)*(.*?)\\end\{tabular\*?\}",
    re.DOTALL,
)
_LATEX_FIGURE_ENV_RE = re.compile(
    r"\\begin\{(figure\*?)\}(.*?)\\end\{\1\}",
    re.DOTALL,
)
_LATEX_CAPTION_RE = re.compile(r"\\caption\{((?:[^{}]|\{[^{}]*\})*)\}", re.DOTALL)
_LATEX_INCLUDEGRAPHICS_RE = re.compile(
    r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}"
)


def _strip_latex_inline(value: str) -> str:
    value = re.sub(r"\\(?:cite|citep|citet|ref|eqref|label)\{[^}]*\}", "", value)
    value = re.sub(r"\\(?:text(?:bf|it|tt|rm|sf)|emph|textsc)\{([^}]*)\}", r"\1", value)
    value = re.sub(r"\\[a-zA-Z]+\*?\{([^}]*)\}", r"\1", value)
    value = re.sub(r"\\[a-zA-Z]+\*?", "", value)
    value = value.replace("~", " ").replace("\\", " ")
    value = re.sub(r"[{}]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _parse_latex_tabular(tabular_body: str) -> list[list[str]]:
    body = re.sub(r"\\(?:hline|toprule|midrule|bottomrule|cmidrule(?:\[[^\]]*\])?(?:\{[^}]*\})?)", "", tabular_body)
    raw_rows = re.split(r"\\\\", body)
    rows: list[list[str]] = []
    for raw_row in raw_rows:
        if not raw_row.strip():
            continue
        cells = [_strip_latex_inline(cell) for cell in raw_row.split("&")]
        if any(cell for cell in cells):
            rows.append(cells)
    return rows


def _extract_latex_tables(latex: str) -> list[TableArtifact]:
    tables: list[TableArtifact] = []
    for index, match in enumerate(_LATEX_TABLE_ENV_RE.finditer(latex), start=1):
        body = match.group(2)
        tabular_match = _LATEX_TABULAR_RE.search(body)
        if tabular_match is None:
            continue
        rows = _parse_latex_tabular(tabular_match.group(1))
        if not rows:
            continue
        caption_match = _LATEX_CAPTION_RE.search(body)
        caption = _strip_latex_inline(caption_match.group(1)) if caption_match else None
        tables.append(
            TableArtifact(
                table_id=f"table-{index}",
                caption=caption[:1000] if caption else None,
                rows=rows,
            )
        )
    # Tables may also live outside a \begin{table} wrapper (raw tabular).
    # Only take those that weren't already captured above.
    seen_bodies = set()
    for match in _LATEX_TABLE_ENV_RE.finditer(latex):
        tabular_match = _LATEX_TABULAR_RE.search(match.group(2))
        if tabular_match:
            seen_bodies.add(tabular_match.group(1))
    offset = len(tables)
    for index, match in enumerate(_LATEX_TABULAR_RE.finditer(latex), start=1):
        body = match.group(1)
        if body in seen_bodies:
            continue
        rows = _parse_latex_tabular(body)
        if not rows:
            continue
        tables.append(
            TableArtifact(
                table_id=f"table-{offset + index}",
                caption=None,
                rows=rows,
            )
        )
    return tables


def _rasterize_pdf_to_png(pdf_bytes: bytes) -> bytes | None:
    """Render the first page of a single-figure PDF to PNG.

    arxiv LaTeX sources commonly ship figures as PDF (esp. tikz output).
    Browsers can't render PDF inline, so we rasterize at ingest using
    pypdfium2 (already in deps for the paper-preview path). Failures
    return ``None`` so the caller can fall back to storing raw PDF bytes.
    """
    try:
        import pypdfium2 as pdfium
        from PIL import Image  # noqa: F401  (registers PNG codec)
    except Exception:
        return None
    try:
        document = pdfium.PdfDocument(pdf_bytes)
        if len(document) == 0:
            return None
        # scale=2.0 doubles the rendered DPI relative to the PDF's
        # native resolution. arxiv figures shipped as single-page PDFs
        # are usually authored at print quality (~300 DPI); 2x keeps
        # text in figure annotations legible after the PNG conversion.
        # Matches the scale chosen for EPS rasterization below.
        pil_image = document[0].render(scale=2.0).to_pil()
        buffer = io.BytesIO()
        pil_image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    except Exception:
        return None


def _rasterize_eps_to_png(eps_bytes: bytes) -> bytes | None:
    """Best-effort EPS → PNG via Pillow.

    Pillow's EPS plugin shells out to Ghostscript at runtime; if Ghostscript
    is not installed (the default in our slim Docker image), the rasterize
    fails and the caller stores raw EPS bytes instead. Tracking adding
    Ghostscript to the image is out of scope for this PR — see #48.
    """
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(io.BytesIO(eps_bytes)) as eps_image:
            # scale=2 doubles Ghostscript's render DPI vs Pillow's
            # default. EPS figures are vector-only so doubling has
            # no quality cost at the source; 2x preserves detail in
            # axis labels and small annotations after the PNG
            # conversion. Matches the PDF rasterizer's scale choice.
            eps_image.load(scale=2)
            converted = eps_image.convert("RGBA") if eps_image.mode != "RGBA" else eps_image
            buffer = io.BytesIO()
            converted.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue()
    except Exception:
        return None


class ResolvedLatexImage(NamedTuple):
    """Output of ``_resolve_latex_image``. Named so the call site doesn't
    have to unpack a positional 3-tuple — the (bytes, type, ext) shape
    is non-obvious from the type alone."""

    image_bytes: bytes
    content_type: str | None
    file_extension: str


def _resolve_latex_image(
    asset_bytes: dict[str, bytes],
    source_path: str,
) -> ResolvedLatexImage | None:
    """Resolve a ``\\includegraphics{source_path}`` reference to image bytes
    from the LaTeX tarball.

    Returns a :class:`ResolvedLatexImage` or ``None`` when the path can't
    be resolved. Tries the bare path first, then each candidate
    extension; raster-friendly formats (PNG/JPG/GIF) pass through
    unchanged; PDF figures are rasterized to PNG; EPS attempts
    rasterize and falls back to raw bytes when Ghostscript is missing.
    """
    if not asset_bytes or not source_path:
        return None

    # Authors sometimes write ``./figs/foo`` or ``figs/foo`` interchangeably;
    # treat the leading ``./`` as a no-op so both forms hit the same lookup.
    cleaned = source_path[2:] if source_path.startswith("./") else source_path
    candidates: list[str] = [cleaned]
    if "." not in cleaned.rsplit("/", 1)[-1]:
        candidates.extend(f"{cleaned}{ext}" for ext in _LATEX_IMAGE_EXTENSIONS)

    matched_name: str | None = None
    matched_bytes: bytes | None = None
    for candidate in candidates:
        if candidate in asset_bytes:
            matched_name = candidate
            matched_bytes = asset_bytes[candidate]
            break
        # Fall back to suffix-match for tarballs whose member names carry a
        # leading directory the author didn't write (``main/figs/foo.eps``
        # vs ``\includegraphics{figs/foo.eps}``).
        for member_name in asset_bytes:
            if member_name == candidate or member_name.endswith("/" + candidate):
                matched_name = member_name
                matched_bytes = asset_bytes[member_name]
                break
        if matched_bytes is not None:
            break

    if matched_bytes is None or matched_name is None:
        return None

    extension = matched_name.rsplit(".", 1)[-1].lower() if "." in matched_name else ""
    if extension == "pdf":
        rasterized = _rasterize_pdf_to_png(matched_bytes)
        if rasterized is not None:
            return ResolvedLatexImage(rasterized, "image/png", "png")
        return ResolvedLatexImage(matched_bytes, "application/pdf", "pdf")
    if extension == "eps":
        rasterized = _rasterize_eps_to_png(matched_bytes)
        if rasterized is not None:
            return ResolvedLatexImage(rasterized, "image/png", "png")
        return ResolvedLatexImage(matched_bytes, "application/postscript", "eps")
    if extension in {"png", "jpg", "jpeg", "gif"}:
        content_type = "image/jpeg" if extension == "jpg" else f"image/{extension}"
        return ResolvedLatexImage(matched_bytes, content_type, extension)
    return ResolvedLatexImage(matched_bytes, "application/octet-stream", extension or "bin")


def _extract_latex_figures(
    latex: str,
    asset_bytes: dict[str, bytes] | None = None,
) -> list[FigureArtifact]:
    figures: list[FigureArtifact] = []
    asset_map = asset_bytes or {}
    for index, match in enumerate(_LATEX_FIGURE_ENV_RE.finditer(latex), start=1):
        body = match.group(2)
        caption_match = _LATEX_CAPTION_RE.search(body)
        caption = _strip_latex_inline(caption_match.group(1)) if caption_match else ""
        graphics_match = _LATEX_INCLUDEGRAPHICS_RE.search(body)
        source_path = graphics_match.group(1) if graphics_match else None

        image_bytes: bytes | None = None
        content_type: str | None = None
        file_extension = "bin"
        if source_path:
            resolved = _resolve_latex_image(asset_map, source_path)
            if resolved is not None:
                image_bytes = resolved.image_bytes
                content_type = resolved.content_type
                file_extension = resolved.file_extension

        figures.append(
            FigureArtifact(
                figure_id=f"figure-{index}",
                caption=caption[:1000],
                file_extension=file_extension,
                content_type=content_type,
                image_bytes=image_bytes,
                source_url=source_path,
            )
        )
    return figures


_LATEX_INPUT_RE = re.compile(r"\\(?:input|include|subfile)\{([^}]+)\}")


def _resolve_latex_inputs(
    content: str,
    tex_by_name: dict[str, str],
    current_path: str = "",
    depth: int = 0,
) -> str:
    if depth > 8:
        return content
    from posixpath import dirname as _posix_dirname, normpath as _posix_normpath

    base_dir = _posix_dirname(current_path) if current_path else ""

    def _candidates(ref: str) -> list[str]:
        cands = [ref, f"{ref}.tex"]
        # Resolve relative to the including file's directory (handles
        # "../common/intro" from sections/nested/method.tex etc.).
        if base_dir and not ref.startswith("/"):
            joined = _posix_normpath(f"{base_dir}/{ref}")
            cands.extend([joined, f"{joined}.tex"])
        stripped = ref.lstrip("./")
        cands.extend([stripped, f"{stripped}.tex"])
        return cands

    def _replace(match: re.Match[str]) -> str:
        ref = match.group(1).strip()
        for key in _candidates(ref):
            if key in tex_by_name:
                return (
                    "\n"
                    + _resolve_latex_inputs(tex_by_name[key], tex_by_name, key, depth + 1)
                    + "\n"
                )
        for key, body in tex_by_name.items():
            if key.endswith("/" + ref) or key.endswith("/" + ref + ".tex"):
                return (
                    "\n"
                    + _resolve_latex_inputs(body, tex_by_name, key, depth + 1)
                    + "\n"
                )
        # Don't silently delete unresolved includes — keep the original
        # directive in place so downstream extraction at least sees something
        # rather than a blank gap.
        return match.group(0)

    return _LATEX_INPUT_RE.sub(_replace, content)


def _extract_latex_text(source_bytes: bytes) -> str | None:
    """Backwards-compatible single-value extractor — returns just the
    resolved LaTeX text. Use ``_load_latex_archive`` when the caller
    also needs the binary file map for figure resolution (#48)."""
    text, _ = _load_latex_archive(source_bytes)
    return text


# Image extensions we recognize when resolving ``\includegraphics{path}``.
# Ordered by what we can render cheaply, NOT by arxiv prevalence: when
# an author ships both ``foo.png`` and ``foo.eps`` (a common dual-output
# pattern from LaTeX build pipelines) we'd rather pick the PNG that
# passes through unchanged than the EPS that needs Ghostscript at
# runtime to rasterize. PDF goes through pypdfium2 (already a dep) so
# it lands ahead of EPS, which falls back to raw bytes whenever the
# slim Docker image isn't carrying Ghostscript. ``.pdf_tex`` is rare
# and intentionally excluded — it's a TeX directive container, not a
# real image file.
_LATEX_IMAGE_EXTENSIONS: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".eps")


def _load_latex_archive(source_bytes: bytes) -> tuple[str | None, dict[str, bytes]]:
    """Return ``(resolved_latex_text, asset_bytes_by_name)`` for a tarball
    or plain-text LaTeX source.

    Contract: when ``resolved_latex_text`` is ``None`` (any failure
    path — tarball corrupt, no .tex files, includepdf-only stub,
    plain text without LaTeX markers), ``asset_bytes_by_name`` is
    always ``{}``. A partial asset dict from a failed parse would
    invite a caller to use it without realizing the .tex extraction
    didn't succeed; returning ``{}`` makes the contract explicit.
    On success, ``asset_bytes_by_name`` carries every recognized
    image member (see ``_LATEX_IMAGE_EXTENSIONS``) under its tarball
    member name. Plain-text sources have no assets, so the dict is
    ``{}`` there too.
    """
    asset_bytes: dict[str, bytes] = {}
    try:
        if source_bytes[:2] == b"\x1f\x8b" or source_bytes[:4] == b"PK\x03\x04":
            file_obj = io.BytesIO(source_bytes)
            try:
                with tarfile.open(fileobj=file_obj, mode="r:gz") as archive:
                    tex_by_name: dict[str, str] = {}
                    for member in archive.getmembers():
                        if not member.isfile():
                            continue
                        handle = archive.extractfile(member)
                        if handle is None:
                            continue
                        if member.name.endswith(".tex"):
                            tex_by_name[member.name] = handle.read().decode("utf-8", errors="replace")
                            continue
                        # Cap per-asset size to avoid pathologically large
                        # images filling memory; 25MB covers all real-world
                        # arxiv figures with headroom.
                        if member.size and member.size > 25 * 1024 * 1024:
                            continue
                        lowered = member.name.lower()
                        if any(lowered.endswith(ext) for ext in _LATEX_IMAGE_EXTENSIONS):
                            asset_bytes[member.name] = handle.read()
                    if not tex_by_name:
                        return None, {}
                    entrypoint_name = next(
                        (name for name, body in tex_by_name.items() if "\\begin{document}" in body),
                        None,
                    )
                    if entrypoint_name is None:
                        entrypoint_name = max(tex_by_name, key=lambda n: len(tex_by_name[n]))
                    entrypoint = tex_by_name[entrypoint_name]
                    resolved = _resolve_latex_inputs(entrypoint, tex_by_name, entrypoint_name)
                    if "\\includepdf" in resolved and not re.search(r"\\section\b", resolved):
                        return None, {}
                    return resolved, asset_bytes
            except tarfile.TarError:
                return None, {}
        text = source_bytes.decode("utf-8", errors="replace")
        if "\\begin{document}" in text or "\\section" in text:
            return text, asset_bytes
    except Exception:
        return None, {}
    return None, {}


def _latex_to_markdown(latex: str) -> str:
    text = latex
    body_start = text.find("\\begin{document}")
    if body_start != -1:
        text = text[body_start + len("\\begin{document}"):]
    body_end = text.find("\\end{document}")
    if body_end != -1:
        text = text[:body_end]
    text = re.sub(r"(?<!\\)%[^\n]*", "", text)
    text = re.sub(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", r"\n\n## Abstract\n\n\1\n\n", text, flags=re.DOTALL)
    text = re.sub(r"\\section\*?\{([^}]*)\}", r"\n\n## \1\n\n", text)
    text = re.sub(r"\\subsection\*?\{([^}]*)\}", r"\n\n### \1\n\n", text)
    text = re.sub(r"\\subsubsection\*?\{([^}]*)\}", r"\n\n#### \1\n\n", text)
    text = re.sub(r"\\paragraph\*?\{([^}]*)\}", r"\n\n**\1**\n\n", text)
    text = re.sub(r"\\(?:text(?:bf|it|tt|rm|sf)|emph|textsc)\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:cite|citep|citet|ref|eqref|label)\{[^}]*\}", "", text)
    text = re.sub(
        r"\\(?:begin|end)\{(?:equation|align|figure|table|tabular|itemize|enumerate|proof|theorem|lemma|definition|corollary|remark|example|algorithm|algorithmic|lstlisting)\*?\}",
        "\n",
        text,
    )
    text = re.sub(r"\\item\s*", "\n- ", text)
    text = re.sub(r"\\\\", "\n", text)
    text = re.sub(r"\\\w+\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\\w+\b", "", text)
    text = re.sub(r"[{}]", "", text)
    text = re.sub(r"\$+([^$]+)\$+", r"\1", text)
    lines = [line.rstrip() for line in text.splitlines()]
    body = "\n".join(lines)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return _sanitize_extracted_text(body)


def _docling_export_dict(document: object) -> dict:
    if hasattr(document, "export_to_dict"):
        return document.export_to_dict()
    if hasattr(document, "export_to_json"):
        exported = document.export_to_json()
        return json.loads(exported) if isinstance(exported, str) else exported
    return {}


def _docling_grid_to_rows(grid: object) -> list[list[str]] | None:
    """Convert a docling ``data.grid`` (list of rows of cell dicts) into the
    plain ``list[list[str]]`` shape :class:`TableArtifact` expects. Returns
    ``None`` if the grid isn't shaped like a real table."""
    if not isinstance(grid, list) or not grid:
        return None
    rows: list[list[str]] = []
    for raw_row in grid:
        if not isinstance(raw_row, list):
            return None
        row: list[str] = []
        for cell in raw_row:
            if isinstance(cell, dict):
                row.append(str(cell.get("text") or ""))
            elif cell is None:
                row.append("")
            else:
                row.append(str(cell))
        rows.append(row)
    return rows


def _docling_table_caption(raw: dict) -> str | None:
    """Best-effort caption pull from a docling table item. ``captions`` is a
    list of $ref strings on modern docling; on older shapes it can be a
    plain string."""
    captions = raw.get("captions")
    if isinstance(captions, str) and captions.strip():
        return captions
    if isinstance(captions, list):
        for entry in captions:
            if isinstance(entry, str) and entry.strip():
                return entry
            if isinstance(entry, dict):
                text = entry.get("text") or entry.get("$ref")
                if isinstance(text, str) and text.strip():
                    return text
    caption = raw.get("caption")
    if isinstance(caption, str) and caption.strip():
        return caption
    return None


def _scan_docling_tables(payload: object, tables: list[TableArtifact]) -> None:
    """Collect real table items from a docling ``export_to_dict`` payload.

    Docling puts canonical tables in the top-level ``tables`` list with an
    explicit ``label: "table"`` discriminator. Sibling lists like
    ``pictures``, ``texts``, and ``groups`` can also describe formulas /
    captions / cross-references whose values contain the substring
    "table" (e.g. ``"self_ref": "#/tables/0"``, captions like "as shown
    in Table 3", or formula items referenced from a table caption). A
    previous version of this scanner walked every dict recursively and
    matched on any value containing "table", which classified equations
    as tables whenever their surrounding metadata mentioned one.

    Read only the canonical ``tables`` list, gate on ``label == "table"``,
    and reshape ``data.grid`` into the ``list[list[str]]`` that
    :class:`TableArtifact` expects.
    """
    if not isinstance(payload, dict):
        return
    canonical = payload.get("tables")
    if not isinstance(canonical, list):
        return
    for raw in canonical:
        if not isinstance(raw, dict):
            continue
        if raw.get("label") != "table":
            continue
        data = raw.get("data")
        rows: list[list[str]] | None = None
        if isinstance(data, dict):
            rows = _docling_grid_to_rows(data.get("grid"))
        if rows is None:
            # Legacy/alternate shapes: some docling versions put a plain
            # list-of-list-of-strings under "rows" / "cells" directly on
            # the table item.
            for key in ("rows", "cells"):
                candidate = raw.get(key)
                if (
                    isinstance(candidate, list)
                    and candidate
                    and all(isinstance(row, list) for row in candidate)
                ):
                    rows = [[str(cell) for cell in row] for row in candidate]
                    break
        if rows is None:
            continue
        table_id = raw.get("self_ref") or raw.get("id") or f"table-{len(tables) + 1}"
        caption = _docling_table_caption(raw)
        tables.append(
            TableArtifact(table_id=slugify(str(table_id)), caption=caption, rows=rows)
        )


def extract_with_docling(pdf_bytes: bytes) -> ExtractionOutput | None:
    try:
        from docling.document_converter import DocumentConverter
    except Exception:
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as temp_file:
        temp_file.write(pdf_bytes)
        temp_file.flush()
        converter = DocumentConverter()
        result = converter.convert(temp_file.name)
        document = result.document
        markdown = document.export_to_markdown()
        payload = _docling_export_dict(document)
    tables: list[TableArtifact] = []
    _scan_docling_tables(payload, tables)
    return ExtractionOutput(
        source_type="pdf-docling",
        text_markdown=markdown,
        sections=_extract_sections(markdown),
        tables=tables,
        raw_artifacts=[RawArtifact(path="raw/paper.pdf", data=pdf_bytes, content_type="application/pdf")],
    )


def extract_uploaded_document(filename: str, data: bytes, *, content_type: str | None = None) -> ExtractionOutput:
    suffix = Path(filename).suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        text = data.decode("utf-8", errors="replace")
        markdown = text if suffix in {".md", ".markdown"} else f"# {Path(filename).stem}\n\n{text}"
        return ExtractionOutput(
            source_type="upload-text",
            text_markdown=markdown,
            sections=_extract_sections(markdown),
            raw_artifacts=[RawArtifact(path=f"raw/{Path(filename).name}", data=data, content_type=content_type or "text/plain")],
        )

    if suffix in {".pdf", ".docx", ".pptx"}:
        try:
            from docling.document_converter import DocumentConverter
        except Exception as exc:  # pragma: no cover - depends on optional runtime imports
            raise RuntimeError(f"Docling is unavailable for {filename}") from exc
        with tempfile.NamedTemporaryFile(suffix=suffix) as temp_file:
            temp_file.write(data)
            temp_file.flush()
            converter = DocumentConverter()
            result = converter.convert(temp_file.name)
            document = result.document
            markdown = document.export_to_markdown()
            payload = _docling_export_dict(document)
        tables: list[TableArtifact] = []
        _scan_docling_tables(payload, tables)
        return ExtractionOutput(
            source_type="upload-docling",
            text_markdown=markdown,
            sections=_extract_sections(markdown),
            tables=tables,
            raw_artifacts=[RawArtifact(path=f"raw/{Path(filename).name}", data=data, content_type=content_type or "application/octet-stream")],
        )

    raise RuntimeError(f"Unsupported upload type for {filename}")


def _attach_preview_artifact(
    extraction: ExtractionOutput,
    paper: PaperMetadata,
    *,
    pdf_bytes: bytes | None,
) -> None:
    """Append a first-page preview artifact to ``extraction`` when possible.

    Reuses ``pdf_bytes`` when already fetched; otherwise fetches once purely for
    the preview. Any failure (PDF missing, render failure) is swallowed so
    ingestion continues without a preview.
    """
    try:
        bytes_to_render = pdf_bytes
        if bytes_to_render is None:
            bytes_to_render = fetch_pdf(paper.arxiv_id, paper.pdf_url)
        if not bytes_to_render:
            return
        image_bytes = generate_first_page_preview(bytes_to_render)
        if not image_bytes:
            return
    except Exception:
        logger.warning(
            "Failed to attach first-page preview",
            extra={"event": "preview.attach_failed", "arxiv_id": paper.arxiv_id},
            exc_info=True,
        )
        return
    extraction.raw_artifacts.append(
        RawArtifact(path=PREVIEW_ARTIFACT_PATH, data=image_bytes, content_type="image/webp")
    )


def index_paper_artifacts(paper: PaperMetadata) -> ExtractionOutput:
    html = fetch_html(paper.arxiv_id)
    if html:
        source_bytes = fetch_source(paper.arxiv_id)
        sections = _extract_sections_from_html(html)
        markdown = _assemble_markdown_from_sections(paper, sections) if sections else _clean_html_to_markdown(html)
        raw_artifacts: list[RawArtifact] = []
        if source_bytes:
            raw_artifacts.append(
                RawArtifact(
                    path="raw/source.tar.gz",
                    data=source_bytes,
                    content_type="application/gzip",
                )
            )
        extraction = ExtractionOutput(
            source_type="html",
            text_markdown=markdown,
            sections=sections or _extract_sections(markdown),
            figures=_extract_figures_from_html(html, arxiv_id=paper.arxiv_id),
            tables=_extract_tables_from_html(html),
            raw_artifacts=raw_artifacts,
        )
        _attach_preview_artifact(extraction, paper, pdf_bytes=None)
        return extraction

    source_bytes = fetch_source(paper.arxiv_id)
    if source_bytes:
        latex, asset_bytes = _load_latex_archive(source_bytes)
        if latex:
            markdown = _latex_to_markdown(latex)
            extraction = ExtractionOutput(
                source_type="latex",
                text_markdown=markdown,
                sections=_extract_sections(markdown),
                figures=_extract_latex_figures(latex, asset_bytes),
                tables=_extract_latex_tables(latex),
                raw_artifacts=[
                    RawArtifact(path="raw/source.tar.gz", data=source_bytes, content_type="application/gzip"),
                    RawArtifact(path="raw/raw.tex", data=latex.encode("utf-8"), content_type="text/x-tex"),
                ],
            )
            _attach_preview_artifact(extraction, paper, pdf_bytes=None)
            return extraction

    pdf_bytes = fetch_pdf(paper.arxiv_id, paper.pdf_url)
    if pdf_bytes:
        docling_output = extract_with_docling(pdf_bytes)
        if docling_output:
            _attach_preview_artifact(docling_output, paper, pdf_bytes=pdf_bytes)
            return docling_output
        fallback_markdown = (
            f"# {paper.title}\n\n"
            f"## Abstract\n\n{paper.abstract}\n\n"
            "_PDF fallback stored because structured extraction was unavailable._"
        )
        extraction = ExtractionOutput(
            source_type="pdf",
            text_markdown=fallback_markdown,
            sections=_extract_sections(fallback_markdown),
            raw_artifacts=[RawArtifact(path="raw/paper.pdf", data=pdf_bytes, content_type="application/pdf")],
        )
        _attach_preview_artifact(extraction, paper, pdf_bytes=pdf_bytes)
        return extraction

    abstract_only = f"# {paper.title}\n\n## Abstract\n\n{paper.abstract}\n"
    return ExtractionOutput(
        source_type="abstract-only",
        text_markdown=abstract_only,
        sections=[SectionArtifact(title="Abstract", slug="abstract", markdown=paper.abstract)],
    )


def build_manifest(paper_id: str, paper: PaperMetadata, extraction: ExtractionOutput) -> dict:
    figure_count = len(extraction.figures)
    table_count = len(extraction.tables)
    section_slugs = [section.slug for section in extraction.sections]
    preview_path = next(
        (artifact.path for artifact in extraction.raw_artifacts if artifact.path == PREVIEW_ARTIFACT_PATH),
        None,
    )
    files = {
        "meta": "meta.json",
        "manifest": "manifest.json",
        "text": "text/full.md",
        "sections": [f"sections/{section.slug}.md" for section in extraction.sections],
        "figures_index": "figures/index.json" if figure_count else None,
        "tables_index": "tables/index.json" if table_count else None,
        "preview": preview_path,
        "raw": [artifact.path for artifact in extraction.raw_artifacts],
    }
    return {
        "paper_id": paper_id,
        "arxiv_id": paper.arxiv_id,
        "source_type": extraction.source_type,
        "index_status": "indexed",
        "available_artifacts": files,
        "section_slugs": section_slugs,
        "figure_count": figure_count,
        "table_count": table_count,
        "last_indexed_at": paper.indexed_at.isoformat() if paper.indexed_at else None,
    }


def figure_index_payload(figures: list[FigureArtifact]) -> list[dict]:
    payload = []
    for figure in figures:
        payload.append(
            {
                "figure_id": figure.figure_id,
                "caption": shorten(figure.caption, width=200, placeholder="..."),
                "source_url": figure.source_url,
                "content_type": figure.content_type,
            }
        )
    return payload


def table_index_payload(tables: list[TableArtifact]) -> list[dict]:
    payload = []
    for table in tables:
        payload.append(
            {
                "table_id": table.table_id,
                "caption": shorten(table.caption or "", width=200, placeholder="..."),
                "row_count": len(table.rows),
            }
        )
    return payload


def table_json_bytes(table: TableArtifact) -> bytes:
    return json.dumps(table.json_payload(), indent=2).encode("utf-8")


def table_csv_bytes(table: TableArtifact) -> bytes:
    return csv_bytes(table.rows)
