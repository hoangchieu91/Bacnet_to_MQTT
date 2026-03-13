"""
FastAPI integration tests for main.py API endpoints.

Strategy: import `app` from backend.main with ALL external services
(BACnet, MQTT, gateway_engine, history_store) replaced by mocks so
tests run without real hardware.
"""
import sys
import types
import json
import pytest

# ── Pre-stub heavy imports that would fail without hardware ──────
for mod in [
    "BAC0", "BAC0.core", "BAC0.core.app", "BAC0.core.app.asyncApp",
    "paho", "paho.mqtt", "paho.mqtt.client",
]:
    if mod not in sys.modules:
        stub = types.ModuleType(mod)
        sys.modules[mod] = stub

# Provide a minimal BAC0.lite stub so main.py imports don't crash
bac0_stub = sys.modules["BAC0"]
bac0_stub.lite = None

from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────
def _make_mock_cm(tmp_path=None):
    """Minimal ConfigManager mock."""
    cm = MagicMock()
    cm.config.mqtt.broker_host = "localhost"
    cm.config.mqtt.broker_port = 1883
    cm.config.bacnet.ip = "0.0.0.0"
    cm.config.bacnet.port = 47808
    cm.config.users = []
    cm.config.schedules = []
    cm.config.anomaly_rules = []
    cm.config.webhooks = []
    cm.mappings = []
    cm.charts = []
    cm.groups = []
    cm.schedules = []
    return cm


def _make_mock_history():
    h = MagicMock()
    h._conn = MagicMock()
    h.query_events.return_value = []
    h.query.return_value = []
    h.get_stats_with_retention.return_value = {
        "total_records": 0, "db_size_mb": 0.1, "event_count": 0,
        "retention_days": 30, "event_retention_days": 180,
    }
    h.get_config.return_value = {
        "max_records_per_point": 10000, "max_db_size_mb": 500,
        "retention_days": 30, "event_retention_days": 180,
    }
    return h


# ─────────────────────────────────────────────────────────────────
# Fixture: test client with all services mocked
# ─────────────────────────────────────────────────────────────────
@pytest.fixture
def client(tmp_path):
    """Create a FastAPI TestClient with all services mocked out."""
    mock_cm = _make_mock_cm(tmp_path)
    mock_history = _make_mock_history()
    mock_bacnet = MagicMock()
    mock_bacnet.connected = False
    mock_bacnet._devices = {}
    mock_mqtt = MagicMock()
    mock_mqtt.connected = False
    mock_gateway = MagicMock()
    mock_gateway.status = "stopped"
    mock_gateway.uptime = 0
    mock_anomaly = MagicMock()
    mock_anomaly.get_rules.return_value = []
    mock_anomaly._states = {}

    patches = [
        patch("backend.main.config_manager", mock_cm),
        patch("backend.main.history_store", mock_history),
        patch("backend.main.bacnet_service", mock_bacnet),
        patch("backend.main.mqtt_service", mock_mqtt),
        patch("backend.main.gateway_engine", mock_gateway),
        patch("backend.main.anomaly_engine", mock_anomaly),
        patch("backend.main.scheduler_service", MagicMock()),
        patch("backend.main.webhook_service", MagicMock()),
        # Skip lifespan startup so TestClient doesn't try to connect anything
        patch("backend.main.lifespan", new_callable=lambda: lambda _: (
            __import__("contextlib").asynccontextmanager(
                lambda app: (x for x in [None])  # no-op lifespan
            )
        )),
    ]

    # Apply patches and import fresh app
    with __import__("contextlib").ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        from backend.main import app
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


# ─────────────────────────────────────────────────────────────────
# /api/status
# ─────────────────────────────────────────────────────────────────
class TestStatusEndpoint:
    def test_status_200(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200

    def test_status_has_gateway_field(self, client):
        data = client.get("/api/status").json()
        assert "gateway" in data

    def test_status_has_bacnet_connected(self, client):
        data = client.get("/api/status").json()
        assert "bacnet_connected" in data
        assert data["bacnet_connected"] is False

    def test_status_has_mqtt_connected(self, client):
        data = client.get("/api/status").json()
        assert "mqtt_connected" in data


# ─────────────────────────────────────────────────────────────────
# /api/health
# ─────────────────────────────────────────────────────────────────
class TestHealthEndpoint:
    def test_health_200(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────
# /api/anomaly/rules
# ─────────────────────────────────────────────────────────────────
class TestAnomalyRulesEndpoints:
    def test_get_rules_returns_list(self, client):
        resp = client.get("/api/anomaly/rules")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_active_anomalies(self, client):
        resp = client.get("/api/anomaly/active")
        assert resp.status_code == 200

    def test_post_rule_valid(self, client):
        from backend.anomaly_engine import AnomalyRule
        new_rule = AnomalyRule(
            id="r1", name="High Temp", trigger_mapping_id="m1",
            trigger_condition="gt:30", severity="critical",
        )
        with patch("backend.main.anomaly_engine") as mock_ae:
            mock_ae.add_rule.return_value = new_rule
            resp = client.post("/api/anomaly/rules", json={
                "name": "High Temp",
                "trigger_mapping_id": "m1",
                "trigger_condition": "gt:30",
            })
        assert resp.status_code in (200, 201)

    def test_delete_nonexistent_rule_returns_404(self, client):
        with patch("backend.main.anomaly_engine") as mock_ae:
            mock_ae.delete_rule.return_value = False
            resp = client.delete("/api/anomaly/rules/nonexistent-id")
        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────
# /api/events
# ─────────────────────────────────────────────────────────────────
class TestEventsEndpoints:
    def test_get_events_returns_list(self, client):
        resp = client.get("/api/events")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("events", data), list)

    def test_get_events_with_type_filter(self, client):
        resp = client.get("/api/events?event_type=alarm")
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────
# /api/config
# ─────────────────────────────────────────────────────────────────
class TestConfigEndpoints:
    def test_get_mappings_returns_list(self, client):
        resp = client.get("/api/config/mappings")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_charts_returns_list(self, client):
        resp = client.get("/api/config/charts")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_bacnet_config(self, client):
        resp = client.get("/api/bacnet/config")
        assert resp.status_code == 200

    def test_get_mqtt_config(self, client):
        resp = client.get("/api/mqtt/config")
        assert resp.status_code == 200
