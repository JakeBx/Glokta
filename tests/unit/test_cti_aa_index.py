"""Tests for the CISA AA-series advisory index pager (report connector)."""

from glokta.infrastructure.cti.connectors.report import fetch_aa_advisory_index

_PAGES = {
    0: """<html><body>
      <a href="/news-events/cybersecurity-advisories/aa25-001a">x</a>
      <a href="/news-events/cybersecurity-advisories/aa25-002a">y</a>
      <nav class="pager">
        <a href="?f%5B0%5D=advisory_type%3A94&amp;page=1">2</a>
        <a href="?f%5B0%5D=advisory_type%3A94&amp;page=2" rel="last">Last</a>
      </nav></body></html>""",
    1: """<html><body>
      <a href="/news-events/cybersecurity-advisories/aa24-010a">x</a>
      <a href="/news-events/cybersecurity-advisories/aa24-011a">y</a>
      </body></html>""",
    2: """<html><body>
      <a href="/news-events/cybersecurity-advisories/aa24-001a">x</a>
      <a href="/news-events/cybersecurity-advisories/aa25-001a">dup</a>
      </body></html>""",
}


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class _Client:
    def __init__(self):
        self.requested_pages = []

    def get(self, url, params=None, headers=None):
        page = int((params or {}).get("page", 0))
        self.requested_pages.append(page)
        return _Resp(_PAGES.get(page, "<html></html>"))


class TestFetchAaAdvisoryIndex:
    def test_pages_through_and_builds_full_urls(self):
        client = _Client()
        urls = fetch_aa_advisory_index(client)
        assert urls[0].endswith("/news-events/cybersecurity-advisories/aa25-001a")
        assert all(u.startswith("https://www.cisa.gov/news-events/cybersecurity-advisories/aa") for u in urls)
        # page 0 advisories come before page 2 advisories (newest-first ordering preserved)
        assert urls.index(urls[0]) < next(i for i, u in enumerate(urls) if u.endswith("aa24-001a"))

    def test_dedupes_across_pages(self):
        urls = fetch_aa_advisory_index(_Client())
        slugs = [u.rsplit("/", 1)[-1] for u in urls]
        assert len(slugs) == len(set(slugs))
        assert slugs == ["aa25-001a", "aa25-002a", "aa24-010a", "aa24-011a", "aa24-001a"]

    def test_respects_max_pages(self):
        client = _Client()
        urls = fetch_aa_advisory_index(client, max_pages=1)
        assert client.requested_pages == [0]
        assert len(urls) == 2

    def test_stops_at_pager_last(self):
        client = _Client()
        fetch_aa_advisory_index(client)
        # pager "last" is page 2, so it must fetch pages 0,1,2 and stop (not run forever)
        assert client.requested_pages == [0, 1, 2]
