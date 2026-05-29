"""Pipeline serve entrypoint.

Runs both Prefect flows on schedule without requiring a Prefect Server.
Used as the CMD in Dockerfile.pipeline for local Docker Compose.

For production (Prefect Server + Worker), use prefect.yaml + `prefect deploy --all`
and run the worker with: prefect worker start --pool glokta-process-pool
"""

import asyncio
import logging

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
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_serve())
