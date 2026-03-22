"""
Precision Timer - Zero Latency Scheduler
==========================================
Ensures all time-critical events fire at the EXACT second:
  - Candle close confirmation at XX:00:00, XX:15:00, XX:30:00, XX:45:00
  - Result check at entry_time + 15:01 (15 minutes + 1 second)
  - 120-second stability countdown

Uses asyncio.sleep with sub-second precision and NTP-aware timing.
All display times are in UTC+3.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

import config

logger = logging.getLogger(__name__)

# UTC+3 timezone object
UTC3 = timezone(timedelta(hours=config.UTC_OFFSET))


def now_utc() -> datetime:
    """Get current UTC time."""
    return datetime.now(timezone.utc)


def now_utc3() -> datetime:
    """Get current time in UTC+3."""
    return datetime.now(UTC3)


def utc_to_utc3(dt: datetime) -> datetime:
    """Convert a UTC datetime to UTC+3."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(UTC3)


def utc_to_utc3_str(dt: datetime) -> str:
    """Convert UTC datetime to UTC+3 HH:MM string."""
    return utc_to_utc3(dt).strftime("%H:%M")


def utc_time_str_to_utc3(utc_time_str: str) -> str:
    """Convert a UTC HH:MM string to UTC+3 HH:MM string."""
    try:
        h, m = map(int, utc_time_str.split(":"))
        dt = datetime.now(timezone.utc).replace(hour=h, minute=m, second=0, microsecond=0)
        return utc_to_utc3(dt).strftime("%H:%M")
    except Exception:
        return utc_time_str


def next_candle_boundary() -> datetime:
    """
    Calculate the next 15-minute candle boundary in UTC.
    Boundaries: :00:00, :15:00, :30:00, :45:00
    """
    now = now_utc()
    interval = config.CANDLE_INTERVAL
    current_boundary = (now.minute // interval) * interval
    next_min = current_boundary + interval

    if next_min >= 60:
        boundary = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        boundary = now.replace(minute=next_min, second=0, microsecond=0)

    return boundary


def seconds_until(target: datetime) -> float:
    """Calculate precise seconds until a target datetime."""
    delta = (target - now_utc()).total_seconds()
    return max(0.0, delta)


def seconds_until_candle_close() -> float:
    """Seconds until the next 15-min candle boundary."""
    return seconds_until(next_candle_boundary())


class PrecisionTimer:
    """
    Manages precision-timed events for the trading pipeline.
    All callbacks are async functions.
    """

    def __init__(self):
        self._tasks: dict = {}  # task_id -> asyncio.Task
        self._running = True

    async def schedule_at(self, target_dt: datetime, callback, *args,
                          task_id: str = None, label: str = ""):
        """
        Schedule a callback to fire at an exact datetime (UTC).
        Uses busy-wait for the last 100ms to ensure sub-second precision.
        """
        delay = seconds_until(target_dt)
        if delay <= 0:
            logger.warning(f"PrecisionTimer: target already passed for {label}")
            await callback(*args)
            return

        task_id = task_id or f"task_{time.time()}"

        async def _precise_wait_and_fire():
            try:
                # Sleep most of the time
                if delay > 0.5:
                    await asyncio.sleep(delay - 0.1)

                # Busy-wait for the last 100ms for precision
                while now_utc() < target_dt:
                    await asyncio.sleep(0.01)  # 10ms precision

                # Fire!
                target_utc3 = utc_to_utc3(target_dt).strftime("%H:%M:%S")
                actual_utc3 = now_utc3().strftime("%H:%M:%S")
                logger.info(f"PrecisionTimer FIRE [{label}]: target={target_utc3} actual={actual_utc3}")

                await callback(*args)

            except asyncio.CancelledError:
                logger.info(f"PrecisionTimer: task {task_id} cancelled")
            except Exception as e:
                logger.error(f"PrecisionTimer error [{label}]: {e}")
            finally:
                self._tasks.pop(task_id, None)

        task = asyncio.ensure_future(_precise_wait_and_fire())
        self._tasks[task_id] = task
        target_str = utc_to_utc3(target_dt).strftime("%H:%M:%S")
        logger.info(f"PrecisionTimer: scheduled [{label}] at {target_str} (UTC+3) in {delay:.1f}s")

    async def schedule_after(self, delay_seconds: float, callback, *args,
                              task_id: str = None, label: str = ""):
        """Schedule a callback after a delay in seconds."""
        target = now_utc() + timedelta(seconds=delay_seconds)
        await self.schedule_at(target, callback, *args, task_id=task_id, label=label)

    async def schedule_stability_check(self, symbol: str, callback, *args):
        """
        Schedule the 120-second stability check.
        Fires exactly 120 seconds after the signal was detected.
        """
        await self.schedule_after(
            config.STABILITY_WINDOW_SECONDS,
            callback, *args,
            task_id=f"stability_{symbol}",
            label=f"120s Stability Check - {symbol}",
        )

    async def schedule_candle_close_confirmation(self, symbol: str, callback, *args):
        """
        Schedule confirmation at the exact next candle close (XX:00:00, XX:15:00, etc.).
        """
        target = next_candle_boundary()
        await self.schedule_at(
            target, callback, *args,
            task_id=f"confirm_{symbol}",
            label=f"Candle Close Confirm - {symbol}",
        )

    async def schedule_result_check(self, symbol: str, expiry_dt: datetime, callback, *args):
        """
        Schedule result check at expiry_time + 1 second (for price settle).
        """
        target = expiry_dt + timedelta(seconds=config.RESULT_CHECK_DELAY_SECONDS)
        await self.schedule_at(
            target, callback, *args,
            task_id=f"result_{symbol}",
            label=f"Result Check - {symbol}",
        )

    def cancel(self, task_id: str):
        """Cancel a scheduled task."""
        task = self._tasks.pop(task_id, None)
        if task and not task.done():
            task.cancel()
            logger.info(f"PrecisionTimer: cancelled {task_id}")

    def cancel_all_for_symbol(self, symbol: str):
        """Cancel all tasks for a symbol."""
        to_cancel = [k for k in self._tasks if symbol in k]
        for k in to_cancel:
            self.cancel(k)

    def shutdown(self):
        """Cancel all pending tasks."""
        self._running = False
        for task_id in list(self._tasks.keys()):
            self.cancel(task_id)
