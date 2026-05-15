"""Unit tests for Pydantic schemas — red phase (tests before implementation)."""

from datetime import date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from glokta.models import Model, Run, ProbeResult


# --- ModelCreate / ModelResponse ---


def test_model_create_requires_name():
    """ModelCreate raises ValidationError when name is missing."""
    from glokta.schemas import ModelCreate

    with pytest.raises(ValidationError) as exc_info:
        ModelCreate(provider="test", snapshot_date=date.today())
    assert "name" in str(exc_info.value)


def test_model_create_requires_provider():
    """ModelCreate raises ValidationError when provider is missing."""
    from glokta.schemas import ModelCreate

    with pytest.raises(ValidationError) as exc_info:
        ModelCreate(name="test-model", snapshot_date=date.today())
    assert "provider" in str(exc_info.value)


def test_model_create_requires_snapshot_date():
    """ModelCreate raises ValidationError when snapshot_date is missing."""
    from glokta.schemas import ModelCreate

    with pytest.raises(ValidationError) as exc_info:
        ModelCreate(name="test-model", provider="test")
    assert "snapshot_date" in str(exc_info.value)


def test_model_create_version_is_optional():
    """ModelCreate succeeds when version is not provided."""
    from glokta.schemas import ModelCreate

    model = ModelCreate(name="test-model", provider="test", snapshot_date=date.today())
    assert model.version is None


def test_model_create_is_active_defaults_to_true():
    """ModelCreate.is_active defaults to True."""
    from glokta.schemas import ModelCreate

    model = ModelCreate(name="test-model", provider="test", snapshot_date=date.today())
    assert model.is_active is True


def test_model_response_from_orm(db_session):
    """ModelResponse.model_validate() succeeds on a persisted Model ORM instance."""
    from glokta.schemas import ModelResponse

    orm_obj = Model(
        name="orm-test-model",
        provider="orm-provider",
        snapshot_date=date.today(),
        is_active=True,
    )
    db_session.add(orm_obj)
    db_session.flush()

    response = ModelResponse.model_validate(orm_obj)
    assert response.name == "orm-test-model"
    assert response.provider == "orm-provider"
    assert response.is_active is True


def test_model_response_id_is_uuid(db_session):
    """ModelResponse.id is a UUID type."""
    from glokta.schemas import ModelResponse

    orm_obj = Model(
        name="uuid-test-model",
        provider="uuid-provider",
        snapshot_date=date.today(),
    )
    db_session.add(orm_obj)
    db_session.flush()

    response = ModelResponse.model_validate(orm_obj)
    assert isinstance(response.id, uuid4().__class__)


# --- RunCreate / RunResponse ---


def test_run_create_requires_model_id():
    """RunCreate raises ValidationError when model_id is missing."""
    from glokta.schemas import RunCreate

    with pytest.raises(ValidationError) as exc_info:
        RunCreate(probe_categories=["test"])
    assert "model_id" in str(exc_info.value)


def test_run_create_probe_categories_defaults_to_empty_list():
    """RunCreate.probe_categories defaults to []."""
    from glokta.schemas import RunCreate

    run = RunCreate(model_id=uuid4())
    assert run.probe_categories == []


def test_run_create_model_id_must_be_uuid():
    """RunCreate raises ValidationError when model_id is not a valid UUID."""
    from glokta.schemas import RunCreate

    with pytest.raises(ValidationError):
        RunCreate(model_id="not-a-uuid")


def test_run_response_from_orm(db_session):
    """RunResponse.model_validate() succeeds on a persisted Run ORM instance."""
    from glokta.schemas import RunResponse

    # First create a Model so Run has a valid foreign key
    model = Model(
        name="run-test-model",
        provider="run-provider",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    orm_obj = Run(model_id=model.id, triggered_by="test", status="pending")
    db_session.add(orm_obj)
    db_session.flush()

    response = RunResponse.model_validate(orm_obj)
    assert response.model_id == model.id
    assert response.triggered_by == "test"
    assert response.status == "pending"


def test_run_response_status_is_literal():
    """RunResponse rejects invalid status values at validation time."""
    from glokta.schemas import RunResponse

    # This test validates that status must be one of the Literal values
    # We test by creating a response dict directly
    with pytest.raises(ValidationError):
        RunResponse(
            id=uuid4(),
            model_id=uuid4(),
            triggered_by="test",
            status="invalid_status",
            created_at=datetime.utcnow(),
        )


# --- ProbeResultResponse ---


def test_probe_result_response_from_orm(db_session):
    """ProbeResultResponse.model_validate() succeeds on a persisted ProbeResult."""
    from glokta.schemas import ProbeResultResponse

    # First create Model and Run
    model = Model(
        name="probe-result-model",
        provider="probe-provider",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    run = Run(model_id=model.id, triggered_by="test", status="complete")
    db_session.add(run)
    db_session.flush()

    orm_obj = ProbeResult(
        run_id=run.id,
        probe_name="test_probe",
        probe_category="test_category",
        detector="test_detector",
        pass_count=5,
        fail_count=3,
        score=0.625,
    )
    db_session.add(orm_obj)
    db_session.flush()

    response = ProbeResultResponse.model_validate(orm_obj)
    assert response.probe_name == "test_probe"
    assert response.pass_count == 5
    assert response.fail_count == 3


def test_probe_result_response_score_nullable():
    """ProbeResultResponse.score can be None."""
    from glokta.schemas import ProbeResultResponse

    # Create response with None score
    response = ProbeResultResponse(
        id=1,
        run_id=uuid4(),
        probe_name="test",
        probe_category="cat",
        detector="det",
        pass_count=0,
        fail_count=0,
        score=None,
        created_at=datetime.utcnow(),
    )
    assert response.score is None


# --- LeaderboardRow ---


def test_leaderboard_row_pass_rate_computed():
    """LeaderboardRow.pass_rate = total_pass / (total_pass + total_fail)."""
    from glokta.schemas import LeaderboardRow

    row = LeaderboardRow(
        model_id=uuid4(),
        model_name="Test Model",
        provider="Test Provider",
        probe_category="test",
        total_pass=7,
        total_fail=3,
        score=0.7,
    )
    assert row.pass_rate == 0.7


def test_leaderboard_row_pass_rate_zero_when_no_attempts():
    """LeaderboardRow.pass_rate is 0.0 when total_pass and total_fail are both 0."""
    from glokta.schemas import LeaderboardRow

    row = LeaderboardRow(
        model_id=uuid4(),
        model_name="Test Model",
        provider="Test Provider",
        probe_category="test",
        total_pass=0,
        total_fail=0,
        score=0.0,
    )
    assert row.pass_rate == 0.0


def test_leaderboard_row_score_is_float():
    """LeaderboardRow.score is a float."""
    from glokta.schemas import LeaderboardRow

    row = LeaderboardRow(
        model_id=uuid4(),
        model_name="Test Model",
        provider="Test Provider",
        probe_category="test",
        total_pass=5,
        total_fail=5,
        score=0.5,
    )
    assert isinstance(row.score, float)


# --- LeaderboardResponse ---


def test_leaderboard_response_structure():
    """LeaderboardResponse can be instantiated with rows, total, page, page_size, total_pages."""
    from glokta.schemas import LeaderboardResponse, LeaderboardRow

    response = LeaderboardResponse(
        rows=[],
        total=0,
        page=1,
        page_size=10,
        total_pages=0,
    )
    assert response.page == 1
    assert response.page_size == 10


def test_leaderboard_response_rows_is_list():
    """LeaderboardResponse.rows is a list of LeaderboardRow."""
    from glokta.schemas import LeaderboardResponse, LeaderboardRow

    row = LeaderboardRow(
        model_id=uuid4(),
        model_name="Test Model",
        provider="Test Provider",
        probe_category="test",
        total_pass=10,
        total_fail=5,
        score=0.667,
    )
    response = LeaderboardResponse(
        rows=[row],
        total=1,
        page=1,
        page_size=10,
        total_pages=1,
    )
    assert len(response.rows) == 1
    assert isinstance(response.rows[0], LeaderboardRow)


# --- ProbeResultDetail ---


def test_probe_result_detail_pass_rate_computed():
    """ProbeResultDetail.pass_rate is correctly calculated."""
    from glokta.schemas import ProbeResultDetail

    detail = ProbeResultDetail(
        probe_name="test_probe",
        probe_category="test_category",
        detector="test_detector",
        pass_count=8,
        fail_count=2,
        score=0.8,
    )
    assert detail.pass_rate == 0.8


def test_probe_result_detail_score_nullable():
    """ProbeResultDetail.score can be None."""
    from glokta.schemas import ProbeResultDetail

    detail = ProbeResultDetail(
        probe_name="test_probe",
        probe_category="test_category",
        detector="test_detector",
        pass_count=0,
        fail_count=10,
        score=None,
    )
    assert detail.score is None


# --- ModelDetailResponse ---


def test_model_detail_response_structure():
    """ModelDetailResponse can be instantiated with model_id, model_name, provider, probe_results."""
    from glokta.schemas import ModelDetailResponse, ProbeResultDetail

    response = ModelDetailResponse(
        model_id=uuid4(),
        model_name="Test Model",
        provider="Test Provider",
        probe_results=[],
    )
    assert response.model_name == "Test Model"
    assert response.provider == "Test Provider"


def test_model_detail_response_summary_is_optional():
    """ModelDetailResponse.summary can be None."""
    from glokta.schemas import ModelDetailResponse

    response = ModelDetailResponse(
        model_id=uuid4(),
        model_name="Test Model",
        provider="Test Provider",
        probe_results=[],
        summary=None,
    )
    assert response.summary is None


# --- Schema imports ---


def test_all_schemas_importable():
    """All schemas can be imported from glokta.schemas."""
    from glokta.schemas import (
        ModelBase,
        ModelCreate,
        ModelResponse,
        RunCreate,
        RunResponse,
        RunStatus,
        ProbeResultResponse,
        LeaderboardRow,
        LeaderboardResponse,
        ProbeResultDetail,
        ModelDetailResponse,
    )

    # Verify they exist and are not None
    assert ModelBase is not None
    assert ModelCreate is not None
    assert ModelResponse is not None
    assert RunCreate is not None
    assert RunResponse is not None
    assert RunStatus is not None
    assert ProbeResultResponse is not None
    assert LeaderboardRow is not None
    assert LeaderboardResponse is not None
    assert ProbeResultDetail is not None
    assert ModelDetailResponse is not None


def test_leaderboard_row_pass_rate_in_dump():
    """LeaderboardRow.pass_rate appears in .model_dump() output (computed_field)."""
    from glokta.schemas import LeaderboardRow

    row = LeaderboardRow(
        model_id=uuid4(),
        model_name="Test Model",
        provider="Test Provider",
        probe_category="test",
        total_pass=3,
        total_fail=7,
        score=0.3,
    )
    dumped = row.model_dump()
    assert "pass_rate" in dumped
    assert dumped["pass_rate"] == 0.3


def test_probe_result_detail_pass_rate_in_dump():
    """ProbeResultDetail.pass_rate appears in .model_dump() output (computed_field)."""
    from glokta.schemas import ProbeResultDetail

    detail = ProbeResultDetail(
        probe_name="test_probe",
        probe_category="test_category",
        detector="test_detector",
        pass_count=4,
        fail_count=6,
        score=0.4,
    )
    dumped = detail.model_dump()
    assert "pass_rate" in dumped
    assert dumped["pass_rate"] == 0.4
