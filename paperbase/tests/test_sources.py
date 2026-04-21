from __future__ import annotations

from paperbase import sources


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, text: str = "", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def test_search_arxiv_parses_feed(monkeypatch):
    xml = """
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry>
        <id>http://arxiv.org/abs/2401.12345v2</id>
        <title> Test Paper </title>
        <summary> Test abstract </summary>
        <published>2024-01-10T00:00:00Z</published>
        <updated>2024-01-11T00:00:00Z</updated>
        <author><name>Alice</name></author>
        <author><name>Bob</name></author>
        <category term="cs.AI" />
        <arxiv:primary_category term="cs.AI" />
        <link title="pdf" href="https://arxiv.org/pdf/2401.12345.pdf" />
      </entry>
    </feed>
    """

    monkeypatch.setattr(sources.SESSION, "get", lambda *args, **kwargs: _FakeResponse(text=xml))

    papers = sources.search_arxiv("test query")

    assert len(papers) == 1
    assert papers[0].arxiv_id == "2401.12345"
    assert papers[0].title == "Test Paper"
    assert papers[0].authors == "Alice, Bob"
    assert papers[0].primary_category == "cs.AI"


def test_semantic_scholar_enrichment_updates_counts(monkeypatch):
    payload = [
        {
            "paperId": "s2-123",
            "citationCount": 42,
            "influentialCitationCount": 7,
        }
    ]

    monkeypatch.setattr(sources.SESSION, "post", lambda *args, **kwargs: _FakeResponse(json_data=payload))

    paper = sources.PaperMetadata(arxiv_id="2401.12345", title="Paper")
    enriched = sources.enrich_with_semantic_scholar([paper])

    assert enriched[0].semantic_scholar_id == "s2-123"
    assert enriched[0].citation_count == 42
    assert enriched[0].influential_citation_count == 7
