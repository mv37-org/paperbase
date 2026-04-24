from __future__ import annotations

import io
import tarfile

import requests
from PIL import Image

from paperbase.extractors import (
    PREVIEW_ARTIFACT_PATH,
    _extract_latex_text,
    _extract_sections,
    _latex_to_markdown,
    _sanitize_extracted_text,
    build_manifest,
    extract_github_url,
    index_paper_artifacts,
)
from paperbase.models import PaperMetadata


def _valid_single_page_pdf() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (612, 792), color="white").save(buffer, format="PDF")
    return buffer.getvalue()


def _sample_paper(arxiv_id: str = "2401.12345") -> PaperMetadata:
    return PaperMetadata(
        arxiv_id=arxiv_id,
        title="Sample Paper",
        authors="Alice, Bob",
        abstract="A short abstract.",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2024-01-10",
        updated="2024-01-11",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        html_url=f"https://arxiv.org/html/{arxiv_id}",
    )


def test_extract_sections_handles_markdown_headings_and_numbering():
    markdown = """
# Sample Paper

## 1 Introduction

Attention is useful.

### 2 Experimental Setup

We train for 10 epochs.
""".strip()

    sections = _extract_sections(markdown)

    assert [section.slug for section in sections] == ["introduction", "experimental-setup"]
    assert sections[0].markdown == "Attention is useful."
    assert sections[1].markdown == "We train for 10 epochs."


def test_index_paper_artifacts_splits_html_into_real_sections(monkeypatch):
    html = """
    <html><body>
      <article class="ltx_document">
        <div class="ltx_abstract">
          <h6 class="ltx_title ltx_title_abstract">Abstract</h6>
          <p class="ltx_p">A short abstract.</p>
        </div>
        <section class="ltx_section" id="S1">
          <h2 class="ltx_title ltx_title_section"><span class="ltx_tag">1 </span>Introduction</h2>
          <div class="ltx_para"><p class="ltx_p">Intro paragraph.</p></div>
        </section>
        <section class="ltx_section" id="S2">
          <h2 class="ltx_title ltx_title_section"><span class="ltx_tag">2 </span>Method</h2>
          <div class="ltx_para"><p class="ltx_p">Method overview.</p></div>
          <section class="ltx_subsection" id="S2.S1">
            <h3 class="ltx_title ltx_title_subsection"><span class="ltx_tag">2.1 </span>Training Setup</h3>
            <div class="ltx_para"><p class="ltx_p">Train with Adam.</p></div>
          </section>
        </section>
      </article>
    </body></html>
    """.strip()

    monkeypatch.setattr("paperbase.extractors.fetch_html", lambda arxiv_id: html)
    monkeypatch.setattr("paperbase.extractors.fetch_source", lambda arxiv_id: None)
    monkeypatch.setattr("paperbase.extractors.fetch_pdf", lambda arxiv_id, pdf_url=None: None)
    monkeypatch.setattr("paperbase.extractors._extract_figures_from_html", lambda html, *, arxiv_id=None: [])
    monkeypatch.setattr("paperbase.extractors._extract_tables_from_html", lambda html: [])

    extraction = index_paper_artifacts(_sample_paper())

    assert extraction.source_type == "html"
    assert [section.slug for section in extraction.sections] == [
        "abstract",
        "introduction",
        "method",
        "training-setup",
    ]
    assert extraction.sections[0].markdown == "A short abstract."
    assert extraction.sections[1].markdown == "Intro paragraph."
    assert extraction.sections[2].markdown == "Method overview."
    assert extraction.sections[3].markdown == "Train with Adam."
    assert "## Introduction" in extraction.text_markdown
    assert "## Training Setup" in extraction.text_markdown


def test_sanitize_extracted_text_drops_latex_preamble_residue():
    raw = "\n".join(
        [
            "-.5em",
            "%6pt",
            "[10pt,twocolumn,letterpaper]article",
            "cvpr",
            "graphicx",
            "amsmath",
            "[1]#1",
            "\\usepackage{babel}",
            "",
            "Deeper neural networks are more difficult to train.",
            "We present a residual learning framework.",
            "",
            "An ensemble achieves 3.57\\% error on ImageNet.",
            "",
            "-1em",
        ]
    )

    cleaned = _sanitize_extracted_text(raw)

    assert cleaned == (
        "Deeper neural networks are more difficult to train.\n"
        "We present a residual learning framework.\n"
        "\n"
        "An ensemble achieves 3.57% error on ImageNet."
    )


def test_latex_to_markdown_emits_section_headers_and_strips_preamble():
    latex = r"""
\documentclass[10pt,twocolumn]{article}
\usepackage{amsmath}
\usepackage{graphicx}
\title{Neural Machine Translation}
\begin{document}
\maketitle
\begin{abstract}
We propose an attention-based translation model.
\end{abstract}
\section{Introduction}
Sequence to sequence learning has improved translation quality.
\section{Learning to Align and Translate}
We introduce a joint alignment mechanism.
\subsection{Decoder}
The decoder attends to encoder states.
\end{document}
"""

    markdown = _latex_to_markdown(latex)

    assert "documentclass" not in markdown
    assert "amsmath" not in markdown
    assert "## Abstract" in markdown
    assert "## Introduction" in markdown
    assert "## Learning to Align and Translate" in markdown
    assert "### Decoder" in markdown
    assert "We propose an attention-based translation model." in markdown


def test_extract_sections_splits_on_arbitrary_markdown_headers():
    markdown = "\n".join(
        [
            "# Neural Machine Translation",
            "",
            "## Abstract",
            "",
            "We propose an attention model.",
            "",
            "## Learning to Align and Translate",
            "",
            "Joint alignment improves results.",
            "",
            "### Decoder",
            "",
            "The decoder attends to encoder states.",
        ]
    )

    sections = _extract_sections(markdown)

    assert [section.slug for section in sections] == [
        "abstract",
        "learning-to-align-and-translate",
        "decoder",
    ]
    assert sections[0].markdown == "We propose an attention model."
    assert sections[1].markdown == "Joint alignment improves results."
    assert sections[2].markdown == "The decoder attends to encoder states."


def _build_tex_tarball(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in files.items():
            data = body.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extract_latex_text_resolves_input_and_include_directives():
    entrypoint = (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\input{intro}\n"
        "\\include{methods/method}\n"
        "\\end{document}\n"
    )
    intro = "\\section{Introduction}\nWe study attention.\n"
    method = "\\section{Method}\nWe align and translate.\n"

    tarball = _build_tex_tarball(
        {
            "main.tex": entrypoint,
            "intro.tex": intro,
            "methods/method.tex": method,
        }
    )

    resolved = _extract_latex_text(tarball)

    assert resolved is not None
    assert "\\section{Introduction}" in resolved
    assert "\\section{Method}" in resolved
    assert "\\input{" not in resolved
    assert "\\include{" not in resolved


def test_extract_sections_keeps_first_h1_when_all_headers_are_h1():
    """If every header is an H1 (some markdown exports do this for Abstract /
    Introduction / etc.), do not silently drop the first one as a paper title."""
    markdown = "\n".join(
        [
            "# Abstract",
            "",
            "We propose a model.",
            "",
            "# Introduction",
            "",
            "Background and motivation.",
            "",
            "# Method",
            "",
            "Architecture details.",
        ]
    )

    sections = _extract_sections(markdown)

    assert [section.slug for section in sections] == ["abstract", "introduction", "method"]
    assert sections[0].markdown == "We propose a model."
    assert sections[1].markdown == "Background and motivation."
    assert sections[2].markdown == "Architecture details."


def test_extract_sections_keeps_first_h1_when_it_has_substantive_body_in_mixed_hierarchy():
    """Mixed `# Abstract\\nbody\\n## Introduction` shape: the leading H1 has
    real body content, so it is a section — not a paper title — even though
    later headers are deeper. The old 'any H2+ means drop H1' heuristic lost
    the Abstract content; body-content check keeps it."""
    markdown = "\n".join(
        [
            "# Abstract",
            "",
            "We propose an attention-based model.",
            "",
            "## Introduction",
            "",
            "Sequence-to-sequence learning motivates this work.",
            "",
            "## Method",
            "",
            "Architecture details.",
        ]
    )

    sections = _extract_sections(markdown)

    assert [section.slug for section in sections] == ["abstract", "introduction", "method"]
    assert sections[0].markdown == "We propose an attention-based model."
    assert sections[1].markdown == "Sequence-to-sequence learning motivates this work."


def test_extract_sections_preserves_lead_text_before_first_heading():
    """Content that appears before the first header (an unheaded abstract or
    opening paragraph) must be indexed rather than silently dropped. The
    splitter emits a synthetic 'Preamble' section for it."""
    markdown = "\n".join(
        [
            "We propose a novel approach to the problem. This is standalone",
            "lead text with no heading above it.",
            "",
            "## Introduction",
            "",
            "Background material.",
            "",
            "## Method",
            "",
            "Approach details.",
        ]
    )

    sections = _extract_sections(markdown)

    assert [section.slug for section in sections] == ["preamble", "introduction", "method"]
    assert "We propose a novel approach" in sections[0].markdown
    assert "standalone" in sections[0].markdown
    assert sections[1].markdown == "Background material."


def test_extract_latex_text_resolves_relative_input_paths():
    """An \\input that uses .. or a directory-relative path must be resolved
    against the *including* file's directory, not just by bare-name lookup."""
    main = (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\input{sections/nested/method}\n"
        "\\end{document}\n"
    )
    method = (
        "\\section{Method}\n"
        "We present details below.\n"
        "\\input{../common/intro}\n"
    )
    intro = "\\section{Common Intro}\nShared introduction content.\n"

    tarball = _build_tex_tarball(
        {
            "main.tex": main,
            "sections/nested/method.tex": method,
            "sections/common/intro.tex": intro,
        }
    )

    resolved = _extract_latex_text(tarball)

    assert resolved is not None
    assert "\\section{Method}" in resolved
    assert "\\section{Common Intro}" in resolved
    assert "Shared introduction content." in resolved
    assert "\\input{" not in resolved


def test_extract_latex_text_preserves_unresolved_includes():
    """An \\input target that doesn't exist in the tarball should leave the
    original directive in place rather than silently deleting the reference."""
    main = (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "Real prose comes first.\n"
        "\\input{nonexistent}\n"
        "More real prose.\n"
        "\\end{document}\n"
    )

    tarball = _build_tex_tarball({"main.tex": main})

    resolved = _extract_latex_text(tarball)

    assert resolved is not None
    assert "Real prose comes first." in resolved
    assert "More real prose." in resolved
    assert "\\input{nonexistent}" in resolved


def test_extract_latex_text_skips_includepdf_wrapper():
    wrapper = (
        "\\documentclass{article}\n"
        "\\usepackage{pdfpages}\n"
        "\\begin{document}\n"
        "\\includepdf[pages=1-last]{paper.pdf}\n"
        "\\end{document}\n"
    )

    tarball = _build_tex_tarball({"arxiv.tex": wrapper})

    assert _extract_latex_text(tarball) is None


def _install_preview_monkeypatches(
    monkeypatch,
    *,
    html: str | None = None,
    source_bytes: bytes | None = None,
    pdf_bytes: bytes | None = None,
    pdf_error: Exception | None = None,
    with_docling: bool = False,
) -> dict[str, int]:
    counts = {"fetch_pdf": 0, "fetch_html": 0, "fetch_source": 0}

    def _fetch_html(_arxiv_id):
        counts["fetch_html"] += 1
        return html

    def _fetch_source(_arxiv_id):
        counts["fetch_source"] += 1
        return source_bytes

    def _fetch_pdf(_arxiv_id, _pdf_url=None):
        counts["fetch_pdf"] += 1
        if pdf_error is not None:
            raise pdf_error
        return pdf_bytes

    monkeypatch.setattr("paperbase.extractors.fetch_html", _fetch_html)
    monkeypatch.setattr("paperbase.extractors.fetch_source", _fetch_source)
    monkeypatch.setattr("paperbase.extractors.fetch_pdf", _fetch_pdf)
    monkeypatch.setattr("paperbase.extractors._extract_figures_from_html", lambda _html, *, arxiv_id=None: [])
    monkeypatch.setattr("paperbase.extractors._extract_tables_from_html", lambda _html: [])
    if with_docling:
        # Leave docling's real behavior alone.
        pass
    else:
        monkeypatch.setattr("paperbase.extractors.extract_with_docling", lambda _bytes: None)
    return counts


def test_index_paper_artifacts_attaches_preview_on_pdf_fallback_without_refetching(monkeypatch):
    pdf_bytes = _valid_single_page_pdf()
    counts = _install_preview_monkeypatches(monkeypatch, pdf_bytes=pdf_bytes)

    extraction = index_paper_artifacts(_sample_paper())

    assert extraction.source_type == "pdf"
    preview_artifacts = [a for a in extraction.raw_artifacts if a.path == PREVIEW_ARTIFACT_PATH]
    assert len(preview_artifacts) == 1
    assert preview_artifacts[0].content_type == "image/webp"
    assert preview_artifacts[0].data.startswith(b"RIFF")
    # PDF fallback path must not refetch — one fetch covers both extraction and preview.
    assert counts["fetch_pdf"] == 1


def test_index_paper_artifacts_attaches_preview_on_html_path_with_one_pdf_fetch(monkeypatch):
    html = """
    <html><body>
      <article class="ltx_document">
        <section class="ltx_section" id="S1">
          <h2 class="ltx_title ltx_title_section">Introduction</h2>
          <div class="ltx_para"><p class="ltx_p">Intro.</p></div>
        </section>
      </article>
    </body></html>
    """.strip()
    pdf_bytes = _valid_single_page_pdf()
    counts = _install_preview_monkeypatches(monkeypatch, html=html, pdf_bytes=pdf_bytes)

    extraction = index_paper_artifacts(_sample_paper())

    assert extraction.source_type == "html"
    preview_artifacts = [a for a in extraction.raw_artifacts if a.path == PREVIEW_ARTIFACT_PATH]
    assert len(preview_artifacts) == 1
    # HTML path does not fetch the PDF for extraction, so the preview fetch is the only one.
    assert counts["fetch_pdf"] == 1


def test_index_paper_artifacts_attaches_preview_on_latex_path_with_one_pdf_fetch(monkeypatch):
    latex_tarball = _build_tex_tarball(
        {
            "paper.tex": (
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "\\section{Introduction}\n"
                "Hello world.\n"
                "\\end{document}\n"
            )
        }
    )
    pdf_bytes = _valid_single_page_pdf()
    counts = _install_preview_monkeypatches(
        monkeypatch,
        html=None,
        source_bytes=latex_tarball,
        pdf_bytes=pdf_bytes,
    )

    extraction = index_paper_artifacts(_sample_paper())

    assert extraction.source_type == "latex"
    preview_artifacts = [a for a in extraction.raw_artifacts if a.path == PREVIEW_ARTIFACT_PATH]
    assert len(preview_artifacts) == 1
    assert counts["fetch_pdf"] == 1


def test_index_paper_artifacts_skips_preview_when_pdf_unavailable(monkeypatch):
    html = "<html><body><article><p>Just intro.</p></article></body></html>"
    counts = _install_preview_monkeypatches(monkeypatch, html=html, pdf_bytes=None)

    extraction = index_paper_artifacts(_sample_paper())

    assert extraction.source_type == "html"
    assert all(artifact.path != PREVIEW_ARTIFACT_PATH for artifact in extraction.raw_artifacts)
    assert counts["fetch_pdf"] == 1
    manifest = build_manifest("paper_1", _sample_paper(), extraction)
    assert manifest["available_artifacts"]["preview"] is None


def test_index_paper_artifacts_skips_preview_when_pdf_fetch_raises(monkeypatch, caplog):
    html = """
    <html><body>
      <article class="ltx_document">
        <section class="ltx_section" id="S1">
          <h2 class="ltx_title ltx_title_section">Introduction</h2>
          <div class="ltx_para"><p class="ltx_p">Intro.</p></div>
        </section>
      </article>
    </body></html>
    """.strip()
    counts = _install_preview_monkeypatches(
        monkeypatch,
        html=html,
        pdf_error=requests.Timeout("preview fetch timed out"),
    )

    with caplog.at_level("WARNING"):
        extraction = index_paper_artifacts(_sample_paper())

    assert extraction.source_type == "html"
    assert all(artifact.path != PREVIEW_ARTIFACT_PATH for artifact in extraction.raw_artifacts)
    assert counts["fetch_pdf"] == 1
    assert any("preview" in record.message.lower() for record in caplog.records)


def test_index_paper_artifacts_skips_preview_when_render_fails(monkeypatch, caplog):
    # PDF fallback path with garbage bytes — docling stub returns None,
    # pdf-fallback branch stores raw/paper.pdf, preview render fails silently.
    counts = _install_preview_monkeypatches(monkeypatch, pdf_bytes=b"not a real pdf")

    with caplog.at_level("WARNING"):
        extraction = index_paper_artifacts(_sample_paper())

    assert extraction.source_type == "pdf"
    assert all(artifact.path != PREVIEW_ARTIFACT_PATH for artifact in extraction.raw_artifacts)
    # One fetch total on the PDF-fallback path; preview reuses those bytes and fails on them.
    assert counts["fetch_pdf"] == 1


def test_manifest_always_carries_preview_key(monkeypatch):
    pdf_bytes = _valid_single_page_pdf()
    _install_preview_monkeypatches(monkeypatch, pdf_bytes=pdf_bytes)

    extraction = index_paper_artifacts(_sample_paper())
    manifest = build_manifest("paper_1", _sample_paper(), extraction)

    assert "preview" in manifest["available_artifacts"]
    assert manifest["available_artifacts"]["preview"] == PREVIEW_ARTIFACT_PATH


def test_extract_figures_from_html_handles_pre_pathed_srcs_without_base_tag(monkeypatch):
    """Real-world shape from arxiv HTML on papers like 1706.03762
    (Attention Is All You Need): NO ``<base>`` tag, and ``<img>`` srcs
    come pre-pathed with ``<arxiv_id>v<N>/Figures/...``. The extractor
    must use ``https://arxiv.org/html/`` as the base so urljoin
    produces the correct absolute URL — NOT
    ``https://arxiv.org/html/<arxiv_id>/`` which would duplicate the
    id and 404.
    """
    from paperbase.extractors import _extract_figures_from_html, SESSION

    html = """
    <html><body>
      <figure>
        <img src="1706.03762v7/Figures/ModalNet-21.png"/>
        <figcaption>Fig 1.</figcaption>
      </figure>
      <figure>
        <img src="1706.03762v7/x1.png"/>
        <figcaption>Fig 2.</figcaption>
      </figure>
    </body></html>
    """.strip()

    fetched_urls: list[str] = []

    class _Resp:
        status_code = 200
        headers = {"content-type": "image/png"}
        content = b"PNG"

    monkeypatch.setattr(SESSION, "get", lambda url, *a, **k: fetched_urls.append(url) or _Resp())

    figures = _extract_figures_from_html(html, arxiv_id="1706.03762")

    assert len(figures) == 2
    assert figures[0].source_url == "https://arxiv.org/html/1706.03762v7/Figures/ModalNet-21.png"
    assert figures[1].source_url == "https://arxiv.org/html/1706.03762v7/x1.png"
    assert all(f.image_bytes == b"PNG" for f in figures)


def test_extract_figures_from_html_resolves_bare_srcs_via_root_relative_base_tag(monkeypatch):
    """Real-world shape from arxiv HTML on papers like 2106.09685 (LoRA)
    and 2305.10601 (Tree of Thoughts): a ROOT-RELATIVE
    ``<base href="/html/<id>v<N>/">`` tag plus bare ``<img src="x1.png">``.

    The extractor must:
      1. Pick up the ``<base>`` tag so bare srcs resolve correctly,
      2. Qualify its root-relative href against ``https://arxiv.org``
         so the result is absolute (otherwise the HTTP GET raises
         ``MissingSchema`` on ``/html/<id>v<N>/x1.png``).

    Also covers a full external ``https://`` src passing through.
    """
    from paperbase.extractors import _extract_figures_from_html, SESSION

    html = """
    <html><head><base href="/html/2106.09685v2/"/></head>
    <body>
      <figure>
        <img src="x1.png"/>
        <figcaption>Fig 1.</figcaption>
      </figure>
      <figure>
        <img src="Figures/arch.png"/>
        <figcaption>Fig 2.</figcaption>
      </figure>
      <figure>
        <img src="https://cdn.example/external.png"/>
        <figcaption>External.</figcaption>
      </figure>
    </body></html>
    """.strip()

    fetched_urls: list[str] = []

    class _Resp:
        status_code = 200
        headers = {"content-type": "image/png"}
        content = b"PNG"

    monkeypatch.setattr(SESSION, "get", lambda url, *a, **k: fetched_urls.append(url) or _Resp())

    figures = _extract_figures_from_html(html, arxiv_id="2106.09685")

    assert len(figures) == 3
    # Bare relative → resolved against the per-paper base derived from <base>.
    assert figures[0].source_url == "https://arxiv.org/html/2106.09685v2/x1.png"
    assert figures[1].source_url == "https://arxiv.org/html/2106.09685v2/Figures/arch.png"
    # Full external URL is left alone.
    assert figures[2].source_url == "https://cdn.example/external.png"
    assert all(f.image_bytes == b"PNG" for f in figures)
    assert fetched_urls == [f.source_url for f in figures]


# --- code_url extraction for #41 auto-enqueue ------------------------------


def test_extract_github_url_picks_up_in_abstract_with_trailing_period():
    """Common arxiv-abstract shape: ``code is available at https://github.com/foo/bar.``
    Trailing period from sentence context must be stripped so the stored
    code_url is canonical."""
    abstract = (
        "We propose LoRA, a low-rank adaptation method. We release "
        "our code at https://github.com/microsoft/LoRA."
    )
    assert extract_github_url(abstract) == "https://github.com/microsoft/LoRA"


def test_extract_github_url_searches_body_when_abstract_is_bare():
    """If the abstract doesn't mention code, the helper falls through to
    the full body text (first-found wins) so papers that only advertise
    their repo in an 'Implementation' section still get picked up."""
    abstract = "We present a model."
    body = "Our code is released at https://github.com/facebookresearch/llama and pre-trained weights..."
    assert extract_github_url(abstract, body) == "https://github.com/facebookresearch/llama"


def test_extract_github_url_strips_git_suffix():
    """Git clone URLs sometimes leak into paper text; the canonical
    stored form uses the web URL without ``.git``."""
    body = "git clone https://github.com/owner/repo.git"
    assert extract_github_url(body) == "https://github.com/owner/repo"


def test_extract_github_url_returns_none_when_no_github_mention():
    """Papers without any GitHub reference (ResNet, GPT-3, etc.) must
    get ``None`` so the downstream code-ingest auto-enqueue is
    correctly skipped."""
    assert extract_github_url("No code released; weights are available on request.") is None
    assert extract_github_url("") is None
    assert extract_github_url() is None


def test_extract_github_url_ignores_gitlab_bitbucket():
    """v1 only supports github.com (per ``code_ingest.parse_github_url``).
    Other hosts must return None so we don't store an un-ingestable URL."""
    assert extract_github_url("code at https://gitlab.com/foo/bar") is None
    assert extract_github_url("https://bitbucket.org/foo/bar for artifacts") is None


def test_extract_github_url_keeps_subpath_for_code_ingest_to_normalize():
    """If the URL includes a ``/tree/<ref>`` subpath, keep it — ``code_ingest.parse_github_url``
    handles normalization downstream and may use the ref hint.
    """
    body = "See https://github.com/owner/repo/tree/v1.2 for the pinned release."
    url = extract_github_url(body)
    assert url is not None
    assert url.startswith("https://github.com/owner/repo")
