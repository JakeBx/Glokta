"""Pipeline serve entrypoint.

Runs both Prefect flows on schedule without requiring a Prefect Server.
Used as the CMD in Dockerfile.pipeline for local Docker Compose.

For production (Prefect Server + Worker), use prefect.yaml + `prefect deploy --all`
and run the worker with: prefect worker start --pool glokta-process-pool
"""

import asyncio
import logging

from glokta.pipeline.cti_flows import (
    cti_ingest_attack,
    cti_ingest_cve,
    cti_ingest_galaxy,
    cti_ingest_kev,
    cti_ingest_report,
    cti_ingest_syn,
    cti_scan_pending,
    cti_trigger,
)
from glokta.pipeline.flows import scan_pending_runs, sync_top_models, trigger_weekly_scans

logger = logging.getLogger(__name__)


async def _serve():
    await asyncio.gather(
        scan_pending_runs.serve(
            name="scan-pending-runs",
            interval=900,  # every 15 minutes
        ),
        sync_top_models.serve(
            name="sync-top-models",
            cron="0 1 * * 1",  # Monday 01:00 UTC
        ),
        trigger_weekly_scans.serve(
            name="trigger-weekly-scans",
            cron="0 2 * * 1",  # Monday 02:00 UTC
        ),
        cti_ingest_cve.serve(
            name="cti-ingest-cve",
            cron="0 * * * *",  # hourly
        ),
        cti_ingest_kev.serve(
            name="cti-ingest-kev",
            cron="30 0 * * *",  # daily 00:30 UTC
        ),
        cti_ingest_attack.serve(
            name="cti-ingest-attack",
            cron="0 4 * * 1",  # Monday 04:00 UTC
        ),
        cti_ingest_galaxy.serve(
            name="cti-ingest-galaxy",
            cron="30 4 * * 1",  # Monday 04:30 UTC
        ),
        cti_ingest_report.serve(
            name="cti-ingest-report",
            cron="0 5 * * *",  # daily 05:00 UTC
        ),
        cti_ingest_syn.serve(
            name="cti-ingest-syn",
            cron="30 5 * * *",  # daily 05:30 UTC
        ),
        cti_scan_pending.serve(
            name="cti-scan-pending",
            interval=900,  # every 15 minutes
        ),
        cti_trigger.serve(
            name="cti-trigger",
            cron="0 3 * * 1",  # Monday 03:00 UTC
        ),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_serve())
