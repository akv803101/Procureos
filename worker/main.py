"""Background worker entrypoint — APScheduler, run as a SEPARATE process.

Fix 09 (binding): the scheduler must NEVER run inside the FastAPI/Gunicorn
workers. It runs here, in its own container (Dockerfile.worker), started with:

    python -m worker.main

Registers (Fix 09 config: coalesce + max_instances=1): delivery_tracker (30min).
Still to register as they land: rfq_timeout (5min), gst_reverifier (daily 2am),
score_updater reconcile. The Supabase-backed job store is wired with the data
layer; until then the default in-memory store is used.
"""
import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from worker.delivery_tracker import poll_deliveries

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker")


def build_scheduler() -> AsyncIOScheduler:
    """Construct the scheduler with Fix 09 defaults.

    coalesce=True     -> if the process was down and missed several runs of a
                         job, collapse them into a single catch-up run.
    max_instances=1   -> never let a job overlap a still-running instance of
                         itself (prevents double-processing the same goal).
    """
    return AsyncIOScheduler(job_defaults={"coalesce": True, "max_instances": 1})


async def main() -> None:
    scheduler = build_scheduler()
    # Delivery tracking — poll in_transit orders every 30 min (PRD Section 5).
    scheduler.add_job(poll_deliveries, "interval", minutes=30, id="delivery_tracker")
    # Still to register as they land: rfq_timeout (5min), gst_reverifier (daily),
    # score_updater reconcile.
    scheduler.start()
    log.info("Worker started (Fix 09: separate process). Registered: delivery_tracker (30min).")
    try:
        # Keep the process alive so the scheduler's event loop keeps running.
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        log.info("Worker shutting down.")
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
