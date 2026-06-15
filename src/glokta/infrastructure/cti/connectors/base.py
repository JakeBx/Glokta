"""Shared connector types — the normalised item contract consumed by ingest_service."""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class NormalisedCtiItem:
    """A source-agnostic benchmark item produced by a connector's normalise step.

    ``first_available_date`` is intentionally NOT set here — ingest_service derives it as
    ``max(input_date, label_date)`` so the temporal-anchoring invariant lives in one place.
    """

    task: str
    external_id: str
    source: str
    input_text: str
    label: dict
    input_ref: dict | None = None
    label_provenance: dict = field(default_factory=dict)
    source_revision: str | None = None
    input_date: date | None = None
    label_date: date | None = None
    authority_agreement: str = "single"
    difficulty: dict = field(default_factory=dict)
