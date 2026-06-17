"""Upsert + index helpers for the CTI reference tables (ATT&CK techniques, actors).

These back ATE label normalisation (revoked->current) and TAA synonym-aware scoring.
"""

import logging

from sqlalchemy.orm import Session

from glokta.infrastructure.cti.connectors.attack import AttackTechnique
from glokta.infrastructure.cti.connectors.galaxy import ThreatActor
from glokta.infrastructure.db.orm import CtiAttackTechnique, CtiThreatActor

logger = logging.getLogger(__name__)


def upsert_attack_techniques(db: Session, techniques: list[AttackTechnique]) -> int:
    """Insert/update ATT&CK technique reference rows by technique_id. Returns row count."""
    existing = {t.technique_id: t for t in db.query(CtiAttackTechnique).all()}
    for tech in techniques:
        row = existing.get(tech.technique_id)
        if row is None:
            db.add(
                CtiAttackTechnique(
                    technique_id=tech.technique_id,
                    name=tech.name,
                    tactic=tech.tactic,
                    revoked_by=tech.revoked_by,
                )
            )
        else:
            row.name = tech.name
            row.tactic = tech.tactic
            row.revoked_by = tech.revoked_by
    db.commit()
    return len(techniques)


def upsert_threat_actors(db: Session, actors: list[ThreatActor]) -> int:
    """Insert/update threat-actor reference rows by canonical_name. Returns row count."""
    existing = {a.canonical_name: a for a in db.query(CtiThreatActor).all()}
    for actor in actors:
        row = existing.get(actor.canonical_name)
        if row is None:
            db.add(
                CtiThreatActor(
                    canonical_name=actor.canonical_name,
                    aliases=actor.aliases,
                    related_groups=actor.related_groups,
                )
            )
        else:
            row.aliases = actor.aliases
            row.related_groups = actor.related_groups
    db.commit()
    return len(actors)


def build_taa_indices(db: Session) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Build (alias_index, related_index) from the threat-actor reference table.

    alias_index maps lower-cased alias/canonical -> canonical; related_index maps canonical
    -> set of related canonicals. Consumed by domain.cti.scoring.score_taa.
    """
    alias_index: dict[str, str] = {}
    related_index: dict[str, set[str]] = {}
    for actor in db.query(CtiThreatActor).all():
        canonical = actor.canonical_name
        alias_index[canonical.lower()] = canonical
        for alias in actor.aliases or []:
            alias_index[alias.lower()] = canonical
        related_index[canonical] = set(actor.related_groups or [])
    return alias_index, related_index


def build_technique_index(db: Session) -> dict[str, str]:
    """Map each technique id to its current id (resolving revoked->revoked_by)."""
    index: dict[str, str] = {}
    for tech in db.query(CtiAttackTechnique).all():
        index[tech.technique_id] = tech.revoked_by or tech.technique_id
    return index
