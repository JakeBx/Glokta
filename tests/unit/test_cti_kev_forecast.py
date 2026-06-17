"""Tests for the kev connector and the Forecast task (Step 3)."""

from datetime import date

import pytest

from glokta.application.cti.forecast_service import (
    resolve_forecast_labels,
    seed_forecast_items,
    upsert_kev_entries,
)
from glokta.application.cti.scoring_aggregate import auc_for_run
from glokta.infrastructure.cti.connectors.kev import normalise_kev_feed
from glokta.infrastructure.cti.evaluator import evaluate_item
from glokta.infrastructure.cti.prompts import parse_response
from glokta.infrastructure.db.orm import CtiItem, CtiKev, CtiResult


def _cve_record(cve_id="CVE-2024-1"):
    return {
        "cveMetadata": {"cveId": cve_id, "datePublished": "2024-05-01T00:00:00Z"},
        "containers": {"cna": {"descriptions": [{"lang": "en", "value": "An RCE bug"}]}},
    }


KEV_FEED = {
    "vulnerabilities": [
        {
            "cveID": "CVE-2024-1",
            "vendorProject": "Acme",
            "product": "Widget",
            "dateAdded": "2024-06-01",
        },
        {"cveID": "CVE-2024-2", "vendorProject": "Beta", "product": "Gizmo", "dateAdded": "2024-06-05"},
    ]
}


class TestNormaliseKevFeed:
    def test_parses_entries(self):
        entries = normalise_kev_feed(KEV_FEED)
        assert {e.cve_id for e in entries} == {"CVE-2024-1", "CVE-2024-2"}
        first = next(e for e in entries if e.cve_id == "CVE-2024-1")
        assert first.date_added == date(2024, 6, 1)
        assert first.vendor == "Acme"


class TestUpsertKevEntries:
    def test_inserts_and_is_idempotent(self, db_session):
        entries = normalise_kev_feed(KEV_FEED)
        upsert_kev_entries(db_session, entries)
        upsert_kev_entries(db_session, entries)  # second run must not duplicate
        assert db_session.query(CtiKev).count() == 2


class TestSeedForecastItems:
    def test_seeds_unresolved_item_anchored_at_publication(self, db_session):
        created = seed_forecast_items(db_session, [_cve_record("CVE-2024-1")], "rev")
        assert created == 1
        item = db_session.query(CtiItem).filter(CtiItem.task == "forecast").one()
        assert item.label == {"exploited": False}
        assert item.first_available_date == date(2024, 5, 1)

    def test_does_not_duplicate_existing(self, db_session):
        seed_forecast_items(db_session, [_cve_record("CVE-2024-1")], "rev")
        seed_forecast_items(db_session, [_cve_record("CVE-2024-1")], "rev")
        assert db_session.query(CtiItem).filter(CtiItem.task == "forecast").count() == 1


class TestResolveForecastLabels:
    def test_flips_exploited_and_sets_label_date(self, db_session):
        seed_forecast_items(db_session, [_cve_record("CVE-2024-1")], "rev")
        entries = normalise_kev_feed(KEV_FEED)
        resolved = resolve_forecast_labels(db_session, entries)
        assert resolved == 1  # only CVE-2024-1 has a forecast item
        item = db_session.query(CtiItem).filter(CtiItem.task == "forecast").one()
        assert item.label == {"exploited": True}
        assert item.label_date == date(2024, 6, 1)

    def test_resolution_advances_temporal_anchor_to_kev_date(self, db_session):
        # CVE published 2024-05-01, KEV-added 2024-06-01 -> anchor must move to the later date,
        # so the exploited label is not treated as available at publication (pre/post-cutoff).
        seed_forecast_items(db_session, [_cve_record("CVE-2024-1")], "rev")
        item = db_session.query(CtiItem).filter(CtiItem.task == "forecast").one()
        assert item.first_available_date == date(2024, 5, 1)  # publication, pre-resolution

        resolve_forecast_labels(db_session, normalise_kev_feed(KEV_FEED))
        db_session.refresh(item)
        assert item.first_available_date == date(2024, 6, 1)  # max(pub, kev_date)


class TestForecastParsing:
    def test_extracts_decimal_probability(self):
        assert parse_response("forecast", "Probability: 0.85") == pytest.approx(0.85)

    def test_extracts_percentage(self):
        assert parse_response("forecast", "I'd say 70%") == pytest.approx(0.70)

    def test_no_number_returns_none(self):
        assert parse_response("forecast", "uncertain") is None


class TestForecastEvaluation:
    def test_confident_correct_scores_high(self):
        scored = evaluate_item("forecast", {"exploited": True}, "0.95")
        assert scored.score > 0.9
        assert scored.correct is True

    def test_confident_wrong_scores_low(self):
        scored = evaluate_item("forecast", {"exploited": False}, "0.95")
        assert scored.score < 0.1
        assert scored.correct is False


class TestAucForRun:
    def test_perfect_separation_is_one(self, db_session):
        results = [
            CtiResult(score_breakdown={"prob": 0.9, "outcome": 1.0}),
            CtiResult(score_breakdown={"prob": 0.8, "outcome": 1.0}),
            CtiResult(score_breakdown={"prob": 0.2, "outcome": 0.0}),
            CtiResult(score_breakdown={"prob": 0.1, "outcome": 0.0}),
        ]
        assert auc_for_run(results) == pytest.approx(1.0)

    def test_single_class_returns_none(self):
        results = [CtiResult(score_breakdown={"prob": 0.5, "outcome": 1.0})]
        assert auc_for_run(results) is None
