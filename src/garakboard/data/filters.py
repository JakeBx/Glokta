"""SFTRecord dataclass, refusal filters, and quality filter pipeline for training data."""

from dataclasses import dataclass

REFUSAL_PREFIXES = [
    "i'm sorry",
    "i am sorry",
    "i cannot",
    "i can't",
    "i won't",
    "i will not",
    "i must decline",
    "i'm not able",
    "i am not able",
    "as an ai",
    "as a language model",
    "i don't feel comfortable",
    "i do not feel comfortable",
    "that's not something i",
    "that is not something i",
    "i'm unable",
    "i am unable",
    "my purpose is",
    "i'm designed to",
    "i was designed to",
]


@dataclass
class SFTRecord:
    prompt: str
    response: str | None
    owasp_id: str
    vulnerability: str
    source_model: str
    probe_name: str
    probe_category: str
    source: str
    detector_outcome: dict | None = None


def _has_response(r: SFTRecord) -> bool:
    return r.response is not None and r.response.strip() != ""


def _not_refusal(r: SFTRecord) -> bool:
    if not r.response:
        return True  # no response to check; handled by _has_response
    normalized = r.response.lower().strip()
    return not any(normalized.startswith(prefix) for prefix in REFUSAL_PREFIXES)


def _min_length(r: SFTRecord) -> bool:
    return len(r.response.strip()) >= 50


def _has_detector_hit(r: SFTRecord) -> bool:
    return bool(r.detector_outcome) and any(r.detector_outcome.values())


def apply_quality_filters(records: list[SFTRecord]) -> list[SFTRecord]:
    """Return records that pass all quality filters."""
    result = []
    for r in records:
        if not _has_response(r):
            continue
        if not _not_refusal(r):
            continue
        if not _min_length(r):
            continue
        if not _has_detector_hit(r):
            continue
        result.append(r)
    return result
