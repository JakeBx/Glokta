"""cve connector — normalise CVE JSON 5.0 records (cvelistV5 + CISA-ADP) into items.

Produces RCM (CVE description -> CWE) and VSP (CVE description -> CVSS vector) items.
Label source-of-truth precedence is CNA -> CISA-ADP -> NVD; cross-source disagreement is
preserved as an ``authority_agreement`` difficulty stratum rather than discarded.

This module is the pure transform. The thin git/delta fetch and NVD API cross-check live
alongside but are kept out of the unit-tested ``normalise`` path.
"""

import json
import logging
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from glokta.infrastructure.cti.connectors.base import NormalisedCtiItem

logger = logging.getLogger(__name__)

SOURCE = "cve"
CVELIST_REPO = "https://github.com/CVEProject/cvelistV5.git"
CVELIST_DELTA_URL = (
    "https://raw.githubusercontent.com/CVEProject/cvelistV5/main/cves/delta.json"
)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def _english_description(cna: dict) -> str:
    descriptions = cna.get("descriptions") or []
    for d in descriptions:
        if str(d.get("lang", "")).lower().startswith("en"):
            return d.get("value") or ""
    return (descriptions[0].get("value") or "") if descriptions else ""


def _cwes_from_container(container: dict) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for pt in container.get("problemTypes") or []:
        for d in pt.get("descriptions") or []:
            cwe = d.get("cweId")
            if cwe and cwe.upper().startswith("CWE-") and cwe.upper() not in seen:
                seen.add(cwe.upper())
                out.append(cwe.upper())
    return out


def _cvss_from_container(container: dict) -> tuple[str | None, float | None]:
    for metric in container.get("metrics") or []:
        for key in ("cvssV3_1", "cvssV3_0"):
            cvss = metric.get(key)
            if cvss and cvss.get("vectorString"):
                return cvss["vectorString"], cvss.get("baseScore")
    return None, None


def _agreement(values_by_source: list[list[str]]) -> str:
    """single (one source), agree (all match) or disagree (sources differ)."""
    present = [set(v) for v in values_by_source if v]
    if len(present) <= 1:
        return "single"
    first = present[0]
    return "agree" if all(s == first for s in present[1:]) else "disagree"


def extract_nvd_labels(
    nvd_record: dict,
) -> tuple[list[str], str | None, float | None]:
    """Extract (CWE ids, CVSS v3.1 vector, base score) from an NVD API 2.0 CVE record."""
    cve = nvd_record.get("cve") or {}
    cwes: list[str] = []
    seen: set[str] = set()
    for weakness in cve.get("weaknesses") or []:
        for desc in weakness.get("description") or []:
            value = (desc.get("value") or "").upper()
            if value.startswith("CWE-") and value not in seen:
                seen.add(value)
                cwes.append(value)
    vector: str | None = None
    base: float | None = None
    for metric in (cve.get("metrics") or {}).get("cvssMetricV31") or []:
        data = metric.get("cvssData") or {}
        if data.get("vectorString"):
            vector = data["vectorString"]
            base = data.get("baseScore")
            break
    return cwes, vector, base


def normalise_cve_record(
    record: dict,
    source_revision: str | None = None,
    nvd_record: dict | None = None,
) -> list[NormalisedCtiItem]:
    """Normalise one CVE JSON 5.0 record into RCM and/or VSP benchmark items.

    ``nvd_record`` (optional NVD API 2.0 response) is folded in as a cross-check source
    for authority agreement, after CNA and ADP in the precedence chain.
    """
    nvd_cwes: list[str] = []
    nvd_vec: str | None = None
    nvd_base: float | None = None
    if nvd_record is not None:
        nvd_cwes, nvd_vec, nvd_base = extract_nvd_labels(nvd_record)

    meta = record.get("cveMetadata") or {}
    cve_id = meta.get("cveId")
    if not cve_id:
        return []

    containers = record.get("containers") or {}
    cna = containers.get("cna") or {}
    adp_list = containers.get("adp") or []

    description = _english_description(cna)
    if not description:
        return []

    published = _parse_date(meta.get("datePublished"))
    difficulty = {"description_length": len(description)}
    items: list[NormalisedCtiItem] = []

    # --- RCM (CVE -> CWE) ---------------------------------------------------
    cna_cwes = _cwes_from_container(cna)
    adp_cwes: list[str] = []
    adp_cwe_date: date | None = None
    for adp in adp_list:
        found = _cwes_from_container(adp)
        if found:
            adp_cwes = found
            adp_cwe_date = _parse_date(
                (adp.get("providerMetadata") or {}).get("dateUpdated")
            )
            break

    cwe_sources: list[tuple[str, list[str], date | None]] = []
    if cna_cwes:
        cwe_sources.append(("cna", cna_cwes, published))
    if adp_cwes:
        cwe_sources.append(("adp", adp_cwes, adp_cwe_date))
    if nvd_cwes:
        cwe_sources.append(("nvd", nvd_cwes, published))

    if cwe_sources:
        _, primary_cwes, primary_date = cwe_sources[0]
        items.append(
            NormalisedCtiItem(
                task="rcm",
                external_id=cve_id,
                source=SOURCE,
                input_text=description,
                label={"cwe": primary_cwes},
                label_provenance={name: vals for name, vals, _ in cwe_sources},
                source_revision=source_revision,
                input_date=published,
                label_date=primary_date,
                authority_agreement=_agreement([v for _, v, _ in cwe_sources]),
                difficulty=difficulty,
            )
        )

    # --- VSP (CVE -> CVSS vector) ------------------------------------------
    cna_vec, cna_base = _cvss_from_container(cna)
    adp_vec: str | None = None
    adp_base: float | None = None
    adp_vec_date: date | None = None
    for adp in adp_list:
        vec, base = _cvss_from_container(adp)
        if vec:
            adp_vec, adp_base = vec, base
            adp_vec_date = _parse_date(
                (adp.get("providerMetadata") or {}).get("dateUpdated")
            )
            break

    vec_sources: list[tuple[str, str, float | None, date | None]] = []
    if cna_vec:
        vec_sources.append(("cna", cna_vec, cna_base, published))
    if adp_vec:
        vec_sources.append(("adp", adp_vec, adp_base, adp_vec_date))
    if nvd_vec:
        vec_sources.append(("nvd", nvd_vec, nvd_base, published))

    if vec_sources:
        _, primary_vec, primary_base, primary_vec_date = vec_sources[0]
        items.append(
            NormalisedCtiItem(
                task="vsp",
                external_id=cve_id,
                source=SOURCE,
                input_text=description,
                label={"vector": primary_vec, "base_score": primary_base},
                label_provenance={name: vec for name, vec, _, _ in vec_sources},
                source_revision=source_revision,
                input_date=published,
                label_date=primary_vec_date,
                authority_agreement=_agreement([[vec] for _, vec, _, _ in vec_sources]),
                difficulty=difficulty,
            )
        )

    return items


# --- fetch side (thin I/O shims; the normalise above is the tested core) ----


def iter_cve_records(root: str | Path) -> Iterator[dict]:
    """Yield parsed CVE JSON 5.0 records from every ``*.json`` under ``root``.

    Invalid JSON files are logged and skipped. Used to walk a freshly-pulled cvelistV5
    checkout (or just its changed-files subset for the delta).
    """
    for path in sorted(Path(root).rglob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                record = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("iter_cve_records: skipping %s (%s)", path, exc)
            continue
        if isinstance(record, dict) and record.get("cveMetadata"):
            yield record


def recent_delta_links(
    delta: dict,
    lookback_days: int,
    now: date | None = None,
) -> list[tuple[str, str]]:
    """Return (cve_id, githubLink) for delta entries updated within the lookback window.

    Combines the ``new`` and ``updated`` arrays of cvelistV5's delta.json.
    """
    today = now or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=lookback_days)
    links: list[tuple[str, str]] = []
    for bucket in ("new", "updated"):
        for entry in delta.get(bucket) or []:
            link = entry.get("githubLink")
            cve_id = entry.get("cveId")
            if not link or not cve_id:
                continue
            updated = _parse_date(entry.get("dateUpdated"))
            if updated is not None and updated < cutoff:
                continue
            links.append((cve_id, link))
    return links


def fetch_recent_cve_records(
    client: Any,
    lookback_days: int,
    now: date | None = None,
    headers: dict | None = None,
    max_records: int | None = None,
) -> list[dict]:
    """Fetch CVE JSON 5.0 records changed within the lookback window via delta.json.

    Avoids cloning the full repo: read delta.json, then GET only the recent records.
    ``max_records`` caps how many are fetched (keeps smoke/dev runs small).
    """
    resp = client.get(CVELIST_DELTA_URL, headers=headers)
    resp.raise_for_status()
    links = recent_delta_links(resp.json(), lookback_days, now=now)
    if max_records is not None:
        links = links[:max_records]

    records: list[dict] = []
    for _cve_id, link in links:
        try:
            record_resp = client.get(link, headers=headers)
            record_resp.raise_for_status()
            record = record_resp.json()
        except Exception as exc:  # network/JSON hiccup on a single record — skip it
            logger.warning("fetch_recent_cve_records: skipping %s (%s)", link, exc)
            continue
        if isinstance(record, dict) and record.get("cveMetadata"):
            records.append(record)
    return records


def git_sync(data_dir: str | Path, repo_url: str = CVELIST_REPO) -> str:
    """Clone or pull the cvelistV5 repo into ``data_dir``; return the current commit sha.

    The sha is pinned as ``source_revision`` on ingested items for reproducibility.
    """
    path = Path(data_dir)
    if (path / ".git").exists():
        subprocess.run(["git", "-C", str(path), "pull", "--ff-only"], check=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", repo_url, str(path)], check=True)
    sha = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return sha.stdout.strip()
