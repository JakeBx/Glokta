"""kev connector — CISA Known Exploited Vulnerabilities (no-auth JSON, CC0).

Provides the resolver for the exploitation Forecast task: a CVE appearing in KEV is a
positive (exploited), with ``dateAdded`` as the resolution date. The normalise step is the
tested core; the fetch is a thin httpx GET.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"


@dataclass
class KevEntry:
    cve_id: str
    date_added: date | None
    vendor: str | None
    product: str | None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def normalise_kev_feed(feed: dict) -> list[KevEntry]:
    """Parse the KEV JSON feed into KevEntry rows."""
    entries: list[KevEntry] = []
    for vuln in feed.get("vulnerabilities") or []:
        cve_id = vuln.get("cveID")
        if not cve_id:
            continue
        entries.append(
            KevEntry(
                cve_id=cve_id,
                date_added=_parse_date(vuln.get("dateAdded")),
                vendor=vuln.get("vendorProject"),
                product=vuln.get("product"),
            )
        )
    return entries


def fetch_kev(client: Any) -> list[KevEntry]:
    """Fetch and normalise the live KEV feed via an httpx-style client."""
    resp = client.get(KEV_URL)
    return normalise_kev_feed(resp.json())
