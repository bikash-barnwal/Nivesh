from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from nivesh.jobs.pipeline import run_eod_pipeline

IST = "Asia/Kolkata"


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=IST)
    scheduler.add_job(
        run_eod_pipeline,
        CronTrigger(day_of_week="mon-fri", hour=18, minute=45, timezone=IST),
        id="eod_pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    return scheduler
