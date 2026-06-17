"""CTI task prompt templates and answer extractors.

Answer extraction is ported from athenabench: strip answer-label prefixes, scan lines
bottom-to-top (the model's final answer is usually last), then apply a per-task regex.
``parse_response`` returns the structured prediction consumed by ``domain/cti/scoring``.
"""

import hashlib
import re
from typing import Any

# --- prompt templates -------------------------------------------------------

_RCM_TEMPLATE = (
    "You are a vulnerability analyst. Given the CVE description below, identify the most "
    "appropriate CWE (Common Weakness Enumeration) identifier(s).\n\n"
    "CVE description:\n{description}\n\n"
    "Respond with only the CWE id(s) in the form CWE-XXX.\nAnswer:"
)

_VSP_TEMPLATE = (
    "You are a vulnerability analyst. Given the CVE description below, predict the CVSS "
    "v3.1 base vector string.\n\n"
    "CVE description:\n{description}\n\n"
    "Respond with only the CVSS v3.1 vector, e.g. "
    "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H.\nAnswer:"
)

_FORECAST_TEMPLATE = (
    "You are a vulnerability prioritisation analyst. Given the CVE description below, "
    "estimate the probability that this vulnerability will be exploited in the wild.\n\n"
    "CVE description:\n{description}\n\n"
    "Respond with only a probability between 0 and 1 (e.g. 0.75).\nAnswer:"
)

_ATE_TEMPLATE = (
    "You are a threat-intelligence analyst. Identify the MITRE ATT&CK techniques described "
    "in the report excerpt below.\n\n"
    "Report excerpt:\n{description}\n\n"
    "Respond with only the ATT&CK technique ids (e.g. T1059), comma-separated.\nAnswer:"
)

_TAA_TEMPLATE = (
    "You are a threat-intelligence analyst. Based on the intrusion narrative below, name the "
    "most likely threat actor responsible.\n\n"
    "Narrative:\n{description}\n\n"
    "Respond with only the threat-actor name.\nAnswer:"
)

_SYN_TEMPLATE = (
    "You are a CTI analyst. From the raw inputs below, produce a concise threat assessment: "
    "name the most likely threat actor, relevant CVEs, MITRE ATT&CK techniques, targeted "
    "sectors, indicators of compromise, and recommended mitigations. Hedge explicitly where "
    "the evidence is weak.\n\n"
    "Inputs:\n{description}\n\n"
    "Assessment:"
)

_TEMPLATES: dict[str, str] = {
    "rcm": _RCM_TEMPLATE,
    "vsp": _VSP_TEMPLATE,
    "forecast": _FORECAST_TEMPLATE,
    "ate": _ATE_TEMPLATE,
    "taa": _TAA_TEMPLATE,
    "syn": _SYN_TEMPLATE,
}


# Cap model-input length so a single oversized item (e.g. a full advisory) can't blow the
# provider's context window / request limits. Generous but bounded.
_MAX_INPUT_CHARS = 8000


def build_prompt(task: str, input_text: str) -> str:
    """Render the prompt for ``task`` over the given model input text (length-capped)."""
    try:
        template = _TEMPLATES[task]
    except KeyError as exc:
        raise ValueError(f"No prompt template for task {task!r}") from exc
    if len(input_text) > _MAX_INPUT_CHARS:
        input_text = input_text[:_MAX_INPUT_CHARS] + "\n[...truncated]"
    return template.format(description=input_text)


def prompt_hash(prompt: str) -> str:
    """SHA256 hex digest of a prompt — reproducibility/cache key (athenabench convention)."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


# --- answer extraction ------------------------------------------------------

# Strip a leading "Answer:" / "Final Answer:" label so the regex sees the bare value.
_PREFIX_RE = re.compile(r"^\s*(?:final\s+)?answer\s*[:\-]?\s*", re.IGNORECASE)
_CWE_RE = re.compile(r"CWE-\d+", re.IGNORECASE)
_CVSS31_RE = re.compile(r"CVSS:3\.1/[^\s]+", re.IGNORECASE)
_PROB_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%?)")
# ATT&CK technique id; sub-technique suffix is dropped to the top-level id (athenabench).
_TECHNIQUE_RE = re.compile(r"T\d{4}(?:\.\d{3})?", re.IGNORECASE)
_QUOTES = "\"'`"
# Trailing characters that are never part of a CVSS vector (sentence punctuation, brackets).
_VECTOR_TRAILING = ".,;)]}>\"'"


def _lines_bottom_to_top(response: str) -> list[str]:
    """Cleaned, non-empty lines ordered last-first."""
    out = []
    for raw in reversed(response.splitlines()):
        line = _PREFIX_RE.sub("", raw).strip()
        if line:
            out.append(line)
    return out


def extract_cwes(response: str) -> set[str]:
    """Extract CWE ids; prefer the model's final (bottom-most) answer line."""
    for line in _lines_bottom_to_top(response):
        matches = _CWE_RE.findall(line)
        if matches:
            return {m.upper() for m in matches}
    return {m.upper() for m in _CWE_RE.findall(response)}


def extract_cvss_vector(response: str) -> str | None:
    """Extract a CVSS:3.1 vector; prefer the model's final (bottom-most) answer line."""
    for line in _lines_bottom_to_top(response):
        match = _CVSS31_RE.search(line)
        if match:
            return match.group(0).rstrip(_VECTOR_TRAILING)
    match = _CVSS31_RE.search(response)
    return match.group(0).rstrip(_VECTOR_TRAILING) if match else None


def extract_probability(response: str) -> float | None:
    """Extract a [0, 1] probability; prefer the final answer line. Handles percentages."""
    for line in _lines_bottom_to_top(response):
        match = _PROB_RE.search(line)
        if match:
            value = float(match.group(1))
            if match.group(2) == "%":
                value /= 100.0
            elif value > 1.0:
                # bare number > 1 is read as a percentage (e.g. "70" -> 0.70)
                value /= 100.0
            return min(1.0, max(0.0, value))
    return None


def extract_techniques(response: str) -> set[str]:
    """Extract ATT&CK technique ids across the whole response (top-level, deduped)."""
    out: set[str] = set()
    for match in _TECHNIQUE_RE.findall(response):
        out.add(match.upper().split(".")[0])  # drop sub-technique suffix
    return out


def extract_actor(response: str) -> str | None:
    """Extract a free-form threat-actor name from the model's final answer line."""
    for line in _lines_bottom_to_top(response):
        cleaned = line.strip().strip(_QUOTES).strip()
        if cleaned:
            return cleaned
    return None


def parse_response(task: str, response: str) -> Any:
    """Parse a raw model response into the structured prediction for ``task``."""
    if task == "rcm":
        return extract_cwes(response)
    if task == "vsp":
        return extract_cvss_vector(response)
    if task == "forecast":
        return extract_probability(response)
    if task == "ate":
        return extract_techniques(response)
    if task == "taa":
        return extract_actor(response)
    raise ValueError(f"No response parser for task {task!r}")
