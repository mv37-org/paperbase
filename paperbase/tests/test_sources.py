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


def test_fetch_references_bounds_retry_budget_on_interactive_rate_limit(monkeypatch):
    """Interactive reference lookups must NOT inherit the full 1/4/16/64s
    retry ladder. When the S2 lookup for an un-enriched paper rate-limits,
    the user-facing path should give up quickly and return []."""
    post_calls = {"n": 0}

    def fake_post(*_args, **_kwargs):
        post_calls["n"] += 1
        return _FakeResponse(status_code=429, headers={"Retry-After": "0"})

    monkeypatch.setattr(sources.SESSION, "post", fake_post)
    # get() is never reached because the S2 id resolution fails first.
    monkeypatch.setattr(sources.SESSION, "get", lambda *_a, **_k: _FakeResponse(status_code=500))
    monkeypatch.setattr(sources.time, "sleep", lambda _s: None)

    refs = sources.fetch_references("2401.33333", semantic_scholar_id=None)

    assert refs == []
    # max_retries=1 in interactive mode → 1 initial + 1 retry = 2 POST calls.
    # The default max_retries=4 would be 5 calls and block on full backoff.
    assert post_calls["n"] == 2, (
        f"fetch_references must use a small retry budget; saw {post_calls['n']} calls"
    )


def test_get_related_papers_bounds_retry_budget_on_interactive_rate_limit(monkeypatch):
    """Same as fetch_references: get_related_papers is an interactive path
    and must not block on 1/4/16/64s backoff when S2 rate-limits."""
    post_calls = {"n": 0}

    def fake_post(*_args, **_kwargs):
        post_calls["n"] += 1
        return _FakeResponse(status_code=429, headers={"Retry-After": "0"})

    monkeypatch.setattr(sources.SESSION, "post", fake_post)
    monkeypatch.setattr(sources.SESSION, "get", lambda *_a, **_k: _FakeResponse(status_code=500))
    monkeypatch.setattr(sources.time, "sleep", lambda _s: None)

    related = sources.get_related_papers("2401.44444", semantic_scholar_id=None)

    assert related == []
    assert post_calls["n"] == 2


# --------------------------------------------------------------------------- #
# Papers With Code crosslink (#46 layer 1)
# --------------------------------------------------------------------------- #


class _StubSession:
    """Minimal requests.Session stub that replays a per-URL response table.

    Each entry is ``url_prefix -> _FakeResponse``. Matches on startswith
    so the two-step PWC lookup (paper lookup then repos list) can use
    different fixtures for each stage."""

    def __init__(self, routes: dict[str, _FakeResponse]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, *, params=None, timeout=None, **_kwargs):
        full = url
        if params:
            from urllib.parse import urlencode

            sep = "&" if "?" in full else "?"
            full = f"{full}{sep}{urlencode(params)}"
        self.calls.append(full)
        # Sort by prefix length descending so the repos route
        # (``.../papers/{id}/repositories/``) wins over the paper-lookup
        # route (``.../papers/``) for the second call.
        for prefix in sorted(self.routes.keys(), key=len, reverse=True):
            if url.startswith(prefix):
                return self.routes[prefix]
        return _FakeResponse(status_code=404)


def test_fetch_papers_with_code_repo_picks_official_entry():
    """PWC returns multiple repos per paper; only the one flagged
    ``is_official=True`` should be surfaced. A community reimplementation
    that happens to rank first must lose to the official entry."""
    paper_route = _FakeResponse(
        json_data={"results": [{"id": "paper-xyz"}]},
    )
    repos_route = _FakeResponse(
        json_data={
            "results": [
                {"url": "https://github.com/community/reimpl", "is_official": False},
                {"url": "https://github.com/author/official", "is_official": True},
                {"url": "https://github.com/another/fork", "is_official": False},
            ]
        },
    )
    session = _StubSession(
        {
            "https://paperswithcode.com/api/v1/papers/": paper_route,
            "https://paperswithcode.com/api/v1/papers/paper-xyz/repositories/": repos_route,
        }
    )
    url = sources.fetch_papers_with_code_repo("2401.12345", session=session)
    assert url == "https://github.com/author/official"


def test_fetch_papers_with_code_repo_returns_none_when_no_official():
    """When no repo carries ``is_official=True``, decline rather than
    fall back to a community reimpl. That decision belongs to the
    admin override layer, not the primary signal."""
    paper_route = _FakeResponse(json_data={"results": [{"id": "paper-xyz"}]})
    repos_route = _FakeResponse(
        json_data={
            "results": [
                {"url": "https://github.com/a/impl", "is_official": False},
                {"url": "https://github.com/b/impl", "is_official": False},
            ]
        },
    )
    session = _StubSession(
        {
            "https://paperswithcode.com/api/v1/papers/": paper_route,
            "https://paperswithcode.com/api/v1/papers/paper-xyz/repositories/": repos_route,
        }
    )
    assert sources.fetch_papers_with_code_repo("2401.12345", session=session) is None


def test_fetch_papers_with_code_repo_handles_404_and_empty_results():
    """PWC 404s (paper not in their database) and empty results sets both
    degrade to None so index-time callers can proceed with the ranker
    fallback."""
    # 404
    session = _StubSession({"https://paperswithcode.com/api/v1/papers/?": _FakeResponse(status_code=404)})
    assert sources.fetch_papers_with_code_repo("nonexistent", session=session) is None
    # Empty results
    session = _StubSession(
        {"https://paperswithcode.com/api/v1/papers/?": _FakeResponse(json_data={"results": []})}
    )
    assert sources.fetch_papers_with_code_repo("unknown", session=session) is None


def test_fetch_papers_with_code_repo_degrades_gracefully_on_network_error():
    """Network failures (timeouts, DNS issues, connection refused) must
    not propagate — the whole point of having a ranker fallback is that
    index-time indexing can't be blocked on PWC uptime."""
    import requests

    class FailingSession:
        def get(self, *args, **kwargs):
            raise requests.ConnectionError("simulated network failure")

    assert sources.fetch_papers_with_code_repo("2401.12345", session=FailingSession()) is None


# --------------------------------------------------------------------------- #
# GitHub README verifier (#46 follow-up)
# --------------------------------------------------------------------------- #


def _readme_response(content: str) -> _FakeResponse:
    """Build a GitHub-shaped README response. Real GitHub returns the
    README content base64-encoded under the ``content`` key."""
    import base64

    return _FakeResponse(
        json_data={"content": base64.b64encode(content.encode("utf-8")).decode("ascii")}
    )


def test_verify_repo_owns_paper_accepts_arxiv_id_match():
    """Primary signal: README mentions the arxiv id verbatim. That's
    what BERT's ``google-research/bert`` README does, and what
    arslnb's PR #45 review proposed as the deterministic check."""
    session = _StubSession(
        {
            "https://api.github.com/repos/google-research/bert/readme": _readme_response(
                "# BERT\n\nPaper: https://arxiv.org/abs/1810.04805"
            )
        }
    )
    assert sources.verify_repo_owns_paper(
        "google-research", "bert", "1810.04805", session=session
    )


def test_verify_repo_owns_paper_accepts_long_title_match():
    """Secondary signal: a sufficiently-long paper title appearing
    verbatim in the README. The 20-char floor prevents short titles
    like ``BERT`` from firing on unrelated repos."""
    session = _StubSession(
        {
            "https://api.github.com/repos/owner/repo/readme": _readme_response(
                "Implementation of Some Long Distinctive Paper Title here."
            )
        }
    )
    assert sources.verify_repo_owns_paper(
        "owner",
        "repo",
        "2401.99999",
        title="Some Long Distinctive Paper Title",
        session=session,
    )


def test_verify_repo_owns_paper_rejects_unrelated_readme():
    """README mentions neither the arxiv id nor the title — this is
    the case that catches CoT's ``chaochun/nlu-asdiv-dataset`` (README
    about the dataset, not the paper) and the ``tensorflow/models`` /
    GraphSAGE mismatch from the 30-paper benchmark."""
    session = _StubSession(
        {
            "https://api.github.com/repos/owner/dataset/readme": _readme_response(
                "Generic dataset README with no paper references."
            )
        }
    )
    assert not sources.verify_repo_owns_paper(
        "owner", "dataset", "1810.04805", session=session
    )


def test_verify_repo_owns_paper_rejects_404_repo():
    """A 404 means the repo doesn't exist — happens when the regex
    captured a malformed URL like ``openai/gpt-3supplemental`` from
    a text-layout corruption. Verifier must return False so the
    candidate gets demoted to None rather than hitting auto-enqueue."""
    session = _StubSession(
        {
            "https://api.github.com/repos/openai/gpt-3supplemental/readme": _FakeResponse(
                status_code=404
            )
        }
    )
    assert not sources.verify_repo_owns_paper(
        "openai", "gpt-3supplemental", "2005.14165", session=session
    )


def test_verify_repo_owns_paper_short_title_does_not_fire():
    """Short title (<20 chars) must NOT be used as a verification
    signal — common-name overlaps would yield false positives.
    ``BERT`` is the canonical example: many unrelated repos mention
    BERT in their README without being the paper's repo."""
    session = _StubSession(
        {
            "https://api.github.com/repos/random/repo/readme": _readme_response(
                "We use BERT embeddings extensively in this codebase."
            )
        }
    )
    assert not sources.verify_repo_owns_paper(
        "random", "repo", "1810.04805", title="BERT", session=session
    )


def test_verify_repo_owns_paper_degrades_gracefully_on_network_error():
    """Same robustness contract as the PWC adapter — index-time
    indexing must not be blocked on GitHub uptime. Network
    exceptions return False so the caller can fall through."""
    import requests

    class FailingSession:
        def get(self, *args, **kwargs):
            raise requests.ConnectionError("simulated")

    assert not sources.verify_repo_owns_paper(
        "owner", "repo", "1810.04805", session=FailingSession()
    )
