"""report connector — CISA advisories (AA-series, ICS) -> ATE/TAA input items.

``normalise_report`` turns a parsed advisory dict into ATE (report -> techniques) and/or
TAA (narrative -> actor) items. The RSS poll + HTML/ATT&CK-table extraction that produces
the parsed advisory dict is the thin shim; the normalise is the tested core.

Expected parsed-advisory shape::

    {"id": "AA24-001A", "published": "2024-01-10", "text": "...",
     "techniques": ["T1059", ...], "actor": "APT29"}
"""

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from glokta.infrastructure.cti.connectors.base import NormalisedCtiItem

logger = logging.getLogger(__name__)

SOURCE = "report"
CISA_ADVISORIES_RSS = "https://www.cisa.gov/cybersecurity-advisories/all.xml"

# Section headings used to segment a CISA advisory (drives input reconstruction in SYN).
SECTION_HEADINGS = [
    "Summary",
    "Overview",
    "Technical Details",
    "Threat Actor Activity",
    "Attribution",
    "Indicators of Compromise",
    "MITRE ATT&CK Techniques",
    "MITRE ATT&CK Tactics and Techniques",
    "Detection",
    "Detections",
    "Mitigations",
]
_HEADING_LOOKUP = {h.lower(): h for h in SECTION_HEADINGS}

_AA_ID_RE = re.compile(r"AA\d{2}-\d{3}[A-Za-z]?")
_TECHNIQUE_RE = re.compile(r"T\d{4}", re.IGNORECASE)
_CVE_RE = re.compile(r"CVE-\d{4}-\d+", re.IGNORECASE)


def split_sections(text: str) -> dict[str, str]:
    """Segment advisory plain text into {heading: body} on known CISA headings."""
    sections: dict[str, str] = {}
    current = "_preamble"
    buf: list[str] = []
    for line in text.splitlines():
        heading = _HEADING_LOOKUP.get(line.strip().rstrip(":").strip().lower())
        if heading:
            sections[current] = "\n".join(buf).strip()
            current = heading
            buf = []
        else:
            buf.append(line)
    sections[current] = "\n".join(buf).strip()
    return sections


def detect_actor(text: str, alias_index: dict[str, str]) -> str | None:
    """Return the canonical actor whose name/alias first appears in the text, else None."""
    lower = text.lower()
    best: tuple[int, str] | None = None
    for alias, canonical in alias_index.items():
        if not alias:
            continue
        pos = lower.find(alias)
        if pos != -1 and (best is None or pos < best[0]):
            best = (pos, canonical)
    return best[1] if best else None


def parse_advisory(entry: dict, page_text: str) -> dict:
    """Parse one advisory (feed entry + page text) into the dict normalise_report expects.

    Actor is left None here — the flow fills it via detect_actor against the galaxy aliases.
    """
    title = entry.get("title", "")
    match = _AA_ID_RE.search(title) or _AA_ID_RE.search(entry.get("link", ""))
    advisory_id = match.group(0) if match else (entry.get("id") or title)
    techniques = sorted({t.upper() for t in _TECHNIQUE_RE.findall(page_text)})
    cves = sorted({c.upper() for c in _CVE_RE.findall(page_text)})
    return {
        "id": advisory_id,
        "published": entry.get("published_date"),
        "text": page_text,
        "sections": split_sections(page_text),
        "techniques": techniques,
        "cves": cves,
        "actor": None,
    }


def fetch_advisory_feed(
    client: Any,
    lookback_days: int,
    now: date | None = None,
    headers: dict | None = None,
) -> list[dict]:
    """Fetch the CISA advisories RSS and return entries within the lookback window."""
    import feedparser  # type: ignore[import-untyped]

    resp = client.get(CISA_ADVISORIES_RSS, headers=headers)
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)

    today = now or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=lookback_days)
    entries: list[dict] = []
    for entry in feed.entries:
        published: date | None = None
        if getattr(entry, "published_parsed", None):
            published = date(*entry.published_parsed[:3])
        if published is not None and published < cutoff:
            continue
        entries.append(
            {
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published_date": published,
            }
        )
    return entries


def _parse_date(value: str | date | datetime | None) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def normalise_report(advisory: dict) -> list[NormalisedCtiItem]:
    """Normalise one parsed advisory into ATE and/or TAA items."""
    advisory_id = advisory.get("id")
    text = advisory.get("text") or ""
    if not advisory_id or not text:
        return []

    published = _parse_date(advisory.get("published"))
    difficulty = {"description_length": len(text)}
    items: list[NormalisedCtiItem] = []

    techniques = [t.upper() for t in (advisory.get("techniques") or []) if t]
    if techniques:
        items.append(
            NormalisedCtiItem(
                task="ate",
                external_id=advisory_id,
                source=SOURCE,
                input_text=text,
                label={"techniques": techniques},
                label_provenance={"report": techniques},
                input_date=published,
                label_date=published,
                authority_agreement="single",
                difficulty={**difficulty, "technique_count": len(techniques)},
            )
        )

    actor = advisory.get("actor")
    if actor:
        items.append(
            NormalisedCtiItem(
                task="taa",
                external_id=advisory_id,
                source=SOURCE,
                input_text=text,
                label={"actor": actor},
                label_provenance={"report": [actor]},
                input_date=published,
                label_date=published,
                authority_agreement="single",
                difficulty=difficulty,
            )
        )

    return items
