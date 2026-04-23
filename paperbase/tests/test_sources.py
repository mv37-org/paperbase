from __future__ import annotations

from paperbase import sources


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, text: str = "", json_data=None, headers=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data
        self.headers = headers or {}
        self.ok = 200 <= status_code < 400

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
    outcome = sources.enrich_with_semantic_scholar([paper])

    # Enrichment mutates paper in-place; outcome reports the counts.
    assert paper.semantic_scholar_id == "s2-123"
    assert paper.citation_count == 42
    assert paper.influential_citation_count == 7
    assert outcome.attempted == 1
    assert outcome.enriched == 1
    assert outcome.batches_succeeded == 1
    assert outcome.batches_failed == 0


def test_semantic_scholar_enrichment_retries_on_429_then_succeeds(monkeypatch):
    """On 429 the client must back off and retry rather than silently drop
    the batch — that was the rate-limit hole that left 5/30 papers unenriched
    during the PR #19 validation pass."""
    payload = [{"paperId": "s2-9", "citationCount": 100, "influentialCitationCount": 2}]
    responses = [
        _FakeResponse(status_code=429, headers={"Retry-After": "0"}),
        _FakeResponse(status_code=429, headers={"Retry-After": "0"}),
        _FakeResponse(status_code=200, json_data=payload),
    ]
    call_count = {"n": 0}

    def fake_post(*_args, **_kwargs):
        response = responses[call_count["n"]]
        call_count["n"] += 1
        return response

    monkeypatch.setattr(sources.SESSION, "post", fake_post)
    # Make sleeps a no-op so the test runs instantly.
    monkeypatch.setattr(sources.time, "sleep", lambda _s: None)

    paper = sources.PaperMetadata(arxiv_id="2401.11111", title="Paper")
    outcome = sources.enrich_with_semantic_scholar(
        [paper], max_retries=4, base_backoff_seconds=0, inter_batch_sleep_seconds=0
    )

    assert call_count["n"] == 3, "expected two retries before success"
    assert paper.citation_count == 100
    assert outcome.enriched == 1
    assert outcome.batches_failed == 0


def test_semantic_scholar_enrichment_gives_up_after_retries(monkeypatch):
    """After max_retries 429s, the batch is skipped (not silently pretended
    succeeded) and recorded in batches_failed. The paper is left un-mutated
    so the caller can requeue it through enrich_missing_metadata later."""
    def fake_post(*_args, **_kwargs):
        return _FakeResponse(status_code=429, headers={"Retry-After": "0"})

    monkeypatch.setattr(sources.SESSION, "post", fake_post)
    monkeypatch.setattr(sources.time, "sleep", lambda _s: None)

    paper = sources.PaperMetadata(arxiv_id="2401.22222", title="Paper")
    outcome = sources.enrich_with_semantic_scholar(
        [paper], max_retries=2, base_backoff_seconds=0, inter_batch_sleep_seconds=0
    )

    assert outcome.enriched == 0
    assert outcome.batches_failed == 1
    assert paper.citation_count == 0
    assert paper.semantic_scholar_id is None
