"""Tests for recent-slice CVE fetch via cvelistV5 delta.json."""

from datetime import date

from glokta.infrastructure.cti.connectors.cve import (
    recent_delta_links,
    fetch_recent_cve_records,
)


DELTA = {
    "fetchTime": "2026-06-15T00:36:16.704Z",
    "numberOfChanges": 3,
    "new": [
        {
            "cveId": "CVE-2026-1",
            "githubLink": "https://raw.githubusercontent.com/.../CVE-2026-1.json",
            "dateUpdated": "2026-06-14T10:00:00.000Z",
        }
    ],
    "updated": [
        {
            "cveId": "CVE-2026-2",
            "githubLink": "https://raw.githubusercontent.com/.../CVE-2026-2.json",
            "dateUpdated": "2026-06-13T10:00:00.000Z",
        },
        {
            "cveId": "CVE-2025-OLD",
            "githubLink": "https://raw.githubusercontent.com/.../CVE-2025-OLD.json",
            "dateUpdated": "2025-01-01T10:00:00.000Z",
        },
    ],
}


class TestRecentDeltaLinks:
    def test_filters_by_lookback_window(self):
        links = recent_delta_links(DELTA, lookback_days=3, now=date(2026, 6, 15))
        ids = {cve_id for cve_id, _ in links}
        assert ids == {"CVE-2026-1", "CVE-2026-2"}  # old one dropped

    def test_includes_both_new_and_updated(self):
        links = recent_delta_links(DELTA, lookback_days=3650, now=date(2026, 6, 15))
        assert len(links) == 3


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class FakeClient:
    """Returns delta.json for the delta URL, and a stub record for each githubLink."""

    def __init__(self, delta):
        self._delta = delta
        self.fetched: list[str] = []

    def get(self, url, headers=None):
        self.fetched.append(url)
        if url.endswith("delta.json"):
            return FakeResponse(self._delta)
        cve_id = url.split("/")[-1].replace(".json", "")
        return FakeResponse({"cveMetadata": {"cveId": cve_id}})


class TestFetchRecentCveRecords:
    def test_fetches_only_recent_records(self):
        client = FakeClient(DELTA)
        records = fetch_recent_cve_records(client, lookback_days=3, now=date(2026, 6, 15))
        ids = {r["cveMetadata"]["cveId"] for r in records}
        assert ids == {"CVE-2026-1", "CVE-2026-2"}
        # delta.json fetched once, then one GET per recent record
        assert sum(1 for u in client.fetched if u.endswith("delta.json")) == 1
        assert len(records) == 2
