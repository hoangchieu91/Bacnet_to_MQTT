"""
Shared pytest fixtures for BACnet-MQTT Gateway tests.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ─────────────────────────────────────────────────────────────────
# Fixtures: Config paths
# ─────────────────────────────────────────────────────────────────
@pytest.fixture
def default_config_data():
    """Minimal valid config dict."""
    return {
        "mqtt": {
            "broker_host": "localhost", "broker_port": 1883,
            "username": "", "password": "", "use_tls": False,
            "client_id": "test_gw", "topic_prefix": "bacnet", "qos": 1, "retain": False,
        },
        "bacnet": {
            "ip": "0.0.0.0", "port": 47808, "mask": "24",
            "device_id": 599, "default_poll_interval": 10,
        },
        "gateway": {"mappings": []},
        "web": {"host": "0.0.0.0", "port": 8080},
        "groups": [], "charts": [], "schedules": [],
        "anomaly_rules": [], "webhooks": [], "users": [],
    }


@pytest.fixture
def config_file(tmp_path, default_config_data):
    """Write a valid runtime_config.json to tmp dir."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "runtime_config.json"
    cfg_file.write_text(json.dumps(default_config_data))
    return cfg_file


@pytest.fixture
def default_config_file(tmp_path, default_config_data):
    """Place a default_config.json for fallback tests."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    dc = cfg_dir / "default_config.json"
    dc.write_text(json.dumps(default_config_data))
    return dc


# ─────────────────────────────────────────────────────────────────
# Mock BACnet service
# ─────────────────────────────────────────────────────────────────
@pytest.fixture
def mock_bacnet():
    svc = MagicMock()
    svc.connected = True
    svc.get_device_address = MagicMock(return_value="192.168.1.10:47808")
    svc.write_object = AsyncMock(return_value=(True, ""))
    svc._network = MagicMock()
    return svc


@pytest.fixture
def mock_history():
    h = MagicMock()
    h.log_event = MagicMock()
    h.record = MagicMock()
    return h


@pytest.fixture
def mock_mqtt():
    m = MagicMock()
    m.publish = MagicMock()
    return m
