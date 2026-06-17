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
# The AA-series (Cybersecurity Advisory) listing — far richer than the RSS feed, which is
# shallow and dominated by KEV/ICS notices. advisory_type:94 = "Cybersecurity Advisory".
CISA_ADVISORIES_LISTING = "https://www.cisa.gov/news-events/cybersecurity-advisories"
_AA_TYPE_FACET = "advisory_type:94"
_AA_SLUG_RE = re.compile(
    r"/news-events/cybersecurity-advisories/(aa\d{2}-\d{3}[a-z]?)", re.IGNORECASE
)
# Bare (not [?&]-anchored): pager hrefs encode the separator as the &amp; entity.
_PAGER_PAGE_RE = re.compile(r"page=(\d+)")

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


def fetch_aa_advisory_index(
    client: Any,
    *,
    max_pages: int | None = None,
    headers: dict | None = None,
) -> list[str]:
    """Page the CISA Cybersecurity-Advisories (AA-series) listing → advisory URLs, newest-first.

    Walks ``?f[0]=advisory_type:94&page=N`` (0-indexed). The first page's pager reveals the last
    page number; iteration stops there, when ``max_pages`` is reached, or when a page yields no
    new advisories. Slugs are de-duplicated while preserving newest-first order.
    """
    urls: list[str] = []
    seen: set[str] = set()
    page = 0
    last_page: int | None = None
    while True:
        params: dict = {"f[0]": _AA_TYPE_FACET}
        if page:
            params["page"] = page
        resp = client.get(CISA_ADVISORIES_LISTING, params=params, headers=headers)
        resp.raise_for_status()
        html = resp.text

        if last_page is None:
            nums = [int(n) for n in _PAGER_PAGE_RE.findall(html)]
            last_page = max(nums) if nums else 0

        new = 0
        for slug in _AA_SLUG_RE.findall(html):
            slug = slug.lower()
            if slug not in seen:
                seen.add(slug)
                urls.append(f"{CISA_ADVISORIES_LISTING}/{slug}")
                new += 1

        page += 1
        if new == 0 or page > last_page:
            break
        if max_pages is not None and page >= max_pages:
            break
    return urls


# --- AA-series advisory HTML parsing -------------------------------------------------------
# Real CISA AA pages keep their body in a `field--name-body` container and split it with
# h2/h3 headings whose names vary by era. We isolate the body, split by actual headings, and
# classify each as observation (kept as model input) vs conclusion/chrome (dropped).

from html.parser import HTMLParser as _HTMLParser  # noqa: E402

# Heading keywords (lower-cased substring match). IOC checked first, then DROP, then OVERVIEW.
_IOC_KEYWORDS = ("indicator", "ioc")
_OVERVIEW_KEYWORDS = ("overview", "introduction", "background")
_DROP_KEYWORDS = (
    "summary", "mitigation", "mitre", "att&ck", "detection", "validate security",
    "recommend", "reference", "resource", "disclaimer", "acknowledg", "version history",
    "revision", "reporting", "contact", "tag", "archived", "purpose", "advice",
    "protective", "best practice", "appendix", "disclosure",
)


def _isolate_body(html: str, markers: tuple[str, ...], end_markers: tuple[str, ...]) -> str:
    """Isolate the advisory body region: keep text after the first ``markers`` hit (a content
    container), then cut at the first ``end_markers`` hit (trailing chrome/related). Falls back to
    the whole document when no marker matches — the heading classifier still drops the chrome."""
    body = html
    for marker in markers:
        idx = html.find(marker)
        if idx != -1:
            body = html[idx + len(marker):]
            break
    for marker in end_markers:
        idx = body.find(marker)
        if idx != -1:
            body = body[:idx]
            break
    return body


_CISA_BODY_MARKERS = ("field--name-body",)
_CISA_END_MARKERS = ("Please share your thoughts", ">Related Advisories<", "field--name-field-tags")


def _advisory_body(html: str) -> str:
    """Isolate the CISA advisory body region (drop nav/header; cut off chrome/related at the end)."""
    return _isolate_body(html, _CISA_BODY_MARKERS, _CISA_END_MARKERS)


def _classify_heading(heading: str, extra_drop: tuple[str, ...] = ()) -> tuple[bool, str]:
    """(keep?, canonical_bucket) for a section heading.

    ``extra_drop`` adds source-specific chrome/conclusion keywords (e.g. CCCS "audience"/"ttp").
    """
    h = heading.lower()
    if any(k in h for k in _IOC_KEYWORDS):
        return True, "Indicators of Compromise"
    if any(k in h for k in _DROP_KEYWORDS) or any(k in h for k in extra_drop):
        return False, heading
    if any(k in h for k in _OVERVIEW_KEYWORDS):
        return True, "Overview"
    return True, "Technical Details"  # default: treat freeform sections as technical narrative


class _AdvisorySectionParser(_HTMLParser):
    """Split a body-region HTML fragment into (heading, text) sections on h2/h3/h4."""

    _HEADS = {"h2", "h3", "h4"}
    _BLOCKS = {"p", "br", "li", "tr", "div", "ul", "ol", "table", "section"}

    def __init__(self) -> None:
        super().__init__()
        self.sections: list[tuple[str, str]] = []
        self._head: str | None = None
        self._buf: list[str] = []
        self._head_buf: list[str] = []
        self._in_head = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._HEADS:
            self._flush()
            self._in_head = True
            self._head_buf = []
        elif tag in self._BLOCKS and not self._in_head:
            self._buf.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self._HEADS and self._in_head:
            self._head = " ".join("".join(self._head_buf).split())
            self._in_head = False

    def handle_data(self, data):
        if self._skip:
            return
        (self._head_buf if self._in_head else self._buf).append(data)

    def _flush(self):
        if self._head is not None:
            text = " ".join("".join(self._buf).split())
            self.sections.append((self._head, text))
        self._buf = []

    def result(self) -> list[tuple[str, str]]:
        self._flush()
        return self.sections


def _release_date(html: str) -> date | None:
    match = re.search(r'datetime="(\d{4}-\d{2}-\d{2})', html)
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            return None
    return None


def parse_advisory_page(entry: dict, html: str) -> dict:
    """Parse a real CISA AA advisory HTML page into the advisory dict the SYN/ATE/TAA path uses.

    Sections are bucketed into canonical keys (Overview / Technical Details / Indicators of
    Compromise = observations kept as model input; Dropped = conclusions + chrome) so
    reconstruct_inputs and build_claim_set work unchanged. Actor is left None for the caller.
    """
    import html as _htmlmod

    parser = _AdvisorySectionParser()
    parser.feed(_advisory_body(html))
    raw_sections = parser.result()

    buckets: dict[str, list[str]] = {
        "Overview": [], "Technical Details": [], "Indicators of Compromise": [], "Dropped": []
    }
    for heading, text in raw_sections:
        text = _htmlmod.unescape(text).strip()
        if not text:
            continue
        keep, bucket = _classify_heading(_htmlmod.unescape(heading))
        if keep:
            buckets[bucket].append(text)
        else:
            buckets["Dropped"].append(f"[{heading}] {text}")
    sections = {k: "\n\n".join(v) for k, v in buckets.items() if v}

    full_text = "\n".join(_htmlmod.unescape(t).strip() for _, t in raw_sections if t.strip())
    slug = re.search(r"(aa\d{2}-\d{3}[a-z]?)", entry.get("link", "") + entry.get("title", ""), re.I)
    advisory_id = slug.group(1).upper() if slug else (entry.get("id") or "UNKNOWN")

    return {
        "id": advisory_id,
        "published": entry.get("published_date") or _release_date(html),
        "text": full_text,
        "sections": sections,
        "techniques": sorted({t.upper() for t in _TECHNIQUE_RE.findall(full_text)}),
        "cves": sorted({c.upper() for c in _CVE_RE.findall(full_text)}),
        "actor": None,
    }


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


# --- Multi-source advisory collection (CISA + national CERTs) -------------------------------
# Each source reuses the CISA parser core (_AdvisorySectionParser + _classify_heading) but adds
# its own listing/link pattern, body-container markers, id pattern, and chrome drop-keywords.

from dataclasses import dataclass  # noqa: E402


@dataclass(frozen=True)
class SourceConfig:
    name: str
    base_url: str
    listing_url: str
    listing_params: dict
    link_re: "re.Pattern[str]"
    body_markers: tuple[str, ...]
    end_markers: tuple[str, ...]
    extra_drop: tuple[str, ...] = ()
    id_re: "re.Pattern[str] | None" = None


SOURCES: dict[str, SourceConfig] = {
    "cisa": SourceConfig(
        name="cisa",
        base_url="https://www.cisa.gov",
        listing_url=CISA_ADVISORIES_LISTING,
        listing_params={"f[0]": _AA_TYPE_FACET},
        link_re=re.compile(r"/news-events/cybersecurity-advisories/aa\d{2}-\d{3}[a-z]?", re.I),
        body_markers=_CISA_BODY_MARKERS,
        end_markers=_CISA_END_MARKERS,
        id_re=re.compile(r"AA\d{2}-\d{3}[a-z]?", re.I),
    ),
    "cccs": SourceConfig(
        name="cccs",
        base_url="https://www.cyber.gc.ca",
        listing_url="https://www.cyber.gc.ca/en/alerts-advisories",
        listing_params={},
        link_re=re.compile(r"/en/(?:alerts-advisories|alerts)/[a-z0-9][a-z0-9-]+", re.I),
        body_markers=("field--name-body", "node__content", "region-content"),
        end_markers=("Date modified", "Report a problem", "field--name-field-date-modified"),
        # CCCS-specific chrome/conclusion headings that the CISA keyword set doesn't cover.
        extra_drop=("audience", "suggested action", "tactics", "ttp"),
        id_re=re.compile(r"A[LV]\d{2}-\d{3}", re.I),
    ),
    "ncsc": SourceConfig(
        name="ncsc",
        base_url="https://www.ncsc.gov.uk",
        listing_url="https://www.ncsc.gov.uk/section/keep-up-to-date/reports-advisories",
        listing_params={},
        link_re=re.compile(r"/news/[a-z0-9][a-z0-9-]+", re.I),
        body_markers=("nojs-content", "<article", "<main"),
        end_markers=("Was this", "Related content", "Published on"),
    ),
    "dfir": SourceConfig(
        name="dfir",
        base_url="https://thedfirreport.com",
        # The RSS feed is the enumerator (the homepage is JS-heavy); link_re matches the
        # date-slug post URLs in the feed's <link>/<guid> elements.
        listing_url="https://thedfirreport.com/feed/",
        listing_params={},
        link_re=re.compile(r"https?://thedfirreport\.com/\d{4}/\d{2}/\d{2}/[a-z0-9][a-z0-9-]+/?", re.I),
        body_markers=("entry-content", "<article"),
        end_markers=("Post navigation", "Related Posts", "Comments are closed", "You may also like"),
        # ATT&CK-tactic sections are the intrusion narrative (kept); the attribution/commercial/
        # detection appendices are conclusions or chrome and must be withheld.
        extra_drop=("diamond model", "services"),
    ),
}

_URL_DATE_RE = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/")


def _url_date(link: str | None) -> date | None:
    """Date embedded in a post URL (e.g. DFIR ``/2026/02/23/slug/``), else None."""
    if not link:
        return None
    match = _URL_DATE_RE.search(link)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def _extract_id(entry: dict, full_text: str, cfg: SourceConfig) -> str:
    """Source id: a numbered advisory id (AA../AL../AV..) if present, else the URL slug."""
    if cfg.id_re is not None:
        hay = f"{entry.get('link', '')} {entry.get('title', '')} {full_text}"
        match = cfg.id_re.search(hay)
        if match:
            return match.group(0).upper()
    link = (entry.get("link") or "").rstrip("/")
    slug = link.rsplit("/", 1)[-1] if link else ""
    return slug or entry.get("id") or "UNKNOWN"


def parse_source_advisory(entry: dict, html: str, source: str) -> dict:
    """Parse one advisory page from any registered source into the advisory dict the
    SYN/ATE/TAA path uses. Same shape as ``parse_advisory_page`` plus ``source_site``/``title``,
    so ``reconstruct_inputs``/``build_claim_set``/``dedupe_advisories`` work unchanged."""
    import html as _htmlmod

    cfg = SOURCES[source]
    parser = _AdvisorySectionParser()
    parser.feed(_isolate_body(html, cfg.body_markers, cfg.end_markers))
    raw_sections = parser.result()

    buckets: dict[str, list[str]] = {
        "Overview": [], "Technical Details": [], "Indicators of Compromise": [], "Dropped": []
    }
    for heading, text in raw_sections:
        text = _htmlmod.unescape(text).strip()
        if not text:
            continue
        keep, bucket = _classify_heading(_htmlmod.unescape(heading), cfg.extra_drop)
        if keep:
            buckets[bucket].append(text)
        else:
            buckets["Dropped"].append(f"[{heading}] {text}")
    sections = {k: "\n\n".join(v) for k, v in buckets.items() if v}

    full_text = "\n".join(_htmlmod.unescape(t).strip() for _, t in raw_sections if t.strip())
    return {
        "id": _extract_id(entry, full_text, cfg),
        "source_site": source,
        "title": entry.get("title", ""),
        "published": entry.get("published_date") or _release_date(html) or _url_date(entry.get("link")),
        "text": full_text,
        "sections": sections,
        "techniques": sorted({t.upper() for t in _TECHNIQUE_RE.findall(full_text)}),
        "cves": sorted({c.upper() for c in _CVE_RE.findall(full_text)}),
        "actor": None,
    }


def fetch_source_index(
    client: Any,
    source: str,
    *,
    max_pages: int = 1,
    headers: dict | None = None,
) -> list[str]:
    """Scrape a source's listing for advisory URLs (newest-first, de-duplicated, absolute).

    Walks up to ``max_pages`` of the listing (appending ``page=N`` when paging); stops early when
    a page yields no new advisory links. Best-effort: a source whose listing is JS-rendered or
    blocked simply returns fewer (or no) URLs.
    """
    cfg = SOURCES[source]
    urls: list[str] = []
    seen: set[str] = set()
    for page in range(max_pages):
        params = dict(cfg.listing_params)
        if page:
            params["page"] = page
        resp = client.get(cfg.listing_url, params=params or None, headers=headers)
        resp.raise_for_status()
        new = 0
        for path in cfg.link_re.findall(resp.text):
            url = path if path.startswith("http") else cfg.base_url + path
            if url not in seen:
                seen.add(url)
                urls.append(url)
                new += 1
        if new == 0:
            break
    return urls
