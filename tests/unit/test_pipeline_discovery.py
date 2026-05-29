"""Unit tests for sync_model_statuses and queue_stale_models."""

import os
from datetime import date, datetime, timedelta, timezone

import pytest

os.environ["TESTING"] = "1"

from glokta.infrastructure.db.orm import Model, Run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(db, name: str, source: str = "openrouter", status: str = "active") -> Model:
    model = Model(
        name=name,
        provider=name.split("/")[1] if name.count("/") >= 1 else name,
        snapshot_date=date.today(),
        source=source,
        status=status,
    )
    db.add(model)
    db.flush()
    return model


def _or_model(model_id: str) -> dict:
    return {"id": model_id, "pricing": {"prompt": "0", "completion": "0"}}


def _hf_model(model_id: str) -> dict:
    return {"id": model_id}


# ---------------------------------------------------------------------------
# TestSyncModelStatuses
# ---------------------------------------------------------------------------


class TestSyncModelStatuses:
    def _call(self, db, openrouter_models=None, hf_models=None):
        from glokta.application.scan_service import sync_model_statuses
        return sync_model_statuses(db, openrouter_models or [], hf_models or [])

    def test_upserts_new_openrouter_model_with_correct_source(self, db_session):
        """A new OR model is created with source='openrouter' and status='active'."""
        self._call(db_session, openrouter_models=[_or_model("openrouter/mistral/mistral-7b")])

        model = db_session.query(Model).filter_by(name="openrouter/mistral/mistral-7b").first()
        assert model is not None
        assert model.source == "openrouter"
        assert model.status == "active"

    def test_upserts_new_hf_model_with_correct_source(self, db_session):
        """A new HF model is created with source='hf' and status='active'."""
        self._call(db_session, hf_models=[_hf_model("huggingface/meta-llama/Llama-3.1-8B-Instruct")])

        model = db_session.query(Model).filter_by(name="huggingface/meta-llama/Llama-3.1-8B-Instruct").first()
        assert model is not None
        assert model.source == "hf"
        assert model.status == "active"

    def test_marks_previously_active_model_as_archived_when_not_in_top_list(self, db_session):
        """A non-manual model active in DB but absent from the new top list → archived."""
        _make_model(db_session, "openrouter/x/old-model", source="openrouter", status="active")
        db_session.commit()

        self._call(db_session, openrouter_models=[_or_model("openrouter/y/new-model")])

        old = db_session.query(Model).filter_by(name="openrouter/x/old-model").first()
        assert old.status == "archived"

    def test_does_not_archive_manual_source_models(self, db_session):
        """Models with source='manual' are never archived by sync."""
        _make_model(db_session, "openrouter/x/manual-model", source="manual", status="active")
        db_session.commit()

        self._call(db_session, openrouter_models=[_or_model("openrouter/y/new-model")])

        manual = db_session.query(Model).filter_by(name="openrouter/x/manual-model").first()
        assert manual.status == "active"

    def test_reactivates_archived_model_that_returns_to_top_list(self, db_session):
        """An archived model that reappears in the top list is set back to active."""
        _make_model(db_session, "openrouter/x/returning-model", source="openrouter", status="archived")
        db_session.commit()

        self._call(db_session, openrouter_models=[_or_model("openrouter/x/returning-model")])

        model = db_session.query(Model).filter_by(name="openrouter/x/returning-model").first()
        assert model.status == "active"

    def test_returns_upserted_and_archived_counts(self, db_session):
        """Return dict has 'upserted' and 'archived' integer keys."""
        result = self._call(db_session, openrouter_models=[_or_model("openrouter/a/b")])
        assert isinstance(result.get("upserted"), int)
        assert isinstance(result.get("archived"), int)

    def test_hf_model_gets_hf_prefix_if_missing(self, db_session):
        """HF model ID without prefix gets huggingface/ prepended."""
        self._call(db_session, hf_models=[_hf_model("meta-llama/Llama-3.1-8B")])

        model = db_session.query(Model).filter_by(name="huggingface/meta-llama/Llama-3.1-8B").first()
        assert model is not None

    def test_openrouter_model_gets_prefix_if_missing(self, db_session):
        """OR model ID without prefix gets openrouter/ prepended."""
        self._call(db_session, openrouter_models=[_or_model("mistral/mistral-7b")])

        model = db_session.query(Model).filter_by(name="openrouter/mistral/mistral-7b").first()
        assert model is not None

    def test_does_not_archive_openrouter_models_when_openrouter_fetch_returns_empty(self, db_session):
        """If OpenRouter fetch returns [] but HF succeeds, OR models must NOT be archived.

        This guards against the scraping-based OpenRouter client failing silently
        and causing all existing OR models to be incorrectly marked as archived.
        """
        _make_model(db_session, "openrouter/x/existing", source="openrouter", status="active")
        db_session.commit()

        # OpenRouter returns nothing (fetch failed), HF returns one model
        self._call(
            db_session,
            openrouter_models=[],
            hf_models=[_hf_model("huggingface/meta-llama/Llama-3.1-8B")],
        )

        existing = db_session.query(Model).filter_by(name="openrouter/x/existing").first()
        assert existing.status == "active", "OR model must not be archived when OR fetch fails"


# ---------------------------------------------------------------------------
# TestQueueStaleModels
# ---------------------------------------------------------------------------


class TestQueueStaleModels:
    def _call(self, db, ttl_days: int = 7):
        from glokta.application.scan_service import queue_stale_models
        return queue_stale_models(db, scan_ttl_days=ttl_days)

    def test_queues_active_model_with_null_last_scan_at(self, db_session):
        """An ACTIVE model with last_scan_at=None gets a pending run."""
        _make_model(db_session, "openrouter/a/model", source="openrouter", status="active")
        db_session.commit()

        result = self._call(db_session)

        assert result["queued"] == 1
        assert db_session.query(Run).count() == 1

    def test_skips_model_with_recent_last_scan_at(self, db_session):
        """An ACTIVE model scanned within TTL is skipped."""
        model = _make_model(db_session, "openrouter/a/fresh", source="openrouter", status="active")
        model.last_scan_at = datetime.now(timezone.utc) - timedelta(days=1)
        db_session.commit()

        result = self._call(db_session, ttl_days=7)

        assert result["skipped"] == 1
        assert result["queued"] == 0

    def test_queues_model_with_stale_last_scan_at(self, db_session):
        """An ACTIVE model whose last scan is older than TTL gets queued."""
        model = _make_model(db_session, "openrouter/a/stale", source="openrouter", status="active")
        model.last_scan_at = datetime.now(timezone.utc) - timedelta(days=10)
        db_session.commit()

        result = self._call(db_session, ttl_days=7)

        assert result["queued"] == 1

    def test_skips_model_with_existing_pending_run(self, db_session):
        """An ACTIVE model with a pending run already in flight is skipped."""
        model = _make_model(db_session, "openrouter/a/busy", source="openrouter", status="active")
        db_session.add(Run(model_id=model.id, status="pending"))
        db_session.commit()

        result = self._call(db_session)

        pending_count = db_session.query(Run).filter_by(status="pending").count()
        assert pending_count == 1  # no new run added
        assert result["skipped"] == 1

    def test_skips_archived_models(self, db_session):
        """ARCHIVED models are never queued for scanning."""
        _make_model(db_session, "openrouter/a/gone", source="openrouter", status="archived")
        db_session.commit()

        result = self._call(db_session)

        assert result["queued"] == 0

    def test_sets_triggered_by_to_scheduled(self, db_session):
        """Runs created by queue_stale_models have triggered_by='scheduled'."""
        _make_model(db_session, "openrouter/a/model2", source="openrouter", status="active")
        db_session.commit()

        self._call(db_session)

        run = db_session.query(Run).first()
        assert run.triggered_by == "scheduled"
