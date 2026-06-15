"""Unit tests for the cve connector's pure normalise step (CVE JSON 5.0 + ADP).

No git/network: we feed captured CVE-record dicts and assert the produced
NormalisedCtiItem rows for RCM and VSP, including provenance and authority agreement.
"""

from datetime import date

from glokta.infrastructure.cti.connectors.cve import normalise_cve_record


def _record(*, cna_cwe=None, adp_cwe=None, cna_vector=None):
    cna: dict = {
        "descriptions": [{"lang": "en", "value": "A SQL injection in Acme allows RCE."}],
    }
    if cna_cwe is not None:
        cna["problemTypes"] = [
            {"descriptions": [{"lang": "en", "cweId": cna_cwe, "description": "x"}]}
        ]
    if cna_vector is not None:
        cna["metrics"] = [{"cvssV3_1": {"vectorString": cna_vector, "baseScore": 9.8}}]

    containers: dict = {"cna": cna}
    if adp_cwe is not None:
        containers["adp"] = [
            {
                "providerMetadata": {
                    "shortName": "CISA-ADP",
                    "dateUpdated": "2024-05-20T10:00:00.000Z",
                },
                "problemTypes": [{"descriptions": [{"lang": "en", "cweId": adp_cwe}]}],
            }
        ]
    return {
        "cveMetadata": {
            "cveId": "CVE-2024-1234",
            "state": "PUBLISHED",
            "datePublished": "2024-05-01T10:00:00.000Z",
            "dateUpdated": "2024-05-20T10:00:00.000Z",
        },
        "containers": containers,
    }


VEC = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"


class TestNormaliseCveRecord:
    def test_produces_rcm_and_vsp_items(self):
        items = normalise_cve_record(_record(cna_cwe="CWE-89", cna_vector=VEC), "rev1")
        tasks = {i.task for i in items}
        assert tasks == {"rcm", "vsp"}
        assert all(i.external_id == "CVE-2024-1234" for i in items)
        assert all(i.source == "cve" for i in items)
        assert all(i.source_revision == "rev1" for i in items)

    def test_rcm_label_and_input(self):
        items = normalise_cve_record(_record(cna_cwe="CWE-89"), "rev1")
        rcm = next(i for i in items if i.task == "rcm")
        assert rcm.label == {"cwe": ["CWE-89"]}
        assert "SQL injection" in rcm.input_text
        assert rcm.input_date == date(2024, 5, 1)

    def test_vsp_label_carries_vector_and_base(self):
        items = normalise_cve_record(_record(cna_vector=VEC), "rev1")
        vsp = next(i for i in items if i.task == "vsp")
        assert vsp.label["vector"] == VEC
        assert vsp.label["base_score"] == 9.8

    def test_authority_agreement_when_cna_and_adp_agree(self):
        items = normalise_cve_record(_record(cna_cwe="CWE-89", adp_cwe="CWE-89"), "r")
        rcm = next(i for i in items if i.task == "rcm")
        assert rcm.authority_agreement == "agree"
        assert rcm.label_provenance == {"cna": ["CWE-89"], "adp": ["CWE-89"]}

    def test_authority_agreement_when_sources_disagree(self):
        items = normalise_cve_record(_record(cna_cwe="CWE-89", adp_cwe="CWE-79"), "r")
        rcm = next(i for i in items if i.task == "rcm")
        assert rcm.authority_agreement == "disagree"
        # precedence: CNA is primary, so the scored label follows CNA
        assert rcm.label == {"cwe": ["CWE-89"]}

    def test_single_source_marked_single(self):
        items = normalise_cve_record(_record(cna_cwe="CWE-89"), "r")
        rcm = next(i for i in items if i.task == "rcm")
        assert rcm.authority_agreement == "single"

    def test_adp_fills_gap_when_cna_has_no_cwe(self):
        items = normalise_cve_record(_record(cna_cwe=None, adp_cwe="CWE-787"), "r")
        rcm = next(i for i in items if i.task == "rcm")
        assert rcm.label == {"cwe": ["CWE-787"]}
        # label became available at ADP enrichment time, not CVE publication
        assert rcm.label_date == date(2024, 5, 20)

    def test_no_labels_yields_no_items(self):
        assert normalise_cve_record(_record(), "r") == []

    def test_difficulty_includes_description_length(self):
        items = normalise_cve_record(_record(cna_cwe="CWE-89"), "r")
        rcm = next(i for i in items if i.task == "rcm")
        assert rcm.difficulty["description_length"] > 0
