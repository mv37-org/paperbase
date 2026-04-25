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


def _extract_latex_figures(latex: str) -> list[FigureArtifact]:
    figures: list[FigureArtifact] = []
    for index, match in enumerate(_LATEX_FIGURE_ENV_RE.finditer(latex), start=1):
        body = match.group(2)
        caption_match = _LATEX_CAPTION_RE.search(body)
        caption = _strip_latex_inline(caption_match.group(1)) if caption_match else ""
        graphics_match = _LATEX_INCLUDEGRAPHICS_RE.search(body)
        source_path = graphics_match.group(1) if graphics_match else None
        figures.append(
            FigureArtifact(
                figure_id=f"figure-{index}",
                caption=caption[:1000],
                file_extension="bin",
                content_type=None,
                image_bytes=None,
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
    try:
        if source_bytes[:2] == b"\x1f\x8b" or source_bytes[:4] == b"PK\x03\x04":
            file_obj = io.BytesIO(source_bytes)
            try:
                with tarfile.open(fileobj=file_obj, mode="r:gz") as archive:
                    tex_by_name: dict[str, str] = {}
                    for member in archive.getmembers():
                        if not member.name.endswith(".tex"):
                            continue
                        handle = archive.extractfile(member)
                        if handle is None:
                            continue
                        tex_by_name[member.name] = handle.read().decode("utf-8", errors="replace")
                    if not tex_by_name:
                        return None
                    entrypoint_name = next(
                        (name for name, body in tex_by_name.items() if "\\begin{document}" in body),
                        None,
                    )
                    if entrypoint_name is None:
                        entrypoint_name = max(tex_by_name, key=lambda n: len(tex_by_name[n]))
                    entrypoint = tex_by_name[entrypoint_name]
                    resolved = _resolve_latex_inputs(entrypoint, tex_by_name, entrypoint_name)
                    if "\\includepdf" in resolved and not re.search(r"\\section\b", resolved):
                        return None
                    return resolved
            except tarfile.TarError:
                return None
        text = source_bytes.decode("utf-8", errors="replace")
        if "\\begin{document}" in text or "\\section" in text:
            return text
    except Exception:
        return None
    return None


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


def _scan_docling_tables(payload: object, tables: list[TableArtifact]) -> None:
    if isinstance(payload, dict):
        lower_values = " ".join(str(value) for value in payload.values()).lower()
        if "table" in lower_values:
            rows = payload.get("rows") or payload.get("data") or payload.get("cells")
            if isinstance(rows, list) and rows and all(isinstance(row, list) for row in rows):
                table_id = payload.get("id") or f"table-{len(tables) + 1}"
                caption = payload.get("caption")
                tables.append(TableArtifact(table_id=slugify(str(table_id)), caption=caption, rows=rows))
        for value in payload.values():
            _scan_docling_tables(value, tables)
    elif isinstance(payload, list):
        for item in payload:
            _scan_docling_tables(item, tables)


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
        latex = _extract_latex_text(source_bytes)
        if latex:
            markdown = _latex_to_markdown(latex)
            extraction = ExtractionOutput(
                source_type="latex",
                text_markdown=markdown,
                sections=_extract_sections(markdown),
                figures=_extract_latex_figures(latex),
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
