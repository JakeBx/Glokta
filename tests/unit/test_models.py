"""Unit tests for SQLAlchemy models in glokta.models."""

import uuid
from datetime import date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from glokta.models import (
    Model,
    Run,
    ProbeResult,
    Attempt,
    ProbeRunQueue,
)


# =============================================================================
# Group 1: Model (models table)
# =============================================================================

def test_model_can_be_created(db_session):
    """A Model instance can be created and queried from the database."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    retrieved = db_session.query(Model).filter_by(name="claude-3-5-sonnet").first()
    assert retrieved is not None
    assert retrieved.provider == "Anthropic"
    assert retrieved.snapshot_date == date(2024, 1, 15)


def test_model_name_is_unique(db_session):
    """Two models with the same name raise an IntegrityError."""
    model1 = Model(
        name="gpt-4",
        provider="OpenAI",
        snapshot_date=date(2024, 2, 1),
    )
    db_session.add(model1)
    db_session.commit()

    model2 = Model(
        name="gpt-4",
        provider="OpenAI",
        snapshot_date=date(2024, 3, 1),
    )
    db_session.add(model2)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_model_is_active_defaults_to_true(db_session):
    """is_active defaults to True when not specified."""
    model = Model(
        name="gemini-pro",
        provider="Google",
        snapshot_date=date(2024, 1, 1),
    )
    db_session.add(model)
    db_session.commit()

    assert model.is_active is True


def test_model_has_runs_relationship(db_session):
    """Model.runs returns an empty list when no runs have been created."""
    model = Model(
        name="llama-3",
        provider="Meta",
        snapshot_date=date(2024, 1, 1),
    )
    db_session.add(model)
    db_session.commit()

    assert model.runs == []


# =============================================================================
# Group 2: Run (runs table)
# =============================================================================

def test_run_can_be_created(db_session):
    """A Run instance can be created linked to a Model."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run = Run(
        model_id=model.id,
        triggered_by="api",
    )
    db_session.add(run)
    db_session.commit()

    retrieved = db_session.query(Run).filter_by(model_id=model.id).first()
    assert retrieved is not None
    assert retrieved.triggered_by == "api"
    assert retrieved.status == "pending"


def test_run_status_defaults_to_pending(db_session):
    """Run.status defaults to 'pending'."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run = Run(model_id=model.id)
    db_session.add(run)
    db_session.commit()

    assert run.status == "pending"


def test_run_has_probe_results_relationship(db_session):
    """Run.probe_results returns an empty list when no results exist."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run = Run(model_id=model.id)
    db_session.add(run)
    db_session.commit()

    assert run.probe_results == []


def test_run_has_attempts_relationship(db_session):
    """Run.attempts returns an empty list when no attempts exist."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run = Run(model_id=model.id)
    db_session.add(run)
    db_session.commit()

    assert run.attempts == []


def test_run_model_backref(db_session):
    """Run.model back-references the parent Model."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run = Run(model_id=model.id)
    db_session.add(run)
    db_session.commit()

    assert run.model is not None
    assert run.model.id == model.id
    assert run.model.name == "claude-3-5-sonnet"


# =============================================================================
# Group 3: ProbeResult (probe_results table)
# =============================================================================

def test_probe_result_can_be_created(db_session):
    """A ProbeResult can be created linked to a Run."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run = Run(model_id=model.id)
    db_session.add(run)
    db_session.commit()

    probe_result = ProbeResult(
        run_id=run.id,
        probe_name="prompt_injection",
        probe_category="safety",
        detector="TestDetector",
        pass_count=10,
        fail_count=2,
    )
    db_session.add(probe_result)
    db_session.commit()

    retrieved = db_session.query(ProbeResult).filter_by(run_id=run.id).first()
    assert retrieved is not None
    assert retrieved.probe_name == "prompt_injection"
    assert retrieved.pass_count == 10
    assert retrieved.fail_count == 2


def test_probe_result_score_is_nullable(db_session):
    """ProbeResult.score can be None."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run = Run(model_id=model.id)
    db_session.add(run)
    db_session.commit()

    probe_result = ProbeResult(
        run_id=run.id,
        probe_name="prompt_injection",
        probe_category="safety",
        detector="TestDetector",
        pass_count=10,
        fail_count=2,
        score=None,
    )
    db_session.add(probe_result)
    db_session.commit()

    retrieved = db_session.query(ProbeResult).filter_by(run_id=run.id).first()
    assert retrieved.score is None


def test_probe_result_pass_fail_default_to_zero(db_session):
    """pass_count and fail_count default to 0."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run = Run(model_id=model.id)
    db_session.add(run)
    db_session.commit()

    probe_result = ProbeResult(
        run_id=run.id,
        probe_name="prompt_injection",
        probe_category="safety",
        detector="TestDetector",
    )
    db_session.add(probe_result)
    db_session.commit()

    assert probe_result.pass_count == 0
    assert probe_result.fail_count == 0


# =============================================================================
# Group 4: Attempt (attempts table)
# =============================================================================

def test_attempt_can_be_created(db_session):
    """An Attempt can be created linked to a Run."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run = Run(model_id=model.id)
    db_session.add(run)
    db_session.commit()

    attempt = Attempt(
        run_id=run.id,
        probe_name="prompt_injection",
        prompt="test prompt",
        response="test response",
    )
    db_session.add(attempt)
    db_session.commit()

    retrieved = db_session.query(Attempt).filter_by(run_id=run.id).first()
    assert retrieved is not None
    assert retrieved.probe_name == "prompt_injection"
    assert retrieved.prompt == "test prompt"
    assert retrieved.response == "test response"


def test_attempt_detector_outcome_stores_json(db_session):
    """detector_outcome can store and retrieve a dict."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run = Run(model_id=model.id)
    db_session.add(run)
    db_session.commit()

    outcome = {"detected": True, "confidence": 0.95, "labels": ["toxic", "unsafe"]}
    attempt = Attempt(
        run_id=run.id,
        probe_name="prompt_injection",
        detector_outcome=outcome,
    )
    db_session.add(attempt)
    db_session.commit()

    retrieved = db_session.query(Attempt).filter_by(run_id=run.id).first()
    assert retrieved.detector_outcome == outcome
    assert retrieved.detector_outcome["detected"] is True
    assert retrieved.detector_outcome["labels"] == ["toxic", "unsafe"]


def test_attempt_fields_are_nullable(db_session):
    """prompt and response are nullable."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run = Run(model_id=model.id)
    db_session.add(run)
    db_session.commit()

    attempt = Attempt(
        run_id=run.id,
        probe_name="prompt_injection",
        prompt=None,
        response=None,
    )
    db_session.add(attempt)
    db_session.commit()

    retrieved = db_session.query(Attempt).filter_by(run_id=run.id).first()
    assert retrieved.prompt is None
    assert retrieved.response is None


# =============================================================================
# Group 5: ProbeRunQueue (probe_run_queue table)
# =============================================================================

def test_probe_run_queue_can_be_created(db_session):
    """A ProbeRunQueue entry can be created."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    queue_entry = ProbeRunQueue(
        model_id=model.id,
        probe_category="safety",
        scheduled_date=date(2024, 3, 1),
    )
    db_session.add(queue_entry)
    db_session.commit()

    retrieved = db_session.query(ProbeRunQueue).filter_by(model_id=model.id).first()
    assert retrieved is not None
    assert retrieved.probe_category == "safety"
    assert retrieved.scheduled_date == date(2024, 3, 1)
    assert retrieved.status == "pending"


def test_probe_run_queue_status_defaults_to_pending(db_session):
    """ProbeRunQueue.status defaults to 'pending'."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    queue_entry = ProbeRunQueue(
        model_id=model.id,
        probe_category="safety",
        scheduled_date=date(2024, 3, 1),
    )
    db_session.add(queue_entry)
    db_session.commit()

    assert queue_entry.status == "pending"


# =============================================================================
# Additional tests for relationships and composite features
# =============================================================================

def test_model_runs_relationship_with_data(db_session):
    """Model.runs returns all related runs."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run1 = Run(model_id=model.id, triggered_by="api")
    run2 = Run(model_id=model.id, triggered_by="scheduled")
    db_session.add_all([run1, run2])
    db_session.commit()

    assert len(model.runs) == 2


def test_run_status_valid_values(db_session):
    """Run status can be set to valid values: pending, running, complete, failed."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    for status in ["pending", "running", "complete", "failed"]:
        run = Run(model_id=model.id, status=status)
        db_session.add(run)
        db_session.commit()
        assert run.status == status


def test_probe_result_run_relationship(db_session):
    """ProbeResult.run back-references the parent Run."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run = Run(model_id=model.id)
    db_session.add(run)
    db_session.commit()

    probe_result = ProbeResult(
        run_id=run.id,
        probe_name="prompt_injection",
        probe_category="safety",
        detector="TestDetector",
        pass_count=10,
        fail_count=2,
    )
    db_session.add(probe_result)
    db_session.commit()

    assert probe_result.run is not None
    assert probe_result.run.id == run.id


def test_attempt_run_relationship(db_session):
    """Attempt.run back-references the parent Run."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run = Run(model_id=model.id)
    db_session.add(run)
    db_session.commit()

    attempt = Attempt(
        run_id=run.id,
        probe_name="prompt_injection",
        prompt="test prompt",
    )
    db_session.add(attempt)
    db_session.commit()

    assert attempt.run is not None
    assert attempt.run.id == run.id


def test_model_probe_run_queue_relationship(db_session):
    """Model.probe_run_queue returns list of queue entries."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    queue_entry = ProbeRunQueue(
        model_id=model.id,
        probe_category="safety",
        scheduled_date=date(2024, 3, 1),
    )
    db_session.add(queue_entry)
    db_session.commit()

    assert len(model.probe_run_queue) == 1
    assert model.probe_run_queue[0].probe_category == "safety"


def test_probe_run_queue_model_backref(db_session):
    """ProbeRunQueue.model back-references the parent Model."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    queue_entry = ProbeRunQueue(
        model_id=model.id,
        probe_category="safety",
        scheduled_date=date(2024, 3, 1),
    )
    db_session.add(queue_entry)
    db_session.commit()

    assert queue_entry.model is not None
    assert queue_entry.model.id == model.id


def test_model_version_nullable(db_session):
    """Model.version is nullable."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
        version=None,
    )
    db_session.add(model)
    db_session.commit()

    assert model.version is None


def test_run_started_at_completed_at_nullable(db_session):
    """Run.started_at and completed_at are nullable."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run = Run(model_id=model.id)
    db_session.add(run)
    db_session.commit()

    assert run.started_at is None
    assert run.completed_at is None


def test_model_created_at_defaults(db_session):
    """Model.created_at is set automatically."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    assert model.created_at is not None
    assert isinstance(model.created_at, datetime)


def test_attempt_created_at_defaults(db_session):
    """Attempt.created_at is set automatically."""
    model = Model(
        name="claude-3-5-sonnet",
        provider="Anthropic",
        snapshot_date=date(2024, 1, 15),
    )
    db_session.add(model)
    db_session.commit()

    run = Run(model_id=model.id)
    db_session.add(run)
    db_session.commit()

    attempt = Attempt(
        run_id=run.id,
        probe_name="prompt_injection",
    )
    db_session.add(attempt)
    db_session.commit()

    assert attempt.created_at is not None
    assert isinstance(attempt.created_at, datetime)