"""Models API router."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from garakboard.api.deps import get_db
from garakboard.models import Model
from garakboard.schemas import ModelResponse

router = APIRouter()


@router.get("/models", response_model=list[ModelResponse])
def list_models(db: Session = Depends(get_db)) -> list[Model]:
    """List all active models, ordered by name."""
    models = db.query(Model).order_by(Model.name).all()
    return models


@router.get("/models/{model_id}", response_model=ModelResponse)
def get_model(model_id: UUID, db: Session = Depends(get_db)) -> Model:
    """Get a single model by UUID; 404 if not found."""
    model = db.query(Model).filter(Model.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    return model