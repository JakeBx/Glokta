"""SYN claim-set construction (hybrid) and section-based input reconstruction.

- ``build_claim_set`` (label side): deterministic claims for actor/CVE/technique/IOC; a pinned
  LLM (injected as ``judge_infer``) adds sectors, mitigations, and hedge levels — the fuzzy prose
  residue only. Deterministic-only when no judge is supplied.
- ``claim_set_from_text`` (model side): deterministic claims from a model's free-text assessment.
- ``reconstruct_inputs``: keep observation sections, drop conclusion sections, so SYN scores
  analysis rather than summarisation.
"""

import json
import logging
import re
from typing import Callable

from glokta.config import settings
from glokta.domain.cti.claims import Claim, ClaimSet
from glokta.infrastructure.cti.connectors.report import detect_actor

logger = logging.getLogger(__name__)

_CVE_RE = re.compile(r"CVE-\d{4}-\d+", re.IGNORECASE)
_TECHNIQUE_RE = re.compile(r"T\d{4}", re.IGNORECASE)
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")

# Sections that are observations (model inputs) vs conclusions (must be withheld).
_INPUT_SECTIONS = ("Overview", "Technical Details", "Indicators of Compromise")

_JUDGE_PROMPT = (
    "Extract structured facts from this CISA advisory. Return JSON with keys: "
    '"sectors" (list of targeted sectors), "mitigations" (list of recommended mitigations), '
    'and "hedges" (object mapping a claim value to a hedge level 0.0=asserted..1.0=hedged).\n\n'
    "Advisory:\n{text}\n\nJSON:"
)


def _iocs(text: str) -> list[str]:
    return _IP_RE.findall(text) + _SHA256_RE.findall(text)


def _parse_judge_json(raw: str) -> dict:
    """Best-effort parse of the first JSON object in the judge's reply."""
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        logger.warning("claim_extraction: could not parse judge JSON")
        return {}


def build_claim_set(
    advisory: dict,
    *,
    judge_infer: Callable[[str, str], str] | None = None,
    judge_model: str | None = None,
) -> ClaimSet:
    """Build the label claim set for a SYN item (hybrid: rules + optional pinned LLM)."""
    claims: list[Claim] = []
    if advisory.get("actor"):
        claims.append(Claim("actor", advisory["actor"]))
    for cve in advisory.get("cves") or []:
        claims.append(Claim("cve", cve.upper()))
    for tech in advisory.get("techniques") or []:
        claims.append(Claim("technique", tech.upper()))
    ioc_section = (advisory.get("sections") or {}).get("Indicators of Compromise", "")
    for ioc in _iocs(ioc_section):
        claims.append(Claim("ioc", ioc))

    if judge_infer is not None:
        prompt = _JUDGE_PROMPT.format(text=advisory.get("text") or "")
        parsed = _parse_judge_json(judge_infer(judge_model or settings.cti_judge_model, prompt))
        for sector in parsed.get("sectors") or []:
            claims.append(Claim("sector", str(sector)))
        for mitigation in parsed.get("mitigations") or []:
            claims.append(Claim("mitigation", str(mitigation)))
        hedges = parsed.get("hedges") or {}
        for claim in claims:
            if claim.value in hedges:
                try:
                    claim.hedge_level = float(hedges[claim.value])
                except (TypeError, ValueError):
                    pass

    return ClaimSet(claims=claims)


def claim_set_from_text(text: str, alias_index: dict[str, str] | None = None) -> ClaimSet:
    """Deterministic claim set from a model's free-text assessment (the model side)."""
    claims: list[Claim] = []
    actor = detect_actor(text, alias_index or {})
    if actor:
        claims.append(Claim("actor", actor))
    for cve in {c.upper() for c in _CVE_RE.findall(text)}:
        claims.append(Claim("cve", cve))
    for tech in {t.upper() for t in _TECHNIQUE_RE.findall(text)}:
        claims.append(Claim("technique", tech))
    for ioc in _iocs(text):
        claims.append(Claim("ioc", ioc))
    return ClaimSet(claims=claims)


def reconstruct_inputs(advisory: dict) -> str:
    """Section-based input reconstruction: keep observations, drop conclusions."""
    sections = advisory.get("sections") or {}
    parts = [sections[name] for name in _INPUT_SECTIONS if sections.get(name)]
    return "\n\n".join(parts).strip()


# Word-bounded ATT&CK technique id (incl. sub-technique), e.g. T1059 or T1566.001.
_TECH_ID_RE = re.compile(r"\bT\d{4}(?:\.\d{3})?\b", re.IGNORECASE)


def _actor_terms(actor: str | None, alias_index: dict[str, str] | None) -> set[str]:
    """The actor's canonical name + all aliases that resolve to it (>= 3 chars)."""
    if not actor:
        return set()
    terms = {actor}
    if alias_index:
        canonical = alias_index.get(actor.lower(), actor)
        terms.add(canonical)
        terms.update(a for a, c in alias_index.items() if c == canonical)
    return {t for t in terms if t and len(t) >= 3}


def mask_conclusions(
    text: str,
    *,
    actor: str | None = None,
    alias_index: dict[str, str] | None = None,
) -> str:
    """Remove the synthesised *labels* (ATT&CK technique ids; actor name + aliases) from the
    reconstructed inputs, keeping the surrounding behavioural evidence.

    Masking technique *ids* (not the behavioural prose) is the legitimate transform that makes
    SYN a synthesis task rather than a copy task. Technique *names* are intentionally left as
    evidence. Actor names are masked because attribution is a conclusion.
    """
    masked = _TECH_ID_RE.sub("[technique]", text)
    for term in sorted(_actor_terms(actor, alias_index), key=len, reverse=True):
        masked = re.sub(re.escape(term), "[actor]", masked, flags=re.IGNORECASE)
    return masked
