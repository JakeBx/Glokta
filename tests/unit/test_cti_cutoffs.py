"""Tests for per-model cutoff application (infrastructure/cti/cutoffs.py)."""

from datetime import date

from glokta.infrastructure.cti.cutoffs import apply_cutoffs, cutoff_for
from glokta.infrastructure.db.orm import Model


class TestCutoffFor:
    def test_matches_known_substring(self):
        result = cutoff_for("openrouter/anthropic/claude-opus-4-8")
        assert result is not None
        start, end = result
        assert start <= end

    def test_unknown_model_returns_none(self):
        assert cutoff_for("openrouter/acme/unknown-model-xyz") is None


class TestApplyCutoffs:
    def test_sets_cutoff_on_known_models_only(self, db_session):
        known = Model(
            name="openrouter/anthropic/claude-opus-4-8",
            provider="anthropic",
            snapshot_date=date(2024, 1, 1),
        )
        unknown = Model(
            name="openrouter/acme/mystery-1",
            provider="acme",
            snapshot_date=date(2024, 1, 1),
        )
        db_session.add_all([known, unknown])
        db_session.flush()

        updated = apply_cutoffs(db_session)
        db_session.refresh(known)
        db_session.refresh(unknown)

        assert updated == 1
        assert known.cutoff_end is not None
        assert unknown.cutoff_start is None

    def test_is_idempotent(self, db_session):
        m = Model(
            name="openrouter/anthropic/claude-opus-4-8",
            provider="anthropic",
            snapshot_date=date(2024, 1, 1),
        )
        db_session.add(m)
        db_session.flush()
        apply_cutoffs(db_session)
        second = apply_cutoffs(db_session)  # nothing changes the second time
        assert second == 0
