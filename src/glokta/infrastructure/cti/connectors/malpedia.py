"""malpedia connector — Malpedia actor library -> actor alias enrichment.

Malpedia (Fraunhofer FKIE) curates a threat-actor library with synonyms that complements the
MISP galaxy. Actors are normalised to the same ``ThreatActor`` shape, so they merge through
``upsert_threat_actors`` / ``build_taa_indices`` and the SYN masking alias index unchanged.

The public API exposes a slug list (``/api/get/actors``) and per-actor detail
(``/api/get/actor/<slug>``); ``fetch_malpedia_actors`` is the thin (bounded, best-effort) shim,
``normalise_malpedia_actors`` is the tested core. Reuses ``galaxy.ThreatActor``.
"""

import logging
from typing import Any

from glokta.infrastructure.cti.connectors.galaxy import ThreatActor

logger = logging.getLogger(__name__)

MALPEDIA_BASE = "https://malpedia.caad.fkie.fraunhofer.de"


def fetch_malpedia_actors(
    client: Any,
    *,
    limit: int | None = None,
    base_url: str = MALPEDIA_BASE,
) -> list[dict]:
    """Fetch up to ``limit`` actor detail dicts from the Malpedia API (best-effort per actor).

    The slug-list endpoint is fetched once; each actor detail is then fetched individually and
    failures are skipped (the API is slow/rate-limited, so callers should keep ``limit`` modest).
    Entries that are already detail dicts (e.g. a pre-fetched bulk export) pass straight through.
    """
    resp = client.get(f"{base_url}/api/get/actors")
    resp.raise_for_status()
    slugs = resp.json()
    if isinstance(slugs, dict):
        slugs = list(slugs)

    if limit is not None:
        slugs = slugs[:limit]

    details: list[dict] = []
    for slug in slugs:
        if not isinstance(slug, str):  # already a detail dict
            details.append(slug)
            continue
        try:
            detail = client.get(f"{base_url}/api/get/actor/{slug}")
            detail.raise_for_status()
            details.append(detail.json())
        except Exception as exc:  # noqa: BLE001 - best-effort enrichment
            logger.warning("malpedia: actor %s failed: %s", slug, exc)
    return details


def normalise_malpedia_actors(details: list[dict]) -> list[ThreatActor]:
    """Parse Malpedia actor detail dicts into ThreatActor rows (canonical name + aliases).

    Tolerant of the API's key variants: name from ``common_name``/``value``/``name``; synonyms
    from ``meta.synonyms`` or a top-level ``synonyms``/``aliases``. Entries without a name are
    skipped; aliases are de-duplicated preserving order.
    """
    actors: list[ThreatActor] = []
    for detail in details or []:
        name = detail.get("common_name") or detail.get("value") or detail.get("name")
        if not name:
            continue
        meta = detail.get("meta") or {}
        raw = meta.get("synonyms") or detail.get("synonyms") or detail.get("aliases") or []
        aliases = list(dict.fromkeys(a for a in raw if a))
        actors.append(ThreatActor(canonical_name=name, aliases=aliases, related_groups=[]))
    return actors
