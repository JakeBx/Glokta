"""Tests for multi-source advisory parsing (NCSC / CCCS) via parse_source_advisory.

The CISA parser core (_AdvisorySectionParser + _classify_heading) is reused; each source adds
its own body-container markers, id pattern, and extra drop-keywords so its conclusion/chrome
sections are withheld from the reconstructed inputs.
"""

from glokta.infrastructure.cti.connectors.report import (
    SOURCES,
    fetch_source_index,
    parse_source_advisory,
)

# CCCS-style page: Drupal body container; CCCS-specific chrome headings (Audience/Purpose/
# Suggested actions/TTP) that must be dropped, plus observation sections kept.
CCCS_PAGE = """<html><body>
<nav>menu junk</nav>
<div class="field--name-body">
  <h2>Audience</h2><p>This advisory is intended for IT professionals.</p>
  <h2>Purpose</h2><p>To warn of ALPHV activity.</p>
  <h2>Details</h2><p>The actors exploited CVE-2024-21887 and beaconed to 203.0.113.10.</p>
  <h2>Tactics, techniques, and procedures (TTP)</h2><p>They relied on T1486 for impact.</p>
  <h2>Suggested actions</h2><p>Patch immediately.</p>
  <h2>Indicators of compromise</h2><p>203.0.113.10</p>
  <h2>MITRE ATT&amp;CK techniques</h2><p>T1190; T1486</p>
  <h2>References</h2><p>[1] link</p>
</div>
<footer>Date modified: 2024-04-09</footer>
</body></html>"""

# NCSC-style page: no Drupal body class; headings include Executive summary / Mitigation chrome.
NCSC_PAGE = """<html><body>
<main>
  <h2>Executive summary</h2><p>We assess APT28 is responsible.</p>
  <h2>Introduction</h2><p>Background on the campaign.</p>
  <h2>Technical analysis</h2><p>The actors exploited CVE-2023-50224; C2 at 198.51.100.5.</p>
  <h2>Indicators of compromise</h2><p>198.51.100.5</p>
  <h2>MITRE ATT&amp;CK</h2><p>T1190; T1557</p>
  <h2>Mitigation</h2><p>Update firmware.</p>
</main></body></html>"""


class TestSourceRegistry:
    def test_known_sources_present(self):
        assert {"cisa", "cccs", "ncsc"} <= set(SOURCES)


class TestParseCccs:
    def setup_method(self):
        self.adv = parse_source_advisory(
            {"link": "https://www.cyber.gc.ca/en/alerts-advisories/alphvblackcat", "title": "ALPHV BlackCat"},
            CCCS_PAGE, "cccs",
        )

    def test_tags_source_site(self):
        assert self.adv["source_site"] == "cccs"

    def test_keeps_observations(self):
        kept = self.adv["sections"]
        body = " ".join(kept.get(k, "") for k in ("Overview", "Technical Details", "Indicators of Compromise"))
        assert "exploited CVE-2024-21887" in body
        assert "203.0.113.10" in body

    def test_drops_cccs_chrome_and_conclusions(self):
        kept = self.adv["sections"]
        body = " ".join(kept.get(k, "") for k in ("Overview", "Technical Details", "Indicators of Compromise"))
        assert "intended for IT professionals" not in body  # Audience dropped
        assert "To warn of ALPHV" not in body                # Purpose dropped
        assert "relied on T1486 for impact" not in body      # TTP conclusion dropped
        assert "Patch immediately" not in body               # Suggested actions dropped
        assert "[1] link" not in body                        # References dropped

    def test_extracts_techniques_and_cves(self):
        assert "T1190" in self.adv["techniques"]
        assert self.adv["cves"] == ["CVE-2024-21887"]

    def test_reconstruct_inputs_free_of_conclusions(self):
        from glokta.infrastructure.cti.claim_extraction import reconstruct_inputs
        inputs = reconstruct_inputs(self.adv)
        assert "CVE-2024-21887" in inputs
        assert "relied on T1486" not in inputs   # TTP conclusion withheld


class TestParseNcsc:
    def setup_method(self):
        self.adv = parse_source_advisory(
            {"link": "https://www.ncsc.gov.uk/news/apt28-router-dns", "title": "APT28 routers"},
            NCSC_PAGE, "ncsc",
        )

    def test_keeps_technical_analysis(self):
        body = " ".join(
            self.adv["sections"].get(k, "")
            for k in ("Overview", "Technical Details", "Indicators of Compromise")
        )
        assert "exploited CVE-2023-50224" in body
        assert "198.51.100.5" in body

    def test_drops_summary_and_mitigation(self):
        body = " ".join(
            self.adv["sections"].get(k, "")
            for k in ("Overview", "Technical Details", "Indicators of Compromise")
        )
        assert "We assess APT28 is responsible" not in body  # Executive summary dropped
        assert "Update firmware" not in body                  # Mitigation dropped


# DFIR Report-style page: WordPress entry-content, ATT&CK-tactic narrative sections (kept),
# Diamond Model / Services / Detection chrome (dropped), IOCs bucket. Date lives in the URL.
DFIR_PAGE = """<html><body>
<header>nav</header>
<div class="entry-content">
  <h2>Background</h2><p>An intrusion investigated by the team.</p>
  <h2>Overview</h2><p>The case began with a phishing email.</p>
  <h2>Initial Access</h2><p>The actors sent a phishing email exploiting CVE-2025-12345 (T1566).</p>
  <h2>Execution</h2><p>PowerShell ran (T1059.001) and beaconed to 203.0.113.10.</p>
  <h2>Services</h2><p>We offer private threat feeds and training.</p>
  <h2>Indicators of Compromise (IOCs)</h2><p>203.0.113.10</p>
  <h2>MITRE ATT&amp;CK</h2><p>T1566 Phishing; T1059.001 PowerShell</p>
  <h2>Diamond Model</h2><p>Adversary attributed to a LockBit affiliate.</p>
  <h2>Detection Engineering and Threat Hunting</h2><p>A Sigma rule for the beacon.</p>
</div>
<footer>Post navigation - Related Posts</footer>
</body></html>"""


class TestParseDfir:
    def setup_method(self):
        self.adv = parse_source_advisory(
            {"link": "https://thedfirreport.com/2026/02/23/apache-activemq-leads-to-lockbit/"},
            DFIR_PAGE, "dfir",
        )

    def test_registry_and_source_tag(self):
        assert "dfir" in SOURCES
        assert self.adv["source_site"] == "dfir"

    def test_date_extracted_from_url(self):
        from datetime import date
        assert self.adv["published"] == date(2026, 2, 23)

    def test_keeps_tactic_narrative_as_technical_details(self):
        body = " ".join(
            self.adv["sections"].get(k, "")
            for k in ("Overview", "Technical Details", "Indicators of Compromise")
        )
        assert "phishing email exploiting CVE-2025-12345" in body
        assert "PowerShell ran" in body
        assert "203.0.113.10" in body

    def test_drops_attribution_and_chrome(self):
        body = " ".join(
            self.adv["sections"].get(k, "")
            for k in ("Overview", "Technical Details", "Indicators of Compromise")
        )
        assert "LockBit affiliate" not in body          # Diamond Model attribution dropped
        assert "private threat feeds" not in body        # Services plug dropped
        assert "Sigma rule" not in body                  # Detection section dropped

    def test_extracts_techniques_and_cves(self):
        assert "T1566" in self.adv["techniques"] and "T1059" in self.adv["techniques"]
        assert self.adv["cves"] == ["CVE-2025-12345"]

    def test_reconstruct_inputs_free_of_attribution(self):
        from glokta.infrastructure.cti.claim_extraction import reconstruct_inputs
        inputs = reconstruct_inputs(self.adv)
        assert "CVE-2025-12345" in inputs
        assert "LockBit affiliate" not in inputs          # Diamond Model conclusion withheld


class TestFetchSourceIndex:
    def test_scrapes_advisory_links_from_listing(self):
        listing = """
        <a href="/en/alerts-advisories/alphvblackcat-ransomware">ALPHV</a>
        <a href="/en/alerts/apt-actor-brute-force">APT brute force</a>
        <a href="/en/about-us">about (not an advisory)</a>
        """

        class _Resp:
            text = listing
            def raise_for_status(self): pass

        class _Client:
            def get(self, url, params=None, headers=None):
                return _Resp()

        urls = fetch_source_index(_Client(), "cccs", max_pages=1)
        assert any("alphvblackcat-ransomware" in u for u in urls)
        assert any("apt-actor-brute-force" in u for u in urls)
        assert all(u.startswith("https://www.cyber.gc.ca") for u in urls)
        assert not any("about-us" in u for u in urls)  # non-advisory link excluded
