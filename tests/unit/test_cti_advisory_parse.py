"""Tests for CISA advisory feed parsing + advisory parsing (report connector, Step 6)."""

from datetime import date

from glokta.infrastructure.cti.connectors.report import (
    detect_actor,
    fetch_advisory_feed,
    parse_advisory,
    split_sections,
)


ADVISORY_TEXT = """\
Summary
APT29 conducted a campaign against the energy sector.

Technical Details
The actors leveraged T1059 and T1566 for initial access. Affected: CVE-2024-1234.

Indicators of Compromise
1.2.3.4
evil.example.com

MITRE ATT&CK Techniques
T1059 Command and Scripting Interpreter

Mitigations
Patch affected systems.
"""


class TestSplitSections:
    def test_splits_on_known_headings(self):
        sections = split_sections(ADVISORY_TEXT)
        assert "Technical Details" in sections
        assert "CVE-2024-1234" in sections["Technical Details"]
        assert "Mitigations" in sections


class TestParseAdvisory:
    def test_extracts_id_techniques_cves(self):
        entry = {
            "title": "AA24-100A: APT29 Targets Energy Sector",
            "link": "https://www.cisa.gov/.../aa24-100a",
            "published_date": date(2024, 4, 9),
        }
        advisory = parse_advisory(entry, ADVISORY_TEXT)
        assert advisory["id"] == "AA24-100A"
        assert "T1059" in advisory["techniques"]
        assert "T1566" in advisory["techniques"]
        assert advisory["cves"] == ["CVE-2024-1234"]
        assert advisory["published"] == date(2024, 4, 9)
        assert "Technical Details" in advisory["sections"]


class TestDetectActor:
    ALIAS = {"apt29": "APT29", "cozy bear": "APT29"}

    def test_finds_alias_in_text(self):
        assert detect_actor("...attributed to Cozy Bear...", self.ALIAS) == "APT29"

    def test_returns_none_when_absent(self):
        assert detect_actor("no actor named here", self.ALIAS) is None


RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item>
  <title>AA24-100A: Recent Advisory</title>
  <link>https://www.cisa.gov/aa24-100a</link>
  <pubDate>Tue, 11 Jun 2024 12:00:00 GMT</pubDate>
</item>
<item>
  <title>AA20-001A: Old Advisory</title>
  <link>https://www.cisa.gov/aa20-001a</link>
  <pubDate>Wed, 01 Jan 2020 12:00:00 GMT</pubDate>
</item>
</channel></rss>
"""


class FakeResp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeClient:
    def __init__(self, text):
        self._text = text

    def get(self, url, headers=None):
        return FakeResp(self._text)


class TestFetchAdvisoryFeed:
    def test_filters_by_lookback(self):
        client = FakeClient(RSS)
        entries = fetch_advisory_feed(client, lookback_days=30, now=date(2024, 6, 14))
        titles = [e["title"] for e in entries]
        assert any("AA24-100A" in t for t in titles)
        assert not any("AA20-001A" in t for t in titles)

    def test_attaches_published_date_and_link(self):
        client = FakeClient(RSS)
        entries = fetch_advisory_feed(client, lookback_days=3650, now=date(2024, 6, 14))
        recent = next(e for e in entries if "AA24-100A" in e["title"])
        assert recent["published_date"] == date(2024, 6, 11)
        assert recent["link"].endswith("aa24-100a")
