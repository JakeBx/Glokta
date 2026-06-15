"""CTI source connectors (OpenCTI external-import pattern, as Prefect flows).

Each connector exposes a pure ``normalise`` step (raw source record -> NormalisedCtiItem
rows or reference rows) plus a thin ``fetch`` step. The temporal invariants (anchoring
first_available_date, mutable-label snapshotting, withhold) are enforced centrally in
``application/cti/ingest_service``, not here.
"""
