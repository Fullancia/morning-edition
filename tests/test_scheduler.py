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
