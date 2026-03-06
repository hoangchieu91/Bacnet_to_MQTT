"""MQTT service — paho-mqtt wrapper for publish / subscribe."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable

import paho.mqtt.client as mqtt

from backend.models import MqttConfig

logger = logging.getLogger(__name__)


class MqttService:
    """Manages MQTT broker connection, publishing, and subscriptions."""

    def __init__(self, config: MqttConfig):
        self._config = config
        self._client: mqtt.Client | None = None
        self._connected = False
        self._on_message_callback: Callable[[str, Any], None] | None = None
        self._lock = threading.Lock()

    # ── lifecycle ──────────────────────────────
    def start(self) -> None:
        """Connect to the MQTT broker in a background thread."""
        self._client = mqtt.Client(
            client_id=self._config.client_id,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )

        if self._config.username:
            self._client.username_pw_set(self._config.username, self._config.password)

        if self._config.use_tls:
            self._client.tls_set()

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        try:
            self._client.connect(self._config.broker_host, self._config.broker_port, keepalive=60)
            self._client.loop_start()
            logger.info(
                "MQTT connecting to %s:%d …",
                self._config.broker_host,
                self._config.broker_port,
            )
        except Exception as exc:
            logger.error("MQTT connection failed: %s", exc)
            self._connected = False

    def stop(self) -> None:
        """Disconnect and stop the MQTT loop."""
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        self._connected = False
        logger.info("MQTT service stopped.")

    @property
    def connected(self) -> bool:
        return self._connected

    # ── publish ────────────────────────────────
    def publish(self, topic: str, payload: Any, qos: int | None = None, retain: bool | None = None) -> bool:
        """Publish a message (dict will be JSON-serialised)."""
        if not self._connected or self._client is None:
            logger.warning("MQTT not connected — skipping publish to %s", topic)
            return False

        if isinstance(payload, dict):
            payload = json.dumps(payload, default=str)
        elif not isinstance(payload, (str, bytes)):
            payload = str(payload)

        qos = qos if qos is not None else self._config.qos
        retain = retain if retain is not None else self._config.retain

        try:
            result = self._client.publish(topic, payload, qos=qos, retain=retain)
            result.wait_for_publish(timeout=5)
            return True
        except Exception as exc:
            logger.error("MQTT publish failed on %s: %s", topic, exc)
            return False

    # ── subscribe ──────────────────────────────
    def subscribe(self, topic: str, callback: Callable[[str, Any], None] | None = None) -> None:
        """Subscribe to a topic. Uses the global callback if none provided."""
        if self._client is None:
            return
        if callback:
            self._on_message_callback = callback
        self._client.subscribe(topic, qos=self._config.qos)
        logger.info("Subscribed to MQTT topic: %s", topic)

    # ── callbacks ──────────────────────────────
    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: Any, properties: Any = None) -> None:
        if hasattr(rc, 'value'):
            rc_val = rc.value
        else:
            rc_val = rc
        if rc_val == 0:
            self._connected = True
            logger.info("MQTT connected to broker.")
        else:
            self._connected = False
            logger.error("MQTT connection refused (rc=%s).", rc)

    def _on_disconnect(self, client: Any, userdata: Any, flags: Any, rc: Any, properties: Any = None) -> None:
        self._connected = False
        logger.warning("MQTT disconnected (rc=%s).", rc)

    def _on_message(self, client: Any, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = msg.payload.decode(errors="replace")

        logger.debug("MQTT message on %s: %s", topic, payload)
        if self._on_message_callback:
            self._on_message_callback(topic, payload)

    # ── config update ──────────────────────────
    def update_config(self, config: MqttConfig) -> None:
        """Update config and reconnect."""
        was_running = self._connected
        if was_running:
            self.stop()
        self._config = config
        if was_running:
            self.start()

    # ── test connection (static) ───────────────
    @staticmethod
    def test_connection(host: str, port: int, username: str = "", password: str = "", use_tls: bool = False) -> dict:
        """Quick connectivity test — returns success/error dict."""
        test_client = mqtt.Client(
            client_id="bacnet_gw_test",
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        result = {"success": False, "message": ""}

        if username:
            test_client.username_pw_set(username, password)
        if use_tls:
            test_client.tls_set()

        try:
            test_client.connect(host, port, keepalive=5)
            test_client.loop_start()

            import time
            time.sleep(2)

            if test_client.is_connected():
                result = {"success": True, "message": f"Connected to {host}:{port}"}
            else:
                result = {"success": False, "message": f"Could not connect to {host}:{port}"}

            test_client.loop_stop()
            test_client.disconnect()
        except Exception as exc:
            result = {"success": False, "message": str(exc)}

        return result
