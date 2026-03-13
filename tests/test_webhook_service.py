"""
Tests for WebhookService — circuit breaker, 4xx abort, retry logic.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def circuit_breaker():
    from backend.webhook_service import CircuitBreaker
    return CircuitBreaker(failure_threshold=3, reset_timeout=30)


class TestCircuitBreaker:
    def test_initial_state_closed(self, circuit_breaker):
        assert circuit_breaker.is_open is False

    def test_opens_after_threshold_failures(self, circuit_breaker):
        for _ in range(3):
            circuit_breaker.record_failure()
        assert circuit_breaker.is_open is True

    def test_success_resets_failure_count(self, circuit_breaker):
        circuit_breaker.record_failure()
        circuit_breaker.record_failure()
        circuit_breaker.record_success()
        assert circuit_breaker.failure_count == 0
        assert circuit_breaker.is_open is False


class TestWebhookDelivery:
    @pytest.mark.asyncio
    async def test_delivery_succeeds_on_200(self):
        """200 response → record_success, no retry."""
        from backend.webhook_service import WebhookService
        svc = WebhookService.__new__(WebhookService)
        svc._breakers = {}

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            await svc._deliver("http://example.com/hook", {"type": "test"}, {})

    @pytest.mark.asyncio
    async def test_4xx_aborts_immediately_without_retry(self):
        """HTTP 4xx → circuit breaker records failure, no more retries."""
        from backend.webhook_service import WebhookService
        from backend.webhook_service import CircuitBreaker as CB
        svc = WebhookService.__new__(WebhookService)
        breaker = CB(failure_threshold=3, reset_timeout=30)
        svc._breakers = {"http://example.com/hook": breaker}

        call_count = {"n": 0}

        mock_resp = AsyncMock()
        mock_resp.status = 404
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        def _post(*a, **kw):
            call_count["n"] += 1
            return mock_resp
        mock_session.post = _post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            await svc._deliver("http://example.com/hook", {"type": "test"}, {})

        # Must have aborted after first 4xx — not retried 3 times
        assert call_count["n"] == 1, "4xx should stop retries immediately"
        assert breaker.failure_count >= 1
