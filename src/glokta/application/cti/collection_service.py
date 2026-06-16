"""Multi-source advisory collection for the report-driven tasks (ATE / TAA / SYN).

Collects recent advisories from the configured sources (CISA + national CERTs + The DFIR
Report), parses each page, filters to the lookback window, detects the actor against the alias
index, and dedupes across sources (joint advisories are co-sealed, so the same report arrives
from several sources). Shared by ``cti_ingest_report`` and ``cti_ingest_syn`` so both ingest the
same deduped, recency-bounded pool.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from glokta.infrastructure.cti.connectors.report import (
    SOURCES,
    detect_actor,
    fetch_source_index,
    parse_advisory_page,
    parse_source_advisory,
)
from glokta.infrastructure.cti.dedupe import dedupe_advisories

logger = logging.getLogger(__name__)


def _parse_for_source(entry: dict, html: str, source: str) -> dict:
    """Parse a page with the source's parser. CISA keeps its battle-tested dedicated parser."""
    if source == "cisa":
        advisory = parse_advisory_page(entry, html)
        advisory["source_site"] = "cisa"
        advisory.setdefault("title", entry.get("title", ""))
        return advisory
    return parse_source_advisory(entry, html, source)


def _published_within(published: Any, cutoff: date) -> bool:
    """Keep an advisory if it has no date (best-effort) or was published on/after ``cutoff``."""
    if published is None:
        return True
    value = published.date() if isinstance(published, datetime) else published
    try:
        return value >= cutoff
    except TypeError:
        return True


def collect_source_advisories(
    client: Any,
    source: str,
    alias_index: dict[str, str],
    *,
    max_items: int,
    max_pages: int = 2,
    headers: dict | None = None,
) -> list[dict]:
    """Enumerate + parse up to ``max_items`` advisories from one source (best-effort per page)."""
    out: list[dict] = []
    try:
        urls = fetch_source_index(client, source, max_pages=max_pages, headers=headers)[:max_items]
    except Exception as exc:  # noqa: BLE001 - a blocked/slow source contributes nothing
        logger.warning("collect %s: index unavailable: %s", source, exc)
        return out
    for url in urls:
        try:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            advisory = _parse_for_source({"link": url}, resp.text, source)
            advisory["actor"] = detect_actor(advisory.get("text") or "", alias_index)
            out.append(advisory)
        except Exception as exc:  # noqa: BLE001
            logger.warning("collect %s: fetch/parse failed %s: %s", source, url, exc)
    return out


def collect_advisories(
    client: Any,
    alias_index: dict[str, str],
    *,
    sources: list[str],
    lookback_days: int,
    max_per_source: int,
    now: date | None = None,
    headers: dict | None = None,
) -> tuple[list[dict], dict]:
    """Collect recent advisories across ``sources``, lookback-filter, and dedupe across sources.

    Returns ``(advisories, stats)`` where stats records per-source fetched/recent counts and the
    dedupe kept/dropped totals — for flow logging.
    """
    today = now or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=lookback_days)

    pool: list[dict] = []
    stats: dict = {}
    for source in sources:
        if source not in SOURCES:
            logger.warning("collect_advisories: unknown source %r skipped", source)
            continue
        fetched = collect_source_advisories(
            client, source, alias_index, max_items=max_per_source, headers=headers
        )
        recent = [a for a in fetched if _published_within(a.get("published"), cutoff)]
        stats[source] = {"fetched": len(fetched), "recent": len(recent)}
        pool.extend(recent)

    result = dedupe_advisories(pool)
    stats["dedupe"] = {"kept": len(result.kept), "dropped": len(result.dropped)}
    logger.info("collect_advisories: %s", stats)
    return result.kept, stats
