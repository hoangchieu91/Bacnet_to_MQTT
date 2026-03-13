"""Webhook delivery service — sends HTTP POST on BACnet alarm events.

Features:
  - Async delivery (never blocks polling loop)
  - Retry: up to 3 attempts with exponential backoff (5s → 15s → 30s)
  - Per-URL circuit breaker: suspends URL for 10 min after 5 consecutive failures
  - Severity filter: each webhook can opt-in to specific severity levels
  - Optional X-Webhook-Secret header for simple authentication
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_RETRY_DELAYS = [5, 15, 30]  # seconds between retries
_REQUEST_TIMEOUT = 5          # seconds per HTTP request
_CB_FAIL_THRESHOLD = 5        # consecutive failures before circuit opens
_CB_RESET_SECS = 600          # 10 minutes circuit-open duration


class _CircuitBreaker:
    """Per-URL failure tracker."""

    def __init__(self) -> None:
        self.fail_count = 0
        self.tripped_at: float | None = None  # epoch when circuit opened

    def record_success(self) -> None:
        self.fail_count = 0
        self.tripped_at = None

    def record_failure(self) -> None:
        self.fail_count += 1
        if self.fail_count >= _CB_FAIL_THRESHOLD:
            if self.tripped_at is None:
                self.tripped_at = time.monotonic()
                logger.warning("Webhook circuit breaker OPEN — suspending for %ds", _CB_RESET_SECS)

    def is_open(self) -> bool:
        if self.tripped_at is None:
            return False
        elapsed = time.monotonic() - self.tripped_at
        if elapsed >= _CB_RESET_SECS:
            # Auto-reset after cooldown
            self.tripped_at = None
            self.fail_count = 0
            logger.info("Webhook circuit breaker CLOSED — resuming delivery")
            return False
        return True


class WebhookService:
    """Delivers alarm events to configured webhook URLs."""

    def __init__(self, config_manager) -> None:
        self._cm = config_manager
        self._breakers: dict[str, _CircuitBreaker] = {}  # url → breaker

    def _get_breaker(self, url: str) -> _CircuitBreaker:
        if url not in self._breakers:
            self._breakers[url] = _CircuitBreaker()
        return self._breakers[url]

    async def fire(
        self,
        event_type: str,
        severity: str,
        mapping_id: str,
        label: str,
        device_id: int,
        object_type: str,
        object_instance: int,
        value: Any,
        alarm_state: str,
        message: str,
    ) -> None:
        """Queue webhook delivery — non-blocking, called from gateway engine."""
        webhooks = getattr(self._cm.config, "webhooks", [])
        if not webhooks:
            return

        payload = {
            "event": event_type,
            "severity": severity,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "gateway_id": getattr(self._cm.config.bacnet, "device_id", "unknown"),
            "point": {
                "id": mapping_id,
                "label": label,
                "device_id": device_id,
                "object_type": object_type,
                "object_instance": object_instance,
            },
            "value": str(value),
            "alarm_state": alarm_state,
            "message": message,
        }

        for wh in webhooks:
            if not wh.enabled:
                continue
            if severity not in (wh.severity_filter or ["warning", "critical"]):
                continue
            asyncio.create_task(self._deliver(wh, payload))

    async def _deliver(self, wh, payload: dict) -> None:
        """Attempt delivery with retry + circuit breaker."""
        import aiohttp  # Lazy import — optional dependency

        url = wh.url
        breaker = self._get_breaker(url)

        if breaker.is_open():
            logger.debug("Webhook circuit open, skipping %s", url)
            return

        headers = {"Content-Type": "application/json"}
        if wh.secret_header:
            headers["X-Webhook-Secret"] = wh.secret_header

        for attempt, delay in enumerate([0] + _RETRY_DELAYS, start=1):
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, json=payload, headers=headers) as resp:
                        if resp.status < 300:
                            breaker.record_success()
                            logger.info(
                                "Webhook delivered [%s] attempt=%d status=%d",
                                url, attempt, resp.status,
                            )
                            return
                        else:
                            logger.warning(
                                "Webhook [%s] attempt=%d HTTP %d",
                                url, attempt, resp.status,
                            )
                            # 4xx client errors → no point retrying, record failure immediately
                            if 400 <= resp.status < 500:
                                breaker.record_failure()
                                logger.error("Webhook [%s] client error %d — aborting retries", url, resp.status)
                                return
            except Exception as exc:
                logger.warning("Webhook [%s] attempt=%d error: %s", url, attempt, exc)

        # All retries exhausted → record failure
        breaker.record_failure()
        logger.error("Webhook [%s] failed after %d attempts", url, len(_RETRY_DELAYS) + 1)

    async def send_test(self, wh) -> dict:
        """Send a test payload and return result synchronously (for API /test endpoint)."""
        try:
            import aiohttp
        except ImportError:
            return {"ok": False, "error": "aiohttp not installed. Run: pip install aiohttp"}

        payload = {
            "event": "test",
            "severity": "info",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": "This is a test webhook from BACnet Gateway V2",
            "point": {"label": "TEST_POINT", "device_id": 0, "object_type": "analogValue", "object_instance": 0},
            "value": "42",
            "alarm_state": "normal",
        }
        headers = {"Content-Type": "application/json"}
        if wh.secret_header:
            headers["X-Webhook-Secret"] = wh.secret_header

        start = time.monotonic()
        try:
            timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(wh.url, json=payload, headers=headers) as resp:
                    elapsed_ms = int((time.monotonic() - start) * 1000)
                    return {"ok": resp.status < 300, "status": resp.status, "elapsed_ms": elapsed_ms}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "elapsed_ms": int((time.monotonic() - start) * 1000)}
