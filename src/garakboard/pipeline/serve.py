"""Pipeline serve entrypoint.

Runs both Prefect flows on schedule without requiring a Prefect Server.
Used as the CMD in Dockerfile.pipeline for local Docker Compose.

For production (Prefect Server + Worker), use prefect.yaml + `prefect deploy --all`
and run the worker with: prefect worker start --pool garakboard-process-pool
"""

import asyncio
import logging

from garakboard.pipeline.flows import discover_and_queue_scans, scan_pending_runs

logger = logging.getLogger(__name__)


async def _serve():
    await asyncio.gather(
        scan_pending_runs.serve(
            name="scan-pending-runs",
            interval=120,  # every 2 minutes
        ),
        discover_and_queue_scans.serve(
            name="discover-and-queue-scans",
            cron="0 2 * * 1",  # Monday 02:00 UTC
        ),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_serve())
