"""Unit tests for the _discover_and_queue pipeline function — TDD red-green.

Tests target the pure _discover_and_queue function, not the Prefect @flow wrapper.
fetch_top_models is always mocked to avoid network calls.
"""

import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

os.environ["TESTING"] = "1"

from glokta.infrastructure.db.orm import Model, Run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOP_MODEL = [
    {"id": "meta-llama/llama-3-8b-instruct:free", "pricing": {"prompt": "0", "completion": "0"}},
]

_EXPENSIVE_MODEL = [
    {"id": "openai/gpt-4o", "pricing": {"prompt": "0.000005", "completion": "0.000015"}},
]


def _completed_run_at(db_session, model: Model, days_ago: int) -> Run:
    run = Run(
        model_id=model.id,
        status="complete",
        triggered_by="test",
        completed_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    db_session.add(run)
    db_session.flush()
    return run


# ---------------------------------------------------------------------------
# _discover_and_queue tests
# ---------------------------------------------------------------------------


class TestDiscoverAndQueue:
    def _call(self, db_session, top_models, top_n=10, max_cost=100.0, ttl_days=7):
        from glokta.pipeline.flows import _discover_and_queue

        with patch("glokta.application.scan_service.fetch_top_models", return_value=top_models):
            return _discover_and_queue(
                db_session,
                api_key="test-key",
                top_n=top_n,
                max_scan_cost_usd=max_cost,
                scan_ttl_days=ttl_days,
            )

    def test_creates_pending_run_for_new_model(self, db_session):
        """A model not in the DB gets a new Model row and a pending Run."""
        result = self._call(db_session, _TOP_MODEL)

        model = db_session.query(Model).filter(
            Model.name == "openrouter/meta-llama/llama-3-8b-instruct:free"
        ).first()
        assert model is not None

        run = db_session.query(Run).filter(Run.model_id == model.id).first()
        assert run is not None
        assert run.status == "pending"
        assert run.triggered_by == "scheduled"
        assert result["queued"] == 1
        assert result["skipped"] == 0

    def test_skips_model_with_recent_complete_run(self, db_session):
        """A model with a complete run within TTL is skipped."""
        model = Model(
            name="openrouter/meta-llama/llama-3-8b-instruct:free",
            provider="meta-llama",
            snapshot_date=date.today(),
        )
        db_session.add(model)
        db_session.flush()
        _completed_run_at(db_session, model, days_ago=1)  # fresh — within 7d TTL

        result = self._call(db_session, _TOP_MODEL, ttl_days=7)

        new_runs = (
            db_session.query(Run)
            .filter(Run.model_id == model.id, Run.status == "pending")
            .count()
        )
        assert new_runs == 0
        assert result["skipped"] == 1

    def test_queues_model_with_stale_complete_run(self, db_session):
        """A model whose last complete run is older than TTL gets a new pending run."""
        model = Model(
            name="openrouter/meta-llama/llama-3-8b-instruct:free",
            provider="meta-llama",
            snapshot_date=date.today(),
        )
        db_session.add(model)
        db_session.flush()
        _completed_run_at(db_session, model, days_ago=10)  # stale — beyond 7d TTL

        result = self._call(db_session, _TOP_MODEL, ttl_days=7)

        new_runs = (
            db_session.query(Run)
            .filter(Run.model_id == model.id, Run.status == "pending")
            .count()
        )
        assert new_runs == 1
        assert result["queued"] == 1

    def test_skips_model_with_existing_pending_run(self, db_session):
        """A model that already has a pending run is not queued again."""
        model = Model(
            name="openrouter/meta-llama/llama-3-8b-instruct:free",
            provider="meta-llama",
            snapshot_date=date.today(),
        )
        db_session.add(model)
        db_session.flush()
        existing = Run(model_id=model.id, status="pending", triggered_by="test")
        db_session.add(existing)
        db_session.flush()

        result = self._call(db_session, _TOP_MODEL)

        pending_count = (
            db_session.query(Run)
            .filter(Run.model_id == model.id, Run.status == "pending")
            .count()
        )
        assert pending_count == 1  # only the original, no duplicate
        assert result["skipped"] == 1

    def test_skips_model_with_running_run(self, db_session):
        """A model with a running scan is not queued again."""
        model = Model(
            name="openrouter/meta-llama/llama-3-8b-instruct:free",
            provider="meta-llama",
            snapshot_date=date.today(),
        )
        db_session.add(model)
        db_session.flush()
        running = Run(model_id=model.id, status="running", triggered_by="test")
        db_session.add(running)
        db_session.flush()

        result = self._call(db_session, _TOP_MODEL)

        assert result["skipped"] == 1

    def test_adds_openrouter_prefix_to_model_name(self, db_session):
        """Model ID without openrouter/ prefix gets it prepended."""
        result = self._call(db_session, _TOP_MODEL)

        model = db_session.query(Model).first()
        assert model.name.startswith("openrouter/")

    def test_preserves_openrouter_prefix_if_already_present(self, db_session):
        """Model ID already starting with openrouter/ is not double-prefixed."""
        top_with_prefix = [
            {"id": "openrouter/meta-llama/llama-3-8b-instruct:free", "pricing": {"prompt": "0", "completion": "0"}},
        ]
        self._call(db_session, top_with_prefix)

        model = db_session.query(Model).first()
        assert not model.name.startswith("openrouter/openrouter/")

    def test_returns_queued_and_skipped_counts(self, db_session):
        """Return value is a dict with queued and skipped integer keys."""
        result = self._call(db_session, _TOP_MODEL)

        assert isinstance(result.get("queued"), int)
        assert isinstance(result.get("skipped"), int)

    def test_noop_when_no_models_returned(self, db_session):
        """Empty model list from OpenRouter produces no DB writes."""
        result = self._call(db_session, [])

        assert db_session.query(Model).count() == 0
        assert result["queued"] == 0
        assert result["skipped"] == 0

    def test_fetch_top_models_called_with_correct_args(self, db_session):
        """_discover_and_queue passes api_key, top_n, max_scan_cost_usd to fetch_top_models."""
        from glokta.pipeline.flows import _discover_and_queue

        with patch("glokta.application.scan_service.fetch_top_models", return_value=[]) as mock_fetch:
            _discover_and_queue(
                db_session,
                api_key="my-key",
                top_n=15,
                max_scan_cost_usd=5.0,
                scan_ttl_days=7,
            )

        mock_fetch.assert_called_once_with(
            api_key="my-key",
            top_n=15,
            max_scan_cost_usd=5.0,
        )


# ---------------------------------------------------------------------------
# _discover_and_queue HF tests
# ---------------------------------------------------------------------------

_HF_TOP_MODEL = [{"id": "meta-llama/Llama-3.1-8B-Instruct"}]


def _completed_run_at_hf(db_session, model: Model, days_ago: int) -> Run:
    run = Run(
        model_id=model.id,
        status="complete",
        triggered_by="test",
        completed_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    db_session.add(run)
    db_session.flush()
    return run


class TestDiscoverAndQueueHf:
    def _call(
        self,
        db_session,
        or_models=None,
        hf_models=None,
        hf_token: str = "test-hf-token",
        hf_top_n: int = 5,
        top_n: int = 10,
        ttl_days: int = 7,
    ):
        from glokta.pipeline.flows import _discover_and_queue

        with patch("glokta.application.scan_service.fetch_top_models", return_value=or_models or []):
            with patch("glokta.application.scan_service.fetch_top_hf_models", return_value=hf_models or []):
                return _discover_and_queue(
                    db_session,
                    api_key="or-key",
                    top_n=top_n,
                    max_scan_cost_usd=100.0,
                    scan_ttl_days=ttl_days,
                    hf_token=hf_token,
                    hf_top_n=hf_top_n,
                )

    def test_creates_pending_run_for_new_hf_model(self, db_session):
        """A new HF model gets a huggingface/-prefixed Model row and a pending Run."""
        result = self._call(db_session, hf_models=_HF_TOP_MODEL)

        model = db_session.query(Model).filter(
            Model.name == "huggingface/meta-llama/Llama-3.1-8B-Instruct"
        ).first()
        assert model is not None
        assert model.provider == "meta-llama"

        run = db_session.query(Run).filter(Run.model_id == model.id).first()
        assert run is not None
        assert run.status == "pending"
        assert result["queued"] == 1

    def test_hf_model_name_not_double_prefixed(self, db_session):
        """HF model ID already carrying 'huggingface/' is not double-prefixed."""
        models_with_prefix = [{"id": "huggingface/org/model"}]
        self._call(db_session, hf_models=models_with_prefix)

        model = db_session.query(Model).first()
        assert model.name == "huggingface/org/model"
        assert not model.name.startswith("huggingface/huggingface/")

    def test_hf_discovery_skipped_when_hf_token_empty(self, db_session):
        """When hf_token is empty string, fetch_top_hf_models is never called."""
        from glokta.pipeline.flows import _discover_and_queue

        with patch("glokta.application.scan_service.fetch_top_models", return_value=[]):
            with patch("glokta.application.scan_service.fetch_top_hf_models") as mock_hf:
                _discover_and_queue(
                    db_session,
                    api_key="key",
                    top_n=5,
                    max_scan_cost_usd=10.0,
                    scan_ttl_days=7,
                    hf_token="",
                    hf_top_n=5,
                )

        mock_hf.assert_not_called()

    def test_hf_discovery_skipped_when_hf_top_n_zero(self, db_session):
        """When hf_top_n=0, HF discovery is also skipped."""
        from glokta.pipeline.flows import _discover_and_queue

        with patch("glokta.application.scan_service.fetch_top_models", return_value=[]):
            with patch("glokta.application.scan_service.fetch_top_hf_models") as mock_hf:
                _discover_and_queue(
                    db_session,
                    api_key="key",
                    top_n=5,
                    max_scan_cost_usd=10.0,
                    scan_ttl_days=7,
                    hf_token="tok",
                    hf_top_n=0,
                )

        mock_hf.assert_not_called()

    def test_openrouter_and_hf_models_both_queued(self, db_session):
        """Both OR and HF models queued in a single call are reflected in counts."""
        or_models = [{"id": "mistralai/mistral-7b-instruct:free", "pricing": {"prompt": "0", "completion": "0"}}]
        hf_models = [{"id": "meta-llama/Llama-3.1-8B-Instruct"}]

        result = self._call(db_session, or_models=or_models, hf_models=hf_models)

        assert result["queued"] == 2
        assert db_session.query(Model).count() == 2

    def test_hf_model_skips_within_ttl(self, db_session):
        """A HF model with a recent complete run is skipped."""
        model = Model(
            name="huggingface/meta-llama/Llama-3.1-8B-Instruct",
            provider="meta-llama",
            snapshot_date=date.today(),
        )
        db_session.add(model)
        db_session.flush()
        _completed_run_at_hf(db_session, model, days_ago=1)

        result = self._call(db_session, hf_models=_HF_TOP_MODEL, ttl_days=7)

        assert result["skipped"] == 1
        assert result["queued"] == 0
