"""Tests for cross-source advisory dedup (dedupe.dedupe_advisories).

Joint advisories are co-sealed by CISA + national CERTs, so the same advisory arrives under
different ids/slugs from each source. Dedup keeps one copy, preferring the highest-priority
source, so the benchmark neither double-counts nor reuses text the model trained on elsewhere.
"""

from glokta.infrastructure.cti.dedupe import (
    advisory_signature,
    dedupe_advisories,
    is_duplicate,
)


def _adv(id, source, *, cves=(), techs=(), title=""):
    return {
        "id": id,
        "source_site": source,
        "cves": list(cves),
        "techniques": list(techs),
        "title": title,
    }


class TestSignatureAndDuplicate:
    def test_signature_is_upper_cased_frozensets(self):
        cves, techs = advisory_signature(_adv("X", "cisa", cves=["cve-2024-1"], techs=["t1059"]))
        assert cves == frozenset({"CVE-2024-1"})
        assert techs == frozenset({"T1059"})

    def test_shared_cve_set_is_duplicate(self):
        a = _adv("AA24-1", "cisa", cves=["CVE-2024-1", "CVE-2024-2"], techs=["T1190"])
        b = _adv("AL24-9", "cccs", cves=["CVE-2024-1", "CVE-2024-2"], techs=["T1190", "T1133"])
        assert is_duplicate(a, b)

    def test_distinct_cves_not_duplicate(self):
        a = _adv("AA24-1", "cisa", cves=["CVE-2024-1"], techs=["T1190"])
        b = _adv("AL24-9", "cccs", cves=["CVE-2030-9"], techs=["T1059"])
        assert not is_duplicate(a, b)

    def test_title_fallback_when_no_cves(self):
        # ransomware TTP reports often carry no CVEs -> match on normalised title
        a = _adv("AL23-010", "cccs", title="ALPHV BlackCat Ransomware Targeting")
        b = _adv("aa23-xyz", "cisa", title="ALPHV/BlackCat Ransomware Targeting")
        assert is_duplicate(a, b)

    def test_unrelated_titles_not_duplicate(self):
        a = _adv("X", "cccs", title="Citrix NetScaler vulnerability")
        b = _adv("Y", "cisa", title="APT28 router DNS hijacking")
        assert not is_duplicate(a, b)


class TestDedupeAdvisories:
    def test_keeps_priority_source_and_drops_dup(self):
        cisa = _adv("AA24-100A", "cisa", cves=["CVE-2024-1"], techs=["T1190"])
        cccs = _adv("AL24-009", "cccs", cves=["CVE-2024-1"], techs=["T1190"])
        res = dedupe_advisories([cccs, cisa], priority=("cisa", "cccs", "ncsc"))
        assert [a["id"] for a in res.kept] == ["AA24-100A"]
        assert res.dropped and res.dropped[0][0]["id"] == "AL24-009"
        assert res.dropped[0][1] == "AA24-100A"  # reason references the kept id

    def test_distinct_advisories_all_kept(self):
        items = [
            _adv("AA24-1", "cisa", cves=["CVE-2024-1"]),
            _adv("AL24-2", "cccs", cves=["CVE-2024-99"]),
            _adv("ncsc-x", "ncsc", title="Unique UK report", techs=["T1566"]),
        ]
        res = dedupe_advisories(items)
        assert len(res.kept) == 3 and not res.dropped

    def test_same_source_items_never_collapsed(self):
        # Two distinct CISA advisories that merely share CVEs must both survive — within-source
        # dedup is the ingest layer's job (unique external_id); this filter is cross-source only.
        a = _adv("AA22-152A", "cisa", cves=["CVE-2022-1", "CVE-2022-2"], techs=["T1190"])
        b = _adv("AA22-320A", "cisa", cves=["CVE-2022-1", "CVE-2022-2"], techs=["T1190"])
        res = dedupe_advisories([a, b], priority=("cisa", "cccs", "ncsc"))
        assert {x["id"] for x in res.kept} == {"AA22-152A", "AA22-320A"}
        assert not res.dropped

    def test_three_way_dup_collapses_to_one(self):
        sig = dict(cves=["CVE-2024-7"], techs=["T1133"])
        items = [
            _adv("ncsc-z", "ncsc", **sig),
            _adv("AL24-7", "cccs", **sig),
            _adv("AA24-7A", "cisa", **sig),
        ]
        res = dedupe_advisories(items, priority=("cisa", "cccs", "ncsc"))
        assert [a["id"] for a in res.kept] == ["AA24-7A"]
        assert len(res.dropped) == 2
