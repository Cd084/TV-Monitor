"""Background thread that triggers scans on the configured schedule."""
import logging
import threading
import time
from datetime import datetime, timedelta

from .scanner import run_scan

log = logging.getLogger(__name__)


def parse_time(value):
    try:
        hh, mm = value.strip().split(":")[:2]
        return min(max(int(hh), 0), 23), min(max(int(mm), 0), 59)
    except (AttributeError, ValueError):
        return 3, 0


class Scheduler:
    def __init__(self, db):
        self.db = db
        self.next_run = None
        self._wake = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, name="scheduler", daemon=True).start()

    def reschedule(self):
        """Call after the schedule settings change."""
        self._wake.set()

    def compute_next(self, now=None):
        settings = self.db.get_settings()
        now = now or datetime.now()
        mode = settings["schedule_mode"]
        if mode == "daily":
            hh, mm = parse_time(settings["schedule_time"])
            candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            return candidate if candidate > now else candidate + timedelta(days=1)
        if mode == "interval":
            try:
                hours = max(1.0, float(settings["schedule_interval_hours"]))
            except ValueError:
                hours = 24.0
            last = self.db.last_run()
            if not last:
                return now + timedelta(minutes=1)
            return max(datetime.fromisoformat(last["started_at"]) + timedelta(hours=hours), now)
        return None

    def _loop(self):
        target = None
        while True:
            try:
                if target is None:
                    target = self.compute_next()
                self.next_run = target
                now = datetime.now()
                if target is not None and now >= target:
                    target = None
                    log.info("Starting scheduled scan")
                    run_scan(self.db, "scheduled")
                    continue
                timeout = 60.0 if target is None else min(60.0, (target - now).total_seconds())
                if self._wake.wait(max(timeout, 1.0)):
                    self._wake.clear()
                    target = None
            except Exception:
                log.exception("Scheduler error")
                target = None
                time.sleep(60)
