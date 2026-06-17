"""Tests for the Malpedia actor connector (alias enrichment for TAA / SYN masking).

Malpedia actors are normalised to the same ``ThreatActor`` shape as the MISP galaxy, so they
merge through ``upsert_threat_actors`` / ``build_taa_indices`` and the alias index unchanged.
"""

from glokta.infrastructure.cti.connectors.galaxy import ThreatActor
from glokta.infrastructure.cti.connectors.malpedia import (
    fetch_malpedia_actors,
    normalise_malpedia_actors,
)


class TestNormalise:
    SYNTH = [
        {"value": "APT28", "meta": {"synonyms": ["Fancy Bear", "Sofacy"]}},
        {"common_name": "Lazarus Group", "synonyms": ["Hidden Cobra"]},
        {"value": "NoAlias"},
        {"description": "no name -> skipped"},
    ]

    def test_returns_threat_actor_objects(self):
        actors = normalise_malpedia_actors(self.SYNTH)
        assert actors and all(isinstance(a, ThreatActor) for a in actors)

    def test_reads_name_and_synonyms_across_shapes(self):
        by = {a.canonical_name: a for a in normalise_malpedia_actors(self.SYNTH)}
        assert {"Fancy Bear", "Sofacy"} <= set(by["APT28"].aliases)   # meta.synonyms
        assert "Hidden Cobra" in by["Lazarus Group"].aliases           # top-level synonyms
        assert by["NoAlias"].aliases == []

    def test_skips_entries_without_a_name(self):
        names = {a.canonical_name for a in normalise_malpedia_actors(self.SYNTH)}
        assert names == {"APT28", "Lazarus Group", "NoAlias"}


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _Client:
    """Fake httpx client: /actors -> slug list; /actor/<slug> -> detail dict."""

    def __init__(self):
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if url.endswith("/actors"):
            return _Resp(["apt28", "lazarus", "extra"])
        if url.endswith("/apt28"):
            return _Resp({"value": "APT28", "meta": {"synonyms": ["Fancy Bear"]}})
        if url.endswith("/lazarus"):
            return _Resp({"value": "Lazarus Group", "meta": {"synonyms": ["Hidden Cobra"]}})
        raise AssertionError(f"unexpected url {url}")


class TestFetch:
    def test_fetch_respects_limit_and_returns_details(self):
        client = _Client()
        details = fetch_malpedia_actors(client, limit=2)
        assert [d["value"] for d in details] == ["APT28", "Lazarus Group"]
        # one list call + two detail calls (limit stops before "extra")
        assert sum(1 for c in client.calls if c.endswith("/actors")) == 1
        assert not any(c.endswith("/extra") for c in client.calls)

    def test_fetch_then_normalise_builds_alias_index(self):
        actors = normalise_malpedia_actors(fetch_malpedia_actors(_Client(), limit=2))
        alias = {al.lower(): a.canonical_name for a in actors for al in a.aliases}
        assert alias["fancy bear"] == "APT28"
        assert alias["hidden cobra"] == "Lazarus Group"
