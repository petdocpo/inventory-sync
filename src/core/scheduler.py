"""
QR 재고 비교 작업을 30분 주기로 실행하는 스케줄러 모듈.
APScheduler를 사용해 주기적으로 comparator를 실행한다.
"""
from typing import Any, Dict
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger


class Scheduler:
    """
    `main.py`에서 `Scheduler(comparator, notifier)` 형태로 사용되는 클래스.
    `start()` 메서드가 호출되면 백그라운드 스케줄러가 시작된다.
    """
    def __init__(self, comparator, notifier):
        self.comparator = comparator
        self.notifier = notifier
        self._scheduler = BackgroundScheduler()

    def _job(self) -> None:
        """스케줄러가 실행할 작업: 비교 후 알림 전송."""
        try:
            result = self.comparator.compare()
            self.notifier.notify_discrepancy(result)
        except Exception as e:
            # 실제 서비스에서는 로깅을 추가한다.
            print(f"Scheduler job failed: {e}")

    def start(self) -> None:
        """30분 간격으로 `_job`을 실행하도록 스케줄러를 시작한다."""
        trigger = IntervalTrigger(minutes=30, start_date="2023-01-01 00:00:00")
        self._scheduler.add_job(self._job, trigger)
        self._scheduler.start()