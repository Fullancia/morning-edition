from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger

from .settings import Settings

JOB_ID = "daily-edition"
UPDATE_JOB_ID = "nightly-update"


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


def schedule_update(sched: BaseScheduler, s: Settings, job: Callable[[], None]) -> None:
    """The overnight update runs at update_time in the paper's timezone while auto_update is on."""
    if not s.auto_update:
        try:
            sched.remove_job(UPDATE_JOB_ID)
        except JobLookupError:
            pass
        return
    hour, minute = map(int, s.update_time.split(":"))
    sched.add_job(job, CronTrigger(hour=hour, minute=minute, timezone=ZoneInfo(s.timezone)),
                  id=UPDATE_JOB_ID, replace_existing=True, max_instances=1, coalesce=True,
                  misfire_grace_time=3600)


def build_is_imminent(s: Settings, now: datetime, minutes: int = 60) -> bool:
    """True when the next edition build starts within `minutes` of `now`."""
    tz = ZoneInfo(s.timezone)
    local = now.astimezone(tz)
    hour, minute = build_time(s)
    upcoming = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if upcoming < local:
        upcoming += timedelta(days=1)
    return upcoming - local <= timedelta(minutes=minutes)
