"""Cross-source advisory dedup.

National CERTs (CISA, CCCS, NCSC, ACSC) co-seal joint advisories, so the same advisory arrives
under different ids/slugs from each source. Ingesting all of them would double-count items and
defeat contamination control (the model may have trained on the CISA copy). This keeps one copy
per advisory, preferring the highest-priority source, using a content fingerprint that survives
the small HTML differences between re-publications.

Pure / in-memory: runs over a freshly-collected batch before ingest; no DB or schema change.
"""

import re
from dataclasses import dataclass

DEFAULT_PRIORITY = ("cisa", "cccs", "ncsc", "dfir", "acsc")

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass
class DedupeResult:
    """``kept`` = the surviving advisories; ``dropped`` = (advisory, kept_id) pairs for transparency."""

    kept: list[dict]
    dropped: list[tuple[dict, str]]


def advisory_signature(advisory: dict) -> tuple[frozenset[str], frozenset[str]]:
    """(CVE set, ATT&CK-technique set), upper-cased — the stable cross-source content fingerprint."""
    cves = frozenset((c or "").upper() for c in (advisory.get("cves") or []) if c)
    techs = frozenset((t or "").upper() for t in (advisory.get("techniques") or []) if t)
    return cves, techs


def _title_tokens(advisory: dict) -> frozenset[str]:
    text = (advisory.get("title") or advisory.get("id") or "").lower()
    return frozenset(_TOKEN_RE.findall(text))


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_duplicate(
    a: dict,
    b: dict,
    *,
    cve_threshold: float = 0.6,
    title_threshold: float = 0.7,
) -> bool:
    """Two advisories are the same report if they share most CVEs, or (absent CVEs) most title tokens.

    CVE overlap is the strong signal — joint advisories cite the same vulnerabilities even when one
    source adds an extra one. The title fallback catches CVE-less reports (e.g. ransomware TTP
    advisories) that two agencies title near-identically.
    """
    cves_a, _ = advisory_signature(a)
    cves_b, _ = advisory_signature(b)
    if cves_a and cves_b and _jaccard(cves_a, cves_b) >= cve_threshold:
        return True
    return _jaccard(_title_tokens(a), _title_tokens(b)) >= title_threshold


def dedupe_advisories(
    advisories: list[dict],
    *,
    priority: tuple[str, ...] = DEFAULT_PRIORITY,
    cve_threshold: float = 0.6,
    title_threshold: float = 0.7,
) -> DedupeResult:
    """Collapse duplicate advisories **across sources**, keeping the highest-priority copy.

    Advisories are considered in priority order (``source_site`` ranked by ``priority``), so a
    later, lower-priority duplicate is the one dropped. Order within a source is preserved.

    Only **cross-source** duplicates are collapsed: two items from the *same* source that merely
    share CVEs are kept (they are distinct advisories with distinct ids — within-source dedup is
    the ingest layer's job, keyed on ``external_id``).
    """
    rank = {s: i for i, s in enumerate(priority)}
    ordered = sorted(
        enumerate(advisories),
        key=lambda pair: (rank.get(pair[1].get("source_site", ""), len(priority)), pair[0]),
    )

    kept: list[dict] = []
    dropped: list[tuple[dict, str]] = []
    for _, adv in ordered:
        match = next(
            (
                k
                for k in kept
                if k.get("source_site") != adv.get("source_site")
                and is_duplicate(adv, k, cve_threshold=cve_threshold, title_threshold=title_threshold)
            ),
            None,
        )
        if match is None:
            kept.append(adv)
        else:
            dropped.append((adv, match.get("id", "?")))
    return DedupeResult(kept=kept, dropped=dropped)
