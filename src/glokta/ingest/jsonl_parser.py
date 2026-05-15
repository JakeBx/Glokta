"""Garak JSONL output file parser and ingest pipeline."""

import json
import logging
import uuid
from dataclasses import dataclass
from typing import TextIO

from sqlalchemy.orm import Session

from glokta.models import ProbeResult, Attempt

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    """Result of ingesting a garak JSONL file."""

    probe_results_count: int
    attempts_count: int
    skipped_count: int


def _extract_prompt_text(prompt) -> str | None:
    """
    Extract plain text from a garak prompt.

    Handles both the legacy format (plain string) and the garak >=0.14
    Conversation format (dict with a 'turns' list).
    """
    if prompt is None:
        return None
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, dict):
        turns = prompt.get("turns") or []
        if turns:
            content = turns[0].get("content", {})
            if isinstance(content, dict):
                return content.get("text", "")
            return str(content)
    return str(prompt)


def _extract_response_text(outputs) -> str | None:
    """
    Extract plain text from garak outputs.

    Handles both the legacy format (plain string or None) and the garak >=0.14
    format (list of dicts with a 'text' key).
    """
    if outputs is None:
        return None
    if isinstance(outputs, list):
        if not outputs:
            return None
        first = outputs[0]
        if isinstance(first, dict):
            return first.get("text")
        return str(first)
    if isinstance(outputs, str):
        return outputs
    return None


def parse_eval_entry(entry: dict, run_id: str) -> ProbeResult:
    """
    Parse a garak 'eval' JSONL entry into a ProbeResult ORM object.

    Args:
        entry: Parsed dict from a JSONL line with entry_type='eval'
        run_id: The UUID string of the DB run record this entry belongs to

    Returns:
        A ProbeResult instance (not yet added to a session)

    Raises:
        ValueError: If entry_type is not 'eval' or required fields are missing
    """
    if entry.get("entry_type") != "eval":
        raise ValueError(f"Expected entry_type 'eval', got '{entry.get('entry_type')}'")

    probe = entry.get("probe", "")
    if "." in probe:
        probe_category = probe.split(".", 1)[0]
        probe_name = probe
    else:
        probe_category = probe
        probe_name = probe

    run_uuid = uuid.UUID(run_id)

    # garak >=0.14 uses 'fails'; older versions used 'failed'
    fail_count = entry.get("fails", entry.get("failed", 0)) or 0

    return ProbeResult(
        run_id=run_uuid,
        probe_name=probe_name,
        probe_category=probe_category,
        detector=entry.get("detector", ""),
        pass_count=entry.get("passed", 0) or 0,
        fail_count=fail_count,
        score=entry.get("score"),
    )


def parse_attempt_entry(entry: dict, run_id: str) -> Attempt:
    """
    Parse a garak 'attempt' JSONL entry into an Attempt ORM object.

    Args:
        entry: Parsed dict from a JSONL line with entry_type='attempt'
        run_id: The UUID string of the DB run record this entry belongs to

    Returns:
        An Attempt instance (not yet added to a session)

    Raises:
        ValueError: If entry_type is not 'attempt' or required fields are missing
    """
    if entry.get("entry_type") != "attempt":
        raise ValueError(f"Expected entry_type 'attempt', got '{entry.get('entry_type')}'")

    # garak >=0.14 uses 'probe_classname'; older versions used 'probe'
    probe = entry.get("probe_classname") or entry.get("probe", "")
    run_uuid = uuid.UUID(run_id)

    return Attempt(
        run_id=run_uuid,
        probe_name=probe,
        prompt=_extract_prompt_text(entry.get("prompt")),
        response=_extract_response_text(entry.get("outputs") or entry.get("response")),
        detector_outcome=entry.get("detector_results", {}),
    )


def ingest_jsonl_file(source: str | TextIO, run_id: str, session: Session) -> IngestResult:
    """
    Parse a garak JSONL output file and insert all rows into the DB.

    source may be a file path string or any file-like text object (e.g. io.StringIO),
    allowing callers that already have the content in memory to avoid a second disk read.
    """
    probe_results_count = 0
    attempts_count = 0
    skipped_count = 0

    f: TextIO = open(source, "r", encoding="utf-8", errors="replace") if isinstance(source, str) else source
    try:
        for lineno, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Skipping line %d of %s: invalid JSON (%s)", lineno, source, exc)
                skipped_count += 1
                continue

            entry_type = entry.get("entry_type")

            try:
                if entry_type == "eval":
                    probe_result = parse_eval_entry(entry, run_id)
                    session.add(probe_result)
                    probe_results_count += 1
                elif entry_type == "attempt":
                    attempt = parse_attempt_entry(entry, run_id)
                    session.add(attempt)
                    attempts_count += 1
                else:
                    skipped_count += 1
            except (ValueError, KeyError) as exc:
                logger.warning(
                    "Skipping line %d of %s: parse error (%s)", lineno, source, exc
                )
                skipped_count += 1
    finally:
        if isinstance(source, str):
            f.close()

    session.flush()

    return IngestResult(
        probe_results_count=probe_results_count,
        attempts_count=attempts_count,
        skipped_count=skipped_count,
    )
