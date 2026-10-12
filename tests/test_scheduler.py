from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from morning_edition import scheduler
from morning_edition.settings import Settings


def test_build_time_subtracts_lead_and_wraps():
    assert scheduler.build_time(Settings(print_time="05:30", build_lead_minutes=20)) == (5, 10)
    assert scheduler.build_time(Settings(print_time="00:10", build_lead_minutes=20)) == (23, 50)


def test_schedule_replaces_job_and_reports_next_run():
    sched = BackgroundScheduler()
    sched.start(paused=True)
    try:
        s = Settings(print_time="05:30", timezone="America/New_York")
        scheduler.schedule(sched, s, lambda: None)
        scheduler.schedule(sched, s.model_copy(update={"print_time": "06:00"}), lambda: None)
        assert len(sched.get_jobs()) == 1
        nxt = scheduler.next_run(sched)
        assert (nxt.hour, nxt.minute) == (5, 40)
        assert str(nxt.tzinfo) == "America/New_York"
    finally:
        sched.shutdown(wait=False)


def test_next_run_without_job():
    sched = BackgroundScheduler()
    sched.start(paused=True)
    try:
        assert scheduler.next_run(sched) is None
    finally:
        sched.shutdown(wait=False)


def started():
    sched = BackgroundScheduler()
    sched.start(paused=True)
    return sched


def test_nightly_update_job_is_added_at_update_time_in_the_papers_timezone():
    sched = started()
    try:
        s = Settings(timezone="America/New_York", auto_update=True, update_time="02:45")
        scheduler.schedule_update(sched, s, lambda: None)
        job = sched.get_job(scheduler.UPDATE_JOB_ID)
        assert job is not None and job.max_instances == 1
        assert (job.next_run_time.hour, job.next_run_time.minute) == (2, 45)
        assert str(job.next_run_time.tzinfo) == "America/New_York"
    finally:
        sched.shutdown(wait=False)


def test_nightly_update_job_is_rescheduled_and_removed_with_the_setting():
    sched = started()
    try:
        on = Settings(auto_update=True, update_time="02:45")
        scheduler.schedule_update(sched, on, lambda: None)
        scheduler.schedule_update(sched, on.model_copy(update={"update_time": "03:10"}), lambda: None)
        assert len(sched.get_jobs()) == 1
        assert sched.get_job(scheduler.UPDATE_JOB_ID).next_run_time.minute == 10
        scheduler.schedule_update(sched, on.model_copy(update={"auto_update": False}), lambda: None)
        assert sched.get_job(scheduler.UPDATE_JOB_ID) is None
        scheduler.schedule_update(sched, on.model_copy(update={"auto_update": False}), lambda: None)  # nothing to remove
    finally:
        sched.shutdown(wait=False)


def test_the_edition_job_and_the_update_job_are_independent():
    sched = started()
    try:
        s = Settings(auto_update=True)
        scheduler.schedule(sched, s, lambda: None)
        scheduler.schedule_update(sched, s, lambda: None)
        assert {j.id for j in sched.get_jobs()} == {scheduler.JOB_ID, scheduler.UPDATE_JOB_ID}
        scheduler.schedule_update(sched, s.model_copy(update={"auto_update": False}), lambda: None)
        assert {j.id for j in sched.get_jobs()} == {scheduler.JOB_ID}
    finally:
        sched.shutdown(wait=False)


def test_build_is_imminent_looks_an_hour_ahead_in_the_papers_timezone():
    from datetime import UTC, datetime
    s = Settings(timezone="America/New_York", print_time="05:30", build_lead_minutes=20)   # builds 05:10 local
    at = lambda h, m: datetime(2026, 10, 12, h, m, tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)
    assert scheduler.build_is_imminent(s, at(4, 10)) is True      # exactly 60 minutes ahead
    assert scheduler.build_is_imminent(s, at(5, 10)) is True      # right now
    assert scheduler.build_is_imminent(s, at(4, 9)) is False
    assert scheduler.build_is_imminent(s, at(5, 11)) is False     # next build is tomorrow
    assert scheduler.build_is_imminent(s, at(2, 30)) is False


def test_build_is_imminent_wraps_past_midnight():
    from datetime import UTC, datetime
    s = Settings(timezone="UTC", print_time="00:10", build_lead_minutes=20)   # builds 23:50
    assert scheduler.build_is_imminent(s, datetime(2026, 10, 12, 23, 0, tzinfo=UTC)) is True
    assert scheduler.build_is_imminent(s, datetime(2026, 10, 12, 22, 0, tzinfo=UTC)) is False
