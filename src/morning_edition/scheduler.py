from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger

from .settings import Settings

JOB_ID = "daily-edition"


def build_time(s: Settings) -> tuple[int, int]:
    hour, minute = map(int, s.print_time.split(":"))
    total = (hour * 60 + minute - s.build_lead_minutes) % (24 * 60)
    return divmod(total, 60)


def schedule(sched: BaseScheduler, s: Settings, job: Callable[[], None]) -> None:
    hour, minute = build_time(s)
    sched.add_job(job, CronTrigger(hour=hour, minute=minute, timezone=ZoneInfo(s.timezone)),
                  id=JOB_ID, replace_existing=True, max_instances=1, coalesce=True,
                  misfire_grace_time=3600)


def next_run(sched: BaseScheduler) -> datetime | None:
    job = sched.get_job(JOB_ID)
    return getattr(job, "next_run_time", None) if job else None
