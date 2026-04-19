from __future__ import annotations

from paperbase.extractors import _extract_sections, index_paper_artifacts
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
