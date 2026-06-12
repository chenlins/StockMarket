from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger


def create_scheduler(run_func, run_time: str = "16:30"):
    """创建每日定时选股任务。run_time 格式 HH:MM。"""
    hour, minute = run_time.split(":")
    scheduler = BlockingScheduler()

    scheduler.add_job(
        run_func,
        CronTrigger(hour=int(hour), minute=int(minute), day_of_week="mon-fri"),
        id="daily_screening",
        name="每日选股",
    )
    return scheduler
