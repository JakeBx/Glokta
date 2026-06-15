"""Tests for the cve connector's fetch-side helpers: record iteration + NVD merge."""

import json
from datetime import date

from glokta.infrastructure.cti.connectors.cve import (
    extract_nvd_labels,
    iter_cve_records,
    normalise_cve_record,
)

VEC = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"


def _cve_record(cve_id="CVE-2024-1", cwe="CWE-89"):
    return {
        "cveMetadata": {"cveId": cve_id, "datePublished": "2024-05-01T00:00:00Z"},
        "containers": {
            "cna": {
                "descriptions": [{"lang": "en", "value": "An issue"}],
                "problemTypes": [{"descriptions": [{"lang": "en", "cweId": cwe}]}],
            }
        },
    }


class TestIterCveRecords:
    def test_walks_json_files_recursively(self, tmp_path):
        sub = tmp_path / "cves" / "2024" / "1xxx"
        sub.mkdir(parents=True)
        (sub / "CVE-2024-1.json").write_text(json.dumps(_cve_record("CVE-2024-1")))
        (sub / "CVE-2024-2.json").write_text(json.dumps(_cve_record("CVE-2024-2")))
        (sub / "notes.txt").write_text("ignore me")

        records = list(iter_cve_records(tmp_path))
        ids = {r["cveMetadata"]["cveId"] for r in records}
        assert ids == {"CVE-2024-1", "CVE-2024-2"}

    def test_skips_invalid_json(self, tmp_path):
        (tmp_path / "bad.json").write_text("{not valid")
        (tmp_path / "good.json").write_text(json.dumps(_cve_record()))
        assert len(list(iter_cve_records(tmp_path))) == 1


class TestExtractNvdLabels:
    def test_extracts_cwes_and_vector(self):
        nvd = {
            "cve": {
                "id": "CVE-2024-1",
                "weaknesses": [
                    {"description": [{"lang": "en", "value": "CWE-79"}]}
                ],
                "metrics": {
                    "cvssMetricV31": [
                        {"cvssData": {"vectorString": VEC, "baseScore": 9.8}}
                    ]
                },
            }
        }
        cwes, vector, base = extract_nvd_labels(nvd)
        assert cwes == ["CWE-79"]
        assert vector == VEC
        assert base == 9.8

    def test_handles_missing_fields(self):
        assert extract_nvd_labels({"cve": {"id": "x"}}) == ([], None, None)


class TestNvdCrossCheck:
    def test_nvd_added_as_third_source_for_agreement(self):
        record = _cve_record(cwe="CWE-89")
        # Add an ADP that agrees, then NVD that disagrees -> disagree.
        record["containers"]["adp"] = [
            {"providerMetadata": {"shortName": "CISA-ADP"},
             "problemTypes": [{"descriptions": [{"cweId": "CWE-89"}]}]}
        ]
        nvd = {"cve": {"weaknesses": [{"description": [{"value": "CWE-22"}]}]}}
        items = normalise_cve_record(record, "rev", nvd_record=nvd)
        rcm = next(i for i in items if i.task == "rcm")
        assert rcm.authority_agreement == "disagree"
        assert "nvd" in rcm.label_provenance
        # precedence unchanged: CNA still the scored label
        assert rcm.label == {"cwe": ["CWE-89"]}

    def test_nvd_fills_when_cna_and_adp_absent(self):
        record = {
            "cveMetadata": {"cveId": "CVE-2024-9", "datePublished": "2024-05-01T00:00:00Z"},
            "containers": {"cna": {"descriptions": [{"lang": "en", "value": "x"}]}},
        }
        nvd = {"cve": {"weaknesses": [{"description": [{"value": "CWE-200"}]}]}}
        items = normalise_cve_record(record, "rev", nvd_record=nvd)
        rcm = next(i for i in items if i.task == "rcm")
        assert rcm.label == {"cwe": ["CWE-200"]}
        assert rcm.input_date == date(2024, 5, 1)
