"""MS/TP Protocol Bridge — BAC0 MS/TP → REST API + MQTT

Đọc object values từ MS/TP bus, cache và publish qua:
  - REST: GET /api/bridge/{node_id}/{obj_type}/{instance}
  - MQTT: mstp/{node_id}/{obj_type}/{instance}/value

Chỉ publish khi value thay đổi (COV-like behaviour).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class PointValue:
    node_id: int
    object_type: str
    instance: int
    value: Any
    timestamp: float
    source: str = "mstp"

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "object_type": self.object_type,
            "instance": self.instance,
            "value": self.value,
            "timestamp": self.timestamp,
            "source": self.source,
        }


class MstpBridge:
    """Polls MS/TP objects and exposes values via REST + MQTT.

    Design notes:
    - Pi joins token ring via BAC0 (same as Scanner)
    - Polls every `poll_interval` seconds
    - Cache: {(node_id, obj_type, instance): PointValue}
    - Only publishes MQTT on value change (reduces broker load)
    """

    def __init__(
        self,
        bacnet: Any,             # BAC0 network instance (reused from scanner/monitor)
        poll_interval: float = 30.0,
        mqtt_enabled: bool = False,
        mqtt_cfg: dict | None = None,
        topic_prefix: str = "mstp",
    ):
        self._bacnet = bacnet
        self._poll_interval = poll_interval
        self._mqtt_enabled = mqtt_enabled
        self._mqtt_cfg = mqtt_cfg or {}
        self._topic_prefix = topic_prefix
        self._cache: dict[tuple, PointValue] = {}
        self._last_values: dict[tuple, Any] = {}
        self._running = False
        self._mqtt_client: Any = None

    @classmethod
    def from_config(cls, config_path: str = "config.yaml", bacnet: Any = None) -> "MstpBridge":
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        bridge_cfg = cfg.get("bridge", {})
        mqtt_cfg   = cfg.get("mqtt", {})
        return cls(
            bacnet=bacnet,
            poll_interval=bridge_cfg.get("poll_interval", 30),
            mqtt_enabled=mqtt_cfg.get("enabled", False),
            mqtt_cfg=mqtt_cfg,
            topic_prefix=mqtt_cfg.get("topic_prefix", "mstp"),
        )

    # ── MQTT ───────────────────────────────────────────────────────────────

    def _mqtt_connect(self) -> None:
        if not self._mqtt_enabled:
            return
        try:
            import paho.mqtt.client as mqtt
            host = self._mqtt_cfg.get("broker_host", "localhost")
            port = self._mqtt_cfg.get("broker_port", 1883)
            self._mqtt_client = mqtt.Client(client_id="mstp-bridge")
            if self._mqtt_cfg.get("username"):
                self._mqtt_client.username_pw_set(
                    self._mqtt_cfg["username"],
                    self._mqtt_cfg.get("password", ""),
                )
            self._mqtt_client.connect(host, port, keepalive=60)
            self._mqtt_client.loop_start()
            logger.info("[Bridge] MQTT connected → %s:%s", host, port)
        except Exception as exc:
            logger.warning("[Bridge] MQTT connect failed: %s", exc)
            self._mqtt_client = None

    def _mqtt_publish(self, key: tuple, pv: PointValue) -> None:
        if not self._mqtt_client:
            return
        topic = f"{self._topic_prefix}/{pv.node_id}/{pv.object_type}/{pv.instance}/value"
        payload = json.dumps(pv.to_dict())
        try:
            self._mqtt_client.publish(topic, payload, qos=0, retain=False)
        except Exception as exc:
            logger.debug("[Bridge] MQTT publish error: %s", exc)

    # ── Polling ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._mqtt_connect()
        self._running = True
        logger.info("[Bridge] Started (poll every %ds, MQTT=%s)",
                    self._poll_interval, self._mqtt_enabled)

    async def stop(self) -> None:
        self._running = False
        if self._mqtt_client:
            self._mqtt_client.loop_stop()
            self._mqtt_client.disconnect()
        logger.info("[Bridge] Stopped")

    async def run_poll_loop(self) -> None:
        """Continuous poll loop — call after start()."""
        while self._running:
            await self._poll_all()
            await asyncio.sleep(self._poll_interval)

    async def _poll_all(self) -> None:
        """Poll every device+object discovered by BAC0."""
        if not self._bacnet:
            return
        try:
            devices = list(self._bacnet.devices or [])
        except Exception:
            return

        for dev_key in devices:
            await self._poll_device(dev_key)

    async def _poll_device(self, dev_key: Any) -> None:
        """Read presentValue for all objects on a device."""
        try:
            dev = self._bacnet[dev_key]
            # Extract node/device id from address
            addr_str = str(dev_key[1]) if isinstance(dev_key, tuple) else str(dev_key)
            node_id = int(addr_str.split(":")[-1]) if ":" in addr_str else 0

            # Iterate BAC0 object list for this device
            obj_list = await asyncio.get_event_loop().run_in_executor(
                None, lambda: dev["objectList"].value
            )
            if not obj_list:
                return

            for obj_ref in obj_list:
                try:
                    # obj_ref: BACpypes ObjectIdentifier (type, instance)
                    obj_type = str(obj_ref[0])
                    inst     = int(obj_ref[1])
                    key      = (node_id, obj_type, inst)

                    val = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda k=dev_key, ot=obj_type, i=inst:
                            self._bacnet[k][f"{ot},{i}"]["presentValue"].value
                    )

                    now = time.time()
                    pv = PointValue(
                        node_id=node_id,
                        object_type=obj_type,
                        instance=inst,
                        value=val,
                        timestamp=now,
                    )
                    self._cache[key] = pv

                    # Only publish if value changed
                    if self._last_values.get(key) != val:
                        self._last_values[key] = val
                        self._mqtt_publish(key, pv)

                except Exception:
                    pass  # Skip individual object errors silently

        except Exception as exc:
            logger.debug("[Bridge] Poll device %s error: %s", dev_key, exc)

    # ── REST accessors ─────────────────────────────────────────────────────

    def get_value(self, node_id: int, obj_type: str, instance: int) -> PointValue | None:
        return self._cache.get((node_id, obj_type, instance))

    def get_all_values(self) -> list[dict]:
        return [pv.to_dict() for pv in self._cache.values()]

    def get_node_values(self, node_id: int) -> list[dict]:
        return [
            pv.to_dict()
            for (nid, *_), pv in self._cache.items()
            if nid == node_id
        ]
