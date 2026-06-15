"""galaxy connector — MISP threat-actor galaxy -> actor reference rows.

Parses the ``threat-actor`` galaxy cluster into ThreatActor rows (canonical name, aliases,
related groups) that back the synonym-aware TAA C/P/I scoring. ``normalise_galaxy`` is the
tested core; the git pull + JSON load is the thin shim.
"""

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

THREAT_ACTOR_GALAXY_URL = (
    "https://raw.githubusercontent.com/MISP/misp-galaxy/main/clusters/threat-actor.json"
)


def fetch_galaxy_cluster(client: Any, url: str = THREAT_ACTOR_GALAXY_URL) -> dict:
    """Fetch the MISP threat-actor galaxy cluster JSON via an httpx-style client."""
    resp = client.get(url)
    resp.raise_for_status()
    return resp.json()


@dataclass
class ThreatActor:
    canonical_name: str
    aliases: list[str]
    related_groups: list[str]


def normalise_galaxy(cluster: dict) -> list[ThreatActor]:
    """Parse a MISP galaxy cluster, resolving related dest-uuid links to actor names."""
    values = cluster.get("values") or []

    uuid_to_name: dict[str, str] = {}
    for entry in values:
        uuid = entry.get("uuid")
        name = entry.get("value")
        if uuid and name:
            uuid_to_name[uuid] = name

    actors: list[ThreatActor] = []
    for entry in values:
        name = entry.get("value")
        if not name:
            continue
        meta = entry.get("meta") or {}
        aliases = [a for a in (meta.get("synonyms") or []) if a]
        related = []
        for rel in entry.get("related") or []:
            dest = rel.get("dest-uuid")
            resolved = uuid_to_name.get(dest)
            if resolved:
                related.append(resolved)
        actors.append(
            ThreatActor(canonical_name=name, aliases=aliases, related_groups=related)
        )
    return actors
