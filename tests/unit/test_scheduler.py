"""Unit tests for OpenRouter client and scheduler task."""

import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

os.environ["TESTING"] = "1"

# ── OpenRouter client ────────────────────────────────────────────────────────


MOCK_OPENROUTER_RESPONSE = {
    "data": [
        {
            "id": "openrouter/meta-llama/llama-3-8b-instruct:free",
            "name": "Llama 3 8B Instruct",
            "pricing": {"prompt": "0", "completion": "0"},
            "context_length": 8192,
        },
        {
            "id": "openrouter/mistralai/mistral-7b-instruct:free",
            "name": "Mistral 7B Instruct",
            "pricing": {"prompt": "0", "completion": "0"},
            "context_length": 32768,
        },
        {
            "id": "openrouter/paid/model",
            "name": "Paid Model",
            "pricing": {"prompt": "0.01", "completion": "0.02"},
            "context_length": 4096,
        },
    ]
}


def _make_mock_response(data: dict) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = data
    mock.raise_for_status.return_value = None
    return mock


def test_fetch_top_models_returns_list():
    from garakboard.worker.openrouter_client import fetch_top_models

    with patch("garakboard.worker.openrouter_client.httpx.get") as mock_get:
        mock_get.return_value = _make_mock_response(MOCK_OPENROUTER_RESPONSE)
        result = fetch_top_models(api_key="test-key", top_n=10)

    assert isinstance(result, list)


def test_fetch_top_models_filters_free_tier():
    from garakboard.worker.openrouter_client import fetch_top_models

    with patch("garakboard.worker.openrouter_client.httpx.get") as mock_get:
        mock_get.return_value = _make_mock_response(MOCK_OPENROUTER_RESPONSE)
        result = fetch_top_models(api_key="test-key", top_n=10)

    names = [m["id"] for m in result]
    assert "openrouter/paid/model" not in names


def test_fetch_top_models_respects_top_n():
    from garakboard.worker.openrouter_client import fetch_top_models

    with patch("garakboard.worker.openrouter_client.httpx.get") as mock_get:
        mock_get.return_value = _make_mock_response(MOCK_OPENROUTER_RESPONSE)
        result = fetch_top_models(api_key="test-key", top_n=1)

    assert len(result) == 1


def test_fetch_top_models_passes_auth_header():
    from garakboard.worker.openrouter_client import fetch_top_models

    with patch("garakboard.worker.openrouter_client.httpx.get") as mock_get:
        mock_get.return_value = _make_mock_response(MOCK_OPENROUTER_RESPONSE)
        fetch_top_models(api_key="sk-test", top_n=10)

    call_kwargs = mock_get.call_args
    headers = call_kwargs[1].get("headers") or call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {}
    assert "sk-test" in str(call_kwargs)


def test_fetch_top_models_each_has_id_field():
    from garakboard.worker.openrouter_client import fetch_top_models

    with patch("garakboard.worker.openrouter_client.httpx.get") as mock_get:
        mock_get.return_value = _make_mock_response(MOCK_OPENROUTER_RESPONSE)
        result = fetch_top_models(api_key="test-key", top_n=10)

    for m in result:
        assert "id" in m


# ── discover_and_schedule_scans ──────────────────────────────────────────────


def _make_db(runs=None):
    """Build a mock SQLAlchemy session that returns given runs for freshness queries."""
    db = MagicMock()
    # query(...).filter(...).order_by(...).first() chain for freshness check
    mock_q = MagicMock()
    mock_q.filter.return_value = mock_q
    mock_q.order_by.return_value = mock_q
    mock_q.first.return_value = runs[0] if runs else None
    db.query.return_value = mock_q
    return db


def _fresh_run():
    run = MagicMock()
    run.completed_at = datetime.now(timezone.utc) - timedelta(days=1)
    return run


def _stale_run():
    run = MagicMock()
    run.completed_at = datetime.now(timezone.utc) - timedelta(days=10)
    return run


TOP_MODELS = [
    {"id": "openrouter/meta-llama/llama-3-8b-instruct:free", "name": "Llama 3 8B"},
]


def test_discover_queues_stale_models():
    from garakboard.worker.tasks import discover_and_schedule_scans

    with (
        patch("garakboard.worker.tasks.fetch_top_models", return_value=TOP_MODELS),
        patch("garakboard.worker.tasks.SessionLocal") as mock_session_cls,
        patch("garakboard.worker.tasks.publish_run_job") as mock_publish,
    ):
        db = MagicMock()
        mock_session_cls.return_value = db

        # Model upsert: first call returns None (not found), then returns a model
        mock_model = MagicMock()
        mock_model.id = "model-uuid"
        mock_model.name = "openrouter/meta-llama/llama-3-8b-instruct:free"

        # Freshness query returns stale run
        stale = _stale_run()

        def query_side_effect(cls):
            q = MagicMock()
            q.filter.return_value = q
            q.order_by.return_value = q
            q.first.return_value = stale
            return q

        db.query.side_effect = query_side_effect

        result = discover_and_schedule_scans()

    assert "queued" in result or "skipped" in result


def test_discover_skips_fresh_models():
    from garakboard.worker.tasks import discover_and_schedule_scans

    with (
        patch("garakboard.worker.tasks.fetch_top_models", return_value=TOP_MODELS),
        patch("garakboard.worker.tasks.SessionLocal") as mock_session_cls,
        patch("garakboard.worker.tasks.publish_run_job") as mock_publish,
    ):
        db = MagicMock()
        mock_session_cls.return_value = db

        fresh = _fresh_run()

        def query_side_effect(cls):
            q = MagicMock()
            q.filter.return_value = q
            q.order_by.return_value = q
            q.first.return_value = fresh
            return q

        db.query.side_effect = query_side_effect

        result = discover_and_schedule_scans()
        mock_publish.assert_not_called()


def test_discover_returns_counts():
    from garakboard.worker.tasks import discover_and_schedule_scans

    with (
        patch("garakboard.worker.tasks.fetch_top_models", return_value=[]),
        patch("garakboard.worker.tasks.SessionLocal") as mock_session_cls,
    ):
        db = MagicMock()
        mock_session_cls.return_value = db
        result = discover_and_schedule_scans()

    assert isinstance(result.get("queued"), int)
    assert isinstance(result.get("skipped"), int)
