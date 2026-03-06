"""Scheduler service — executes timed BACnet write operations.

Format for `cron` field in ScheduleEntry:
  "HH:MM"           — run every day at HH:MM (local time)
  "HH:MM|1,2,3,4,5" — run on specific days (0=Mon, 6=Sun)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class SchedulerService:
    """Checks schedules every 60 seconds and writes values when matched."""

    def __init__(self, config_manager, bacnet_service, history_store=None):
        self._cm = config_manager
        self._bacnet = bacnet_service
        self._history = history_store
        self._task: asyncio.Task | None = None
        self._executed_today: set[str] = set()  # Track which schedules ran today
        self._last_date: str = ""

    def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("Scheduler service started.")

    def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
            logger.info("Scheduler service stopped.")

    async def _loop(self):
        """Main scheduler loop — checks every 30 seconds."""
        try:
            while True:
                await self._tick()
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Scheduler loop error: %s", e)

    async def _tick(self):
        """Check all enabled schedules against current time."""
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        # Reset executed set at midnight
        if today_str != self._last_date:
            self._executed_today.clear()
            self._last_date = today_str

        current_time = now.strftime("%H:%M")
        current_day = now.weekday()  # 0=Monday, 6=Sunday

        schedules = getattr(self._cm.config, 'schedules', [])
        if not schedules:
            return

        for sched in schedules:
            if not sched.enabled or not sched.cron:
                continue

            # Skip if already executed today
            exec_key = f"{sched.id}:{today_str}"
            if exec_key in self._executed_today:
                continue

            # Parse cron: "HH:MM" or "HH:MM|0,1,2,3,4"
            parts = sched.cron.split("|")
            sched_time = parts[0].strip()

            # Check day filter
            if len(parts) > 1:
                allowed_days = [int(d.strip()) for d in parts[1].split(",") if d.strip().isdigit()]
                if current_day not in allowed_days:
                    continue

            # Check time match (within 1-minute window)
            if current_time == sched_time:
                await self._execute_schedule(sched)
                self._executed_today.add(exec_key)

    async def _execute_schedule(self, sched):
        """Write a value to a BACnet object according to the schedule."""
        try:
            address = self._bacnet.get_device_address(sched.device_id)
            if not address:
                logger.warning("Schedule '%s': device %d not found", sched.name, sched.device_id)
                if self._history:
                    self._history.log_event(
                        "schedule", f"Schedule '{sched.name}' failed: device {sched.device_id} not found",
                        device_id=sched.device_id, severity="warning",
                    )
                return

            await self._bacnet.write_object(
                address, sched.object_type, sched.object_instance,
                sched.value, sched.priority,
            )

            logger.info(
                "Schedule '%s' executed: wrote %s to %s:%d @ priority %d",
                sched.name, sched.value, sched.object_type, sched.object_instance, sched.priority,
            )

            if self._history:
                self._history.log_event(
                    "schedule",
                    f"Schedule '{sched.name}': wrote {sched.value} to {sched.object_type}:{sched.object_instance} on device {sched.device_id}",
                    device_id=sched.device_id, severity="info",
                    data={"schedule_id": sched.id, "value": str(sched.value), "priority": sched.priority},
                )

        except Exception as e:
            logger.error("Schedule '%s' error: %s", sched.name, e)
            if self._history:
                self._history.log_event(
                    "schedule", f"Schedule '{sched.name}' error: {e}",
                    device_id=sched.device_id, severity="warning",
                )
