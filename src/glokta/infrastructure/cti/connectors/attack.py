"""attack connector — MITRE ATT&CK STIX 2.1 bundle -> technique reference rows.

Parses ``attack-pattern`` objects into AttackTechnique rows (technique id, name, tactic,
revoked_by). Backs ATE label validation and revoked->current normalisation. The git pull +
JSON load is the thin shim; ``normalise_attack_bundle`` is the tested core.
"""

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_MITRE_SOURCE = "mitre-attack"
ENTERPRISE_ATTACK_URL = (
    "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/"
    "enterprise-attack/enterprise-attack.json"
)


def fetch_attack_bundle(client: Any, url: str = ENTERPRISE_ATTACK_URL) -> dict:
    """Fetch the enterprise ATT&CK STIX bundle JSON via an httpx-style client."""
    resp = client.get(url)
    resp.raise_for_status()
    return resp.json()


@dataclass
class AttackTechnique:
    technique_id: str
    name: str
    tactic: str | None
    revoked_by: str | None


def _external_id(obj: dict) -> str | None:
    for ref in obj.get("external_references") or []:
        if ref.get("source_name") == _MITRE_SOURCE and ref.get("external_id"):
            return ref["external_id"]
    return None


def _tactic(obj: dict) -> str | None:
    phases = obj.get("kill_chain_phases") or []
    for phase in phases:
        if phase.get("kill_chain_name") == _MITRE_SOURCE:
            return phase.get("phase_name")
    return phases[0].get("phase_name") if phases else None


def normalise_attack_bundle(bundle: dict) -> list[AttackTechnique]:
    """Parse a STIX bundle into AttackTechnique rows, resolving revoked-by links."""
    objects = bundle.get("objects") or []

    # Pass 1: map STIX id -> external technique id for attack-patterns.
    stix_to_extid: dict[str, str] = {}
    for obj in objects:
        if obj.get("type") == "attack-pattern":
            ext = _external_id(obj)
            if ext:
                stix_to_extid[obj["id"]] = ext

    # Pass 2: revoked-by relationships (source revoked, target is the replacement).
    revoked_by: dict[str, str] = {}
    for obj in objects:
        if obj.get("type") == "relationship" and obj.get("relationship_type") == "revoked-by":
            src = stix_to_extid.get(obj.get("source_ref", ""))
            dst = stix_to_extid.get(obj.get("target_ref", ""))
            if src and dst:
                revoked_by[src] = dst

    techniques: list[AttackTechnique] = []
    for obj in objects:
        if obj.get("type") != "attack-pattern":
            continue
        ext = _external_id(obj)
        if not ext:
            continue
        techniques.append(
            AttackTechnique(
                technique_id=ext,
                name=obj.get("name", ""),
                tactic=_tactic(obj),
                revoked_by=revoked_by.get(ext),
            )
        )
    return techniques
