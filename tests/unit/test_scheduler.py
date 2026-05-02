"""Unit tests for OpenRouter client and scheduler task."""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

os.environ["TESTING"] = "1"


# ── Fixtures ─────────────────────────────────────────────────────────────────


# Minimal RSC payload snippet containing a rankingData array.
# Real payload has surrounding Next.js wrapping; the parser should extract
# just the array.
MOCK_RSC_PAYLOAD = r'''
anything_before
"rankingData":[{"date":"2026-04-29 00:00:00","model_permaslug":"acme/big-model-20260420","variant":"standard","total_completion_tokens":100,"total_prompt_tokens":2000,"count":10},{"date":"2026-04-29 00:00:00","model_permaslug":"acme/cheap-model-20260421","variant":"standard","total_completion_tokens":50,"total_prompt_tokens":800,"count":5},{"date":"2026-04-29 00:00:00","model_permaslug":"acme/free-model-20260422","variant":"free","total_completion_tokens":30,"total_prompt_tokens":500,"count":3},{"date":"2026-04-29 00:00:00","model_permaslug":"acme/budget-model-20260423","variant":"standard","total_completion_tokens":20,"total_prompt_tokens":400,"count":2}]
anything_after
'''

# /api/v1/models catalog shape — catalog entry's canonical_slug matches ranking permaslug
MOCK_CATALOG = {
    "data": [
        {
            "id": "acme/big-model",
            "canonical_slug": "acme/big-model-20260420",
            "name": "Big Model (expensive)",
            # $30/M prompt, $180/M completion — this is GPT-5.5-Pro territory
            "pricing": {"prompt": "0.00003", "completion": "0.00018"},
        },
        {
            "id": "acme/cheap-model",
            "canonical_slug": "acme/cheap-model-20260421",
            "name": "Cheap Model",
            # $0.4/M prompt, $2/M completion
            "pricing": {"prompt": "0.0000004", "completion": "0.000002"},
        },
        {
            "id": "acme/free-model:free",
            "canonical_slug": "acme/free-model-20260422",
            "name": "Free Model",
            "pricing": {"prompt": "0", "completion": "0"},
        },
        {
            "id": "acme/budget-model",
            "canonical_slug": "acme/budget-model-20260423",
            "name": "Budget Model",
            # $1/M prompt, $3/M completion
            "pricing": {"prompt": "0.000001", "completion": "0.000003"},
        },
    ]
}


def _make_rsc_response(text: str) -> MagicMock:
    mock = MagicMock()
    mock.text = text
    mock.raise_for_status.return_value = None
    return mock


def _make_json_response(data: dict) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = data
    mock.raise_for_status.return_value = None
    return mock


def _patched_http():
    """
    Patch httpx.get so the first call (rankings RSC) returns the mock RSC payload
    and the second call (catalog) returns the mock JSON catalog.
    """
    def side_effect(url, *args, **kwargs):
        if "rankings" in url:
            return _make_rsc_response(MOCK_RSC_PAYLOAD)
        return _make_json_response(MOCK_CATALOG)

    return patch("garakboard.worker.openrouter_client.httpx.get", side_effect=side_effect)


# ── fetch_top_models ─────────────────────────────────────────────────────────


def test_fetch_top_models_returns_list():
    from garakboard.worker.openrouter_client import fetch_top_models
    with _patched_http():
        result = fetch_top_models(api_key="test-key", top_n=10)
    assert isinstance(result, list)


def test_fetch_top_models_orders_by_ranking_tokens():
    """Models come back in the order the rankings page specifies (by total tokens)."""
    from garakboard.worker.openrouter_client import fetch_top_models
    with _patched_http():
        result = fetch_top_models(api_key="test-key", top_n=10, max_scan_cost_usd=1000.0)
    ids = [m["id"] for m in result]
    # Ordered by (prompt+completion) tokens desc: big > cheap > free > budget
    assert ids == ["acme/big-model", "acme/cheap-model", "acme/free-model:free", "acme/budget-model"]


def test_fetch_top_models_each_has_id_and_pricing():
    from garakboard.worker.openrouter_client import fetch_top_models
    with _patched_http():
        result = fetch_top_models(api_key="test-key", top_n=10, max_scan_cost_usd=1000.0)
    for m in result:
        assert "id" in m
        assert "pricing" in m


def test_fetch_top_models_respects_top_n():
    from garakboard.worker.openrouter_client import fetch_top_models
    with _patched_http():
        result = fetch_top_models(api_key="test-key", top_n=2, max_scan_cost_usd=1000.0)
    assert len(result) == 2


# ── Cost cap filter ──────────────────────────────────────────────────────────


def test_fetch_top_models_filters_by_cost_cap_default_10():
    """Default cost cap is $10 — the expensive big-model should be filtered out."""
    from garakboard.worker.openrouter_client import fetch_top_models
    with _patched_http():
        result = fetch_top_models(api_key="test-key", top_n=10)  # default max_scan_cost_usd=10
    ids = [m["id"] for m in result]
    assert "acme/big-model" not in ids


def test_fetch_top_models_cost_cap_keeps_cheap_models():
    """Default cost cap lets through cheap and free models."""
    from garakboard.worker.openrouter_client import fetch_top_models
    with _patched_http():
        result = fetch_top_models(api_key="test-key", top_n=10)
    ids = [m["id"] for m in result]
    assert "acme/cheap-model" in ids
    assert "acme/free-model:free" in ids
    assert "acme/budget-model" in ids


def test_fetch_top_models_explicit_zero_cap_excludes_paid():
    """Setting cap to 0.0 means only free models return."""
    from garakboard.worker.openrouter_client import fetch_top_models
    with _patched_http():
        result = fetch_top_models(api_key="test-key", top_n=10, max_scan_cost_usd=0.0)
    ids = [m["id"] for m in result]
    assert ids == ["acme/free-model:free"]


def test_fetch_top_models_high_cap_keeps_everything():
    from garakboard.worker.openrouter_client import fetch_top_models
    with _patched_http():
        result = fetch_top_models(api_key="test-key", top_n=10, max_scan_cost_usd=10_000.0)
    assert len(result) == 4


def test_fetch_top_models_none_cap_disables_filter():
    """Passing None disables the cost filter (keeps BYOK pricing=-1 models too)."""
    from garakboard.worker.openrouter_client import fetch_top_models
    with _patched_http():
        result = fetch_top_models(api_key="test-key", top_n=10, max_scan_cost_usd=None)
    assert len(result) == 4


# ── estimate_scan_cost_usd ───────────────────────────────────────────────────


def test_estimate_scan_cost_zero_for_free_model():
    from garakboard.worker.openrouter_client import estimate_scan_cost_usd
    assert estimate_scan_cost_usd({"prompt": "0", "completion": "0"}) == 0.0


def test_estimate_scan_cost_scales_with_price():
    from garakboard.worker.openrouter_client import estimate_scan_cost_usd
    cheap = estimate_scan_cost_usd({"prompt": "0.0000001", "completion": "0.0000001"})
    expensive = estimate_scan_cost_usd({"prompt": "0.00001", "completion": "0.00001"})
    assert expensive > cheap > 0.0


def test_estimate_scan_cost_byok_returns_infinity():
    """BYOK models have pricing = -1 → treat as infeasible (infinite cost)."""
    from garakboard.worker.openrouter_client import estimate_scan_cost_usd
    import math
    assert math.isinf(estimate_scan_cost_usd({"prompt": "-1", "completion": "-1"}))


# ── discover_and_schedule_scans ──────────────────────────────────────────────


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
        patch("garakboard.worker.tasks.publish_run_job"),
    ):
        db = MagicMock()
        mock_session_cls.return_value = db
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


def test_discover_publishes_with_openrouter_prefix_and_probe_categories():
    """publish_run_job must be called with `openrouter/<id>` (so LiteLLM routes to
    OpenRouter, not the underlying provider) and the default probe categories
    (not an empty list which silently reduces to encoding-only)."""
    from garakboard.worker.tasks import discover_and_schedule_scans
    from garakboard.worker.garak_runner import DEFAULT_PROBE_CATEGORIES

    top = [{"id": "tencent/hy3-preview:free", "pricing": {"prompt": "0", "completion": "0"}}]

    with (
        patch("garakboard.worker.tasks.fetch_top_models", return_value=top),
        patch("garakboard.worker.tasks.SessionLocal") as mock_session_cls,
        patch("garakboard.worker.tasks.publish_run_job") as mock_publish,
    ):
        db = MagicMock()
        mock_session_cls.return_value = db
        # Freshness query: no prior complete run — always stale
        def query_side_effect(cls):
            q = MagicMock()
            q.filter.return_value = q
            q.order_by.return_value = q
            q.first.return_value = None
            return q
        db.query.side_effect = query_side_effect

        discover_and_schedule_scans()

    assert mock_publish.called
    _, args, _ = mock_publish.mock_calls[0]
    # publish_run_job(run_id, model_name, probe_categories)
    model_name_passed = args[1]
    probe_categories_passed = args[2]
    assert model_name_passed == "openrouter/tencent/hy3-preview:free"
    assert probe_categories_passed == DEFAULT_PROBE_CATEGORIES


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
        discover_and_schedule_scans()
        mock_publish.assert_not_called()


def test_discover_passes_max_scan_cost_from_settings():
    """Scheduler should pass the configured cost cap through to fetch_top_models."""
    from garakboard.worker.tasks import discover_and_schedule_scans
    from garakboard.config import settings

    with (
        patch("garakboard.worker.tasks.fetch_top_models", return_value=[]) as mock_fetch,
        patch("garakboard.worker.tasks.SessionLocal") as mock_session_cls,
    ):
        db = MagicMock()
        mock_session_cls.return_value = db
        discover_and_schedule_scans()

    _, kwargs = mock_fetch.call_args
    assert kwargs.get("max_scan_cost_usd") == settings.scheduler_max_scan_cost_usd


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
