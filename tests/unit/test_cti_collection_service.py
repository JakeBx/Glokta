"""Tests for the shared multi-source advisory collection (collection_service).

cti_ingest_report and cti_ingest_syn both collect from the configured sources, filter to the
lookback window, tag/detect the actor, and dedupe across sources before ingest. This exercises
that orchestration with a fake httpx client.
"""

from datetime import date

from glokta.application.cti.collection_service import collect_advisories

RECENT = "https://thedfirreport.com/2026/06/10/recent-intrusion/"
OLD = "https://thedfirreport.com/2020/01/01/old-intrusion/"
DFIR_FEED = f"<rss><item><link>{RECENT}</link></item><item><link>{OLD}</link></item></rss>"
DFIR_PAGE = (
    '<div class="entry-content">'
    "<h2>Overview</h2><p>Intrusion exploiting CVE-2026-1111 with T1059.</p>"
    "<h2>Indicators of Compromise (IOCs)</h2><p>203.0.113.5</p></div>"
)

CCCS_LISTING = '<a href="/en/alerts-advisories/al26-001-acme">AL26-001</a>'
CCCS_PAGE = (
    '<div class="field--name-body">'
    "<h2>Details</h2><p>Activity exploiting CVE-2026-1111 observed.</p>"
    "<h2>Indicators of compromise</h2><p>203.0.113.5</p></div>"
)


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _Client:
    """Fake httpx client keyed by URL: listings vs detail pages."""

    def get(self, url, params=None, headers=None):
        if url.endswith("/feed/"):
            return _Resp(DFIR_FEED)
        if url.endswith("/en/alerts-advisories"):
            return _Resp(CCCS_LISTING)
        if "thedfirreport.com" in url:
            return _Resp(DFIR_PAGE)
        return _Resp(CCCS_PAGE)


class TestCollectAdvisories:
    NOW = date(2026, 6, 15)

    def test_lookback_filters_old_items_and_tags_source(self):
        kept, stats = collect_advisories(
            _Client(), {}, sources=["dfir"], lookback_days=30,
            max_per_source=10, now=self.NOW,
        )
        assert len(kept) == 1                      # the 2020 report is outside the 30-day window
        assert kept[0]["source_site"] == "dfir"
        assert kept[0]["published"] == date(2026, 6, 10)
        assert "CVE-2026-1111" in kept[0]["text"]
        assert stats["dfir"] == {"fetched": 2, "recent": 1}

    def test_unknown_source_skipped(self):
        kept, stats = collect_advisories(
            _Client(), {}, sources=["dfir", "bogus"], lookback_days=30,
            max_per_source=10, now=self.NOW,
        )
        assert "bogus" not in stats
        assert len(kept) == 1

    def test_cross_source_dedupe_keeps_priority(self):
        # CCCS + both DFIR reports share CVE-2026-1111 -> cross-source duplicates; CCCS outranks
        # DFIR, so the single CCCS copy survives and both DFIR copies drop.
        kept, stats = collect_advisories(
            _Client(), {}, sources=["cccs", "dfir"], lookback_days=3650,
            max_per_source=10, now=self.NOW,
        )
        assert len(kept) == 1
        assert kept[0]["source_site"] == "cccs"
        assert stats["dedupe"]["dropped"] == 2
