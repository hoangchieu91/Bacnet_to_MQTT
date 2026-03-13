"""
Tests for MqttService — multi-callback topic routing, wildcard matching.

Note: paho-mqtt may not be installed in CI environments.
We mock the module at import time to allow test collection.
"""
import sys
import types
from unittest.mock import MagicMock, patch

# Stub out paho so the module can be imported without paho installed
paho_stub = types.ModuleType("paho")
paho_mqtt_stub = types.ModuleType("paho.mqtt")
paho_client_stub = types.ModuleType("paho.mqtt.client")
paho_client_stub.Client = MagicMock
paho_stub.mqtt = paho_mqtt_stub
paho_mqtt_stub.client = paho_client_stub
sys.modules.setdefault("paho", paho_stub)
sys.modules.setdefault("paho.mqtt", paho_mqtt_stub)
sys.modules.setdefault("paho.mqtt.client", paho_client_stub)

import pytest
from backend.mqtt_service import MqttService
from backend.models import MqttConfig


@pytest.fixture
def mqtt_cfg():
    return MqttConfig(
        broker_host="localhost", broker_port=1883,
        username="", password="", use_tls=False,
        client_id="test", topic_prefix="bacnet", qos=1, retain=False,
    )


@pytest.fixture
def service(mqtt_cfg):
    svc = MqttService(mqtt_cfg)
    svc._connected = True
    # MqttService stores paho Client internally; replace with mock
    svc._client = MagicMock()
    return svc


class TestMqttTopicRouting:
    def test_exact_topic_callback_registered(self, service):
        """After subscribe(), callback is stored in _topic_callbacks."""
        cb = MagicMock()
        service.subscribe("bacnet/zone/1/temp", callback=cb)
        assert "bacnet/zone/1/temp" in service._topic_callbacks
        assert service._topic_callbacks["bacnet/zone/1/temp"] is cb

    def test_exact_topic_callback_called_on_message(self, service):
        """Subscribing with exact topic routes to the correct callback."""
        cb = MagicMock()
        service.subscribe("bacnet/zone/1/temp", callback=cb)

        msg = MagicMock()
        msg.topic = "bacnet/zone/1/temp"
        msg.payload = b"22.5"
        service._on_message(None, None, msg)

        cb.assert_called_once()

    def test_different_topic_callback_not_called(self, service):
        """Callback for topic A must not fire for topic B."""
        cb_a = MagicMock()
        cb_b = MagicMock()
        service.subscribe("topic/a", callback=cb_a)
        service.subscribe("topic/b", callback=cb_b)

        msg = MagicMock()
        msg.topic = "topic/a"
        msg.payload = b"1"
        service._on_message(None, None, msg)

        cb_a.assert_called_once()
        cb_b.assert_not_called()

    def test_subscribe_overwrites_previous_callback(self, service):
        """Re-subscribing with same topic replaces the old callback."""
        cb_old = MagicMock()
        cb_new = MagicMock()
        service.subscribe("topic/x", callback=cb_old)
        service.subscribe("topic/x", callback=cb_new)  # replaces
        assert service._topic_callbacks["topic/x"] is cb_new

    def test_wildcard_hash_matches_subtopics(self, service):
        """# wildcard must match any sub-topic."""
        cb = MagicMock()
        service.subscribe("bacnet/#", callback=cb)

        msg = MagicMock()
        msg.topic = "bacnet/zone/1/temp"
        msg.payload = b"25"
        service._on_message(None, None, msg)

        cb.assert_called_once()

    def test_wildcard_plus_matches_trailing_level(self, service):
        """+ wildcard at trailing position matches a single topic level."""
        cb = MagicMock()
        service.subscribe("bacnet/zone1/+", callback=cb)

        msg = MagicMock()
        msg.topic = "bacnet/zone1/temp"
        msg.payload = b"22"
        service._on_message(None, None, msg)

        cb.assert_called_once()

    def test_global_callback_fallback(self, service):
        """If no topic-specific callback matches, global callback is called."""
        global_cb = MagicMock()
        service._on_message_callback = global_cb  # global fallback

        msg = MagicMock()
        msg.topic = "unregistered/topic"
        msg.payload = b"data"
        service._on_message(None, None, msg)

        global_cb.assert_called_once()


class TestMqttPublish:
    def test_publish_not_called_when_disconnected(self, service):
        """No publish while disconnected — should return False, not raise."""
        service._connected = False
        result = service.publish("test/topic", {"value": 1.0})
        assert result is False

    def test_publish_calls_client_when_connected(self, service):
        service._connected = True
        service.publish("test/topic", "hello")
        service._client.publish.assert_called_once()
