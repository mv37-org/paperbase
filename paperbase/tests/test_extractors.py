from __future__ import annotations

import io
import tarfile

from paperbase.extractors import (
    _extract_latex_text,
    _extract_sections,
    _latex_to_markdown,
    _sanitize_extracted_text,
    index_paper_artifacts,
)
from paperbase.models import PaperMetadata


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
