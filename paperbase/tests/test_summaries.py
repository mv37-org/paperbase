from __future__ import annotations

from paperbase.extractors import ExtractionOutput, SectionArtifact
from paperbase.models import PaperMetadata
from paperbase.summaries import build_paper_summaries


def test_build_paper_summaries_uses_abstract_and_sections():
    paper = PaperMetadata(
        arxiv_id="2401.12345",
        title="Agent Paper",
        abstract=(
            "We present an agent framework for tool use. "
            "The method plans over actions and external tools."
        ),
    )
    extraction = ExtractionOutput(
        source_type="html",
        text_markdown="",
        sections=[
            SectionArtifact(title="Abstract", slug="abstract", markdown="..."),
            SectionArtifact(title="Planning", slug="planning", markdown="..."),
            SectionArtifact(title="Tool Use", slug="tool-use", markdown="..."),
        ],
    )

    summary_short, summary_eli5 = build_paper_summaries(paper, extraction)

    assert "agent framework for tool use" in summary_short
    assert "Planning" in summary_short
    assert summary_eli5.startswith("In simple terms,")
    assert "tool use" in summary_eli5.lower()
