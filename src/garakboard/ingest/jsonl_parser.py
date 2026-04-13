"""Garak JSONL output file parser and ingest pipeline."""

import json
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from garakboard.models import ProbeResult, Attempt

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    """Result of ingesting a garak JSONL file."""

    probe_results_count: int
    attempts_count: int
    skipped_count: int


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

    return ProbeResult(
        run_id=run_uuid,
        probe_name=probe_name,
        probe_category=probe_category,
        detector=entry.get("detector", ""),
        pass_count=entry.get("passed", 0),
        fail_count=entry.get("failed", 0),
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

    probe = entry.get("probe", "")
    run_uuid = uuid.UUID(run_id)

    return Attempt(
        run_id=run_uuid,
        probe_name=probe,
        prompt=entry.get("prompt"),
        response=entry.get("response"),
        detector_outcome=entry.get("detector_results", {}),
    )


def ingest_jsonl_file(file_path: str, run_id: str, session: Session) -> IngestResult:
    """
    Parse an entire garak JSONL output file and insert all rows into the DB.

    Reads the file line by line. For each line:
    - If entry_type='eval': create a ProbeResult and add to session
    - If entry_type='attempt': create an Attempt and add to session
    - Otherwise: skip and increment skipped_count

    Malformed JSON and parse errors are logged and counted as skipped; they do
    not abort ingestion of the remaining lines.

    Flushes (but does not commit) after processing all lines. The caller is
    responsible for committing the transaction.

    Args:
        file_path: Path to the garak JSONL output file
        run_id: The UUID string of the DB run this file belongs to
        session: An active SQLAlchemy session

    Returns:
        IngestResult dataclass with probe_results_count, attempts_count,
        and skipped_count.

    Raises:
        FileNotFoundError: If file_path does not exist
    """
    probe_results_count = 0
    attempts_count = 0
    skipped_count = 0

    with open(file_path, "r") as f:
        for lineno, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Skipping line %d of %s: invalid JSON (%s)", lineno, file_path, exc)
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
                    "Skipping line %d of %s: parse error (%s)", lineno, file_path, exc
                )
                skipped_count += 1

    session.flush()

    return IngestResult(
        probe_results_count=probe_results_count,
        attempts_count=attempts_count,
        skipped_count=skipped_count,
    )
