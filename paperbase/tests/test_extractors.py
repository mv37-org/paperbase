from __future__ import annotations

import io
import tarfile

import requests
from PIL import Image

from paperbase.extractors import (
    PREVIEW_ARTIFACT_PATH,
    _extract_latex_figures,
    _extract_latex_text,
    _extract_sections,
    _latex_to_markdown,
    _load_latex_archive,
    _sanitize_extracted_text,
    build_manifest,
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
    monkeypatch.setattr("paperbase.extractors._extract_figures_from_html", lambda html: [])
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


def _build_mixed_tarball(files: dict[str, bytes | str]) -> bytes:
    """Like ``_build_tex_tarball`` but accepts binary entries too — used
    for #48's figure-resolution tests."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in files.items():
            data = body.encode("utf-8") if isinstance(body, str) else body
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _tiny_png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color="red").save(buffer, format="PNG")
    return buffer.getvalue()


def _tiny_jpg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color="blue").save(buffer, format="JPEG")
    return buffer.getvalue()


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
    monkeypatch.setattr("paperbase.extractors._extract_figures_from_html", lambda _html: [])
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


# --- LaTeX figure image-bytes resolution (#48) ------------------------------


def test_load_latex_archive_returns_text_and_assets():
    """``_load_latex_archive`` is the new combined extractor used by the
    LaTeX path: it parses the tarball once and returns both the resolved
    .tex content AND a binary asset map keyed by member name (#48)."""
    png = _tiny_png_bytes()
    tarball = _build_mixed_tarball({
        "main.tex": (
            "\\documentclass{article}\n\\begin{document}\n"
            "\\begin{figure}\\includegraphics{figs/diagram}\\caption{Diagram}\\end{figure}\n"
            "\\end{document}\n"
        ),
        "figs/diagram.png": png,
    })

    text, assets = _load_latex_archive(tarball)

    assert text is not None
    assert "\\begin{document}" in text
    assert "figs/diagram.png" in assets
    assert assets["figs/diagram.png"] == png


def test_extract_latex_figures_passes_through_png_bytes_with_extension_fallback():
    """`\\includegraphics{figs/diagram}` (no extension) resolves to
    ``figs/diagram.png`` via the candidate-extension search; the PNG
    bytes pass through unchanged with content_type=image/png. Closes
    the literal #48 bug: ``image_bytes=None`` for LaTeX figures."""
    png = _tiny_png_bytes()
    latex = (
        "\\begin{figure}\n\\includegraphics{figs/diagram}\n"
        "\\caption{A diagram.}\n\\end{figure}\n"
    )

    figures = _extract_latex_figures(latex, {"figs/diagram.png": png})

    assert len(figures) == 1
    assert figures[0].image_bytes == png
    assert figures[0].content_type == "image/png"
    assert figures[0].file_extension == "png"
    # source_url retains the author-written reference for traceability.
    assert figures[0].source_url == "figs/diagram"


def test_extract_latex_figures_resolves_jpeg_with_explicit_extension():
    """Author wrote the extension explicitly — direct lookup wins over
    extension fallback. JPEG content_type uses ``image/jpeg`` for both
    .jpg and .jpeg suffixes."""
    jpg = _tiny_jpg_bytes()
    latex = "\\begin{figure}\\includegraphics{figs/photo.jpg}\\caption{Photo}\\end{figure}"

    figures = _extract_latex_figures(latex, {"figs/photo.jpg": jpg})

    assert figures[0].image_bytes == jpg
    assert figures[0].content_type == "image/jpeg"
    assert figures[0].file_extension == "jpg"


def test_extract_latex_figures_rasterizes_pdf_to_png():
    """PDF-format figures are rasterized to PNG via pypdfium2 at extract
    time so browsers / VLM pipelines can render them inline. We don't
    assert the exact bytes (the rasterizer is non-deterministic across
    versions); we only assert that bytes are present and content_type
    flipped to image/png."""
    pdf_bytes = _valid_single_page_pdf()
    latex = "\\begin{figure}\\includegraphics{figs/arch.pdf}\\caption{Arch}\\end{figure}"

    figures = _extract_latex_figures(latex, {"figs/arch.pdf": pdf_bytes})

    assert figures[0].image_bytes is not None
    assert figures[0].image_bytes[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic
    assert figures[0].content_type == "image/png"
    assert figures[0].file_extension == "png"


def test_extract_latex_figures_keeps_eps_raw_when_ghostscript_unavailable(monkeypatch):
    """EPS rasterize requires Ghostscript at runtime. When it's not
    installed (the default in our slim Docker image), we fall back to
    storing the raw EPS bytes with ``content_type='application/postscript'``
    rather than dropping the figure. Strictly better than the prior
    state of ``image_bytes=None``; full inline rendering is a follow-up."""
    eps_bytes = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 100 100\n%%EOF\n"
    latex = "\\begin{figure}\\includegraphics{eps/block}\\caption{Block}\\end{figure}"

    # Force the rasterize path to fail so we exercise the raw-bytes
    # fallback regardless of whether Ghostscript happens to be installed
    # on the test machine.
    monkeypatch.setattr("paperbase.extractors._rasterize_eps_to_png", lambda _b: None)

    figures = _extract_latex_figures(latex, {"eps/block.eps": eps_bytes})

    assert figures[0].image_bytes == eps_bytes
    assert figures[0].content_type == "application/postscript"
    assert figures[0].file_extension == "eps"


def test_extract_latex_figures_resolves_member_with_directory_prefix():
    """Tarballs from arxiv often nest contents under a top-level
    directory (``main/figs/foo.eps``) while authors write the
    ``\\includegraphics{figs/foo}`` reference relative to the .tex
    entrypoint. Suffix-match lets the resolver find the file anyway."""
    png = _tiny_png_bytes()
    latex = "\\begin{figure}\\includegraphics{figs/diagram}\\caption{D}\\end{figure}"

    figures = _extract_latex_figures(latex, {"main/figs/diagram.png": png})

    assert figures[0].image_bytes == png
    assert figures[0].content_type == "image/png"


def test_extract_latex_figures_returns_none_bytes_for_unresolvable_path():
    """If the asset map doesn't contain a matching file under any
    candidate extension, the figure record still gets created (the
    caption is real signal) but image_bytes stays None. Pre-#48
    behavior preserved as the failure mode."""
    latex = "\\begin{figure}\\includegraphics{figs/missing}\\caption{M}\\end{figure}"

    figures = _extract_latex_figures(latex, {"figs/something_else.png": _tiny_png_bytes()})

    assert len(figures) == 1
    assert figures[0].image_bytes is None
    assert figures[0].content_type is None
    assert figures[0].source_url == "figs/missing"


def test_extract_latex_figures_without_asset_map_preserves_legacy_behavior():
    """Calling without ``asset_bytes`` (legacy single-arg call) still
    works — image_bytes is None, no exceptions. Lock the backwards-
    compat signature so an older caller doesn't break post-#48."""
    latex = "\\begin{figure}\\includegraphics{figs/diagram}\\caption{X}\\end{figure}"

    figures = _extract_latex_figures(latex)

    assert len(figures) == 1
    assert figures[0].image_bytes is None
    assert figures[0].source_url == "figs/diagram"


def test_load_latex_archive_skips_oversize_image_assets():
    """The asset map caps per-file size to avoid pathologically large
    images blowing up memory. 25MB is the threshold — anything bigger
    is silently dropped; the figure resolver then falls back to None."""
    big_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * (26 * 1024 * 1024)
    tarball = _build_mixed_tarball({
        "main.tex": (
            "\\documentclass{article}\n\\begin{document}\n"
            "\\begin{figure}\\includegraphics{figs/huge.png}\\end{figure}\n"
            "\\end{document}\n"
        ),
        "figs/huge.png": big_png,
    })

    _, assets = _load_latex_archive(tarball)

    assert "figs/huge.png" not in assets
