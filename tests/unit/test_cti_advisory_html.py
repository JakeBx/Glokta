"""Tests for the AA-series advisory HTML section parser (parse_advisory_page)."""

from glokta.infrastructure.cti.connectors.report import parse_advisory_page

# A miniature CISA-style page: body container, observation + conclusion + chrome sections.
PAGE = """<html><head><title>t</title></head><body>
<nav>site nav junk that must be dropped</nav>
<div class="field--name-body">
  <time datetime="2024-04-09">April 09, 2024</time>
  <h2>Summary</h2>
  <p>CISA attributes this activity to APT29 (a CONCLUSION).</p>
  <h2>Technical Details</h2>
  <p>The actors exploited CVE-2024-21887 on an appliance; C2 beacons reached 203.0.113.10.</p>
  <h3>Command and Control</h3>
  <p>Beaconing used T1071 over HTTPS.</p>
  <h2>Indicators of Compromise</h2>
  <p>203.0.113.10</p>
  <h2>MITRE ATT&amp;CK Techniques</h2>
  <p>T1190 Exploit Public-Facing Application; T1071 Application Layer Protocol</p>
  <h2>Mitigations</h2>
  <p>Patch the appliance.</p>
  <h2>References</h2>
  <p>[1] vendor link</p>
  <h2>Tags</h2><p>Cybersecurity Advisory</p>
</div>
<footer>Related Advisories ... more junk</footer>
</body></html>"""


class TestParseAdvisoryPage:
    def setup_method(self):
        self.adv = parse_advisory_page({"link": ".../aa24-100a"}, PAGE)

    def test_id_and_date(self):
        assert self.adv["id"] == "AA24-100A"
        from datetime import date
        assert self.adv["published"] == date(2024, 4, 9)

    def test_keeps_observation_sections(self):
        secs = self.adv["sections"]
        assert "exploited CVE-2024-21887" in secs["Technical Details"]
        assert "Beaconing used T1071" in secs["Technical Details"]  # ATT&CK-tactic narrative kept
        assert "203.0.113.10" in secs["Indicators of Compromise"]

    def test_drops_conclusion_and_chrome_sections(self):
        secs = self.adv["sections"]
        # Summary / Mitigations / MITRE table / References / Tags must NOT be in kept buckets
        kept = " ".join(secs.get(k, "") for k in ("Overview", "Technical Details", "Indicators of Compromise"))
        assert "attributes this activity" not in kept   # Summary dropped
        assert "Patch the appliance" not in kept          # Mitigations dropped
        assert "Exploit Public-Facing Application" not in kept  # MITRE table dropped
        assert "vendor link" not in kept                  # References dropped
        assert "Cybersecurity Advisory" not in kept       # Tags chrome dropped

    def test_extracts_techniques_and_cves_from_body(self):
        assert "T1190" in self.adv["techniques"] and "T1071" in self.adv["techniques"]
        assert self.adv["cves"] == ["CVE-2024-21887"]

    def test_reconstruct_inputs_uses_only_kept_sections(self):
        from glokta.infrastructure.cti.claim_extraction import reconstruct_inputs
        inputs = reconstruct_inputs(self.adv)
        assert "CVE-2024-21887" in inputs and "203.0.113.10" in inputs
        assert "attributes this activity" not in inputs   # conclusion not leaked via Summary
        assert "Patch the appliance" not in inputs
