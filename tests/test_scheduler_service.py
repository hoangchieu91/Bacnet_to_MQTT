"""
Tests for SchedulerService — cron parsing, BACnet guard, loop restart.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


@pytest.fixture
def scheduler(mock_bacnet, mock_history):
    from backend.scheduler_service import SchedulerService
    from backend.config_manager import ConfigManager

    cm = MagicMock()
    cm.schedules = []
    return SchedulerService(bacnet_service=mock_bacnet, config_manager=cm)


@pytest.fixture
def scheduler_no_bacnet(mock_history):
    """Scheduler with BACnet service that is NOT connected."""
    from backend.scheduler_service import SchedulerService
    cm = MagicMock()
    cm.schedules = []
    bac = MagicMock()
    bac.connected = False
    return SchedulerService(bacnet_service=bac, config_manager=cm)


class TestSchedulerBacnetGuard:
    @pytest.mark.asyncio
    async def test_skips_schedule_when_bacnet_disconnected(self, scheduler_no_bacnet):
        """If BACnet is not connected, _execute_schedule should skip and log."""
        sched = MagicMock()
        sched.id = "s1"
        sched.name = "Test Schedule"
        sched.device_id = 100
        sched.object_type = "analogOutput"
        sched.object_instance = 1
        sched.value = 22

        await scheduler_no_bacnet._execute_schedule(sched)

        # Should have recorded 'deferred' status, not tried to write
        status = scheduler_no_bacnet._last_run.get("s1", {})
        assert status.get("success") is False
        assert "deferred" in status.get("message", "").lower() or \
               "not connected" in status.get("message", "").lower()


class TestSchedulerCronParsing:
    @pytest.mark.parametrize("cron,now_time,expected", [
        # schedule at 08:00, current time 08:00 → should run
        ("08:00", "2026-03-13 08:00:30", True),
        # schedule at 08:00, current time 07:59 → should NOT run
        ("08:00", "2026-03-13 07:59:00", False),
        # schedule at 23:59, current time 23:59 → should run
        ("23:59", "2026-03-13 23:59:45", True),
        # cron with day filter: Monday (0), but today is Sunday (6) → skip
        ("10:00|0", "2026-03-13 10:00:00", False),  # 2026-03-13 is Friday (4)
        # cron with day filter: Friday (4), today is Friday → run
        ("10:00|4", "2026-03-13 10:00:00", True),
    ])
    @pytest.mark.asyncio
    async def test_tick_runs_schedule_at_correct_time(
        self, scheduler, cron, now_time, expected
    ):
        """_tick() should execute schedule only when time matches."""
        from backend.models import ScheduleEntry
        import uuid

        sched_id = str(uuid.uuid4())
        sched = MagicMock()
        sched.id = sched_id
        sched.enabled = True
        sched.cron = cron
        sched.name = "Test"
        sched.device_id = 100
        sched.object_type = "analogOutput"
        sched.object_instance = 1
        sched.value = 22

        scheduler._cm.config.schedules = []
        scheduler._cm.schedules = [sched]
        executed = {"ran": False}

        async def fake_execute(s):
            executed["ran"] = True

        scheduler._execute_schedule = fake_execute

        with patch("backend.scheduler_service.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.strptime(now_time, "%Y-%m-%d %H:%M:%S")
            # Patch _cm.config.schedules to return our sched
            scheduler._cm.config.schedules = [sched]
            await scheduler._tick()

        assert executed["ran"] == expected


class TestSchedulerLoop:
    @pytest.mark.asyncio
    async def test_loop_restarts_after_exception(self, scheduler):
        """Loop should continue after a single _tick() exception."""
        call_count = {"n": 0}

        async def bad_tick():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("Simulated tick error")
            # Stop after second call
            raise asyncio.CancelledError

        scheduler._tick = bad_tick

        # Patch sleep so 60s back-off doesn't actually wait
        with patch("asyncio.sleep", new_callable=AsyncMock):
            try:
                await asyncio.wait_for(scheduler._loop(), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        # Loop must have continued past the first error to call _tick again
        assert call_count["n"] >= 2, "Loop must retry after exception"
