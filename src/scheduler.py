"""Scheduler  -- nightly dream-review cron (PRD Sec. 5.4, Task 16.3).

Uses APScheduler to hit the dream-review consolidation on a cron schedule.
The endpoint POST /scheduler/dream-review (in dashboard.py) is the manual
trigger; this module wires the automatic trigger at startup.

Disabled by default in tests/dev  -- enable with VENTUREBOT_ENABLE_SCHEDULER=1.
"""
from __future__ import annotations

import logging

from . import config

logger = logging.getLogger(__name__)

_scheduler = None


def start_scheduler() -> bool:
    """Start the APScheduler with the nightly dream-review job.

    Returns True if the scheduler was started, False if disabled (env not set).
    """
    global _scheduler
    if not config.ENABLE_SCHEDULER:
        logger.info("scheduler disabled (set enable_scheduler:true in config.json or VENTUREBOT_ENABLE_SCHEDULER=1)")
        return False

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    from .memory.dream_review import run_dream_review

    hour = config.DREAM_REVIEW_HOUR

    def _job() -> None:
        try:
            summary = run_dream_review()
            logger.info("dream_review complete: %s", summary)
        except Exception:
            logger.exception("dream_review job failed")

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(_job, CronTrigger(hour=hour, minute=0), id="dream-review")
    _scheduler.start()
    logger.info("scheduler started (dream-review daily at %02d:00 UTC)", hour)
    return True


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
