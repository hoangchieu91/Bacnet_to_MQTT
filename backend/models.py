"""Pydantic models for the BACnet-MQTT Gateway."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────
class GatewayStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"


class ObjectType(str, Enum):
    ANALOG_INPUT = "analogInput"
    ANALOG_OUTPUT = "analogOutput"
    ANALOG_VALUE = "analogValue"
    BINARY_INPUT = "binaryInput"
    BINARY_OUTPUT = "binaryOutput"
    BINARY_VALUE = "binaryValue"
    MULTI_STATE_INPUT = "multiStateInput"
    MULTI_STATE_OUTPUT = "multiStateOutput"
    MULTI_STATE_VALUE = "multiStateValue"


# ──────────────────────────────────────────────
# MQTT Configuration
# ──────────────────────────────────────────────
class MqttConfig(BaseModel):
    broker_host: str = "localhost"
    broker_port: int = 1883
    username: str = ""
    password: str = ""
    use_tls: bool = False
    client_id: str = "bacnet_mqtt_gateway"
    topic_prefix: str = "bacnet"
    qos: int = 1
    retain: bool = False


# ──────────────────────────────────────────────
# BACnet Configuration
# ──────────────────────────────────────────────
class BacnetConfig(BaseModel):
    ip: str = "0.0.0.0"
    port: int = 47808
    mask: str = "24"
    device_id: int = 599
    default_poll_interval: int = 10


# ──────────────────────────────────────────────
# BACnet Device / Object
# ──────────────────────────────────────────────
class BacnetObject(BaseModel):
    object_type: str
    object_instance: int
    object_name: str = ""
    description: str = ""
    present_value: Any = None
    units: str = ""


class BacnetDevice(BaseModel):
    device_id: int
    device_name: str = ""
    address: str = ""
    vendor_name: str = ""
    model_name: str = ""
    objects: list[BacnetObject] = Field(default_factory=list)


# ──────────────────────────────────────────────
# Alarm Configuration (user-defined thresholds)
# ──────────────────────────────────────────────
class AlarmConfig(BaseModel):
    enabled: bool = False
    high_limit: float | None = None
    low_limit: float | None = None
    deadband: float = 0.5
    severity: str = "warning"  # "warning" or "critical"


# ──────────────────────────────────────────────
# Mapping: BACnet Point → MQTT Topic
# ──────────────────────────────────────────────
class PointMapping(BaseModel):
    id: str = ""
    device_id: int
    object_type: str
    object_instance: int
    mqtt_topic: str = ""
    poll_interval: int = 10
    read_mode: str = "poll"  # "poll" or "cov"
    enabled: bool = True
    label: str = ""
    group: str = ""  # Group tag (e.g. "FCU-01", "AHU")
    last_value: Any = None
    last_updated: Optional[str] = None
    priority_array: Optional[dict[str, Any]] = None
    # Extended BACnet properties
    units: Optional[str] = None
    state_text: Optional[list[str]] = None
    description: Optional[str] = None
    active_text: Optional[str] = None
    inactive_text: Optional[str] = None
    alarm_config: Optional[AlarmConfig] = None  # User-defined alarm thresholds


class ChartConfig(BaseModel):
    id: str = ""
    name: str = "New Chart"
    point_ids: list[str] = Field(default_factory=list)
    chart_type: str = "line"  # line, bar, gauge
    duration_minutes: int = 30
    refresh_seconds: int = 5


# ──────────────────────────────────────────────
# Full Gateway Configuration
# ──────────────────────────────────────────────
class WebConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080


class GroupConfig(BaseModel):
    id: str
    name: str = ""
    description: str = ""


class GatewayConfig(BaseModel):
    mqtt: MqttConfig = Field(default_factory=MqttConfig)
    bacnet: BacnetConfig = Field(default_factory=BacnetConfig)
    gateway: dict = Field(default_factory=lambda: {"mappings": []})
    web: WebConfig = Field(default_factory=WebConfig)
    groups: list[GroupConfig] = Field(default_factory=list)
    schedules: list["ScheduleEntry"] = Field(default_factory=list)


class ScheduleEntry(BaseModel):
    id: str = ""
    name: str = ""
    device_id: int
    object_type: str
    object_instance: int
    value: Any = None
    priority: int = 8
    cron: str = ""        # Simplified cron: "HH:MM" or "HH:MM|1,2,3,4,5" (days 0=Mon..6=Sun)
    enabled: bool = True


# ──────────────────────────────────────────────
# API Request / Response helpers
# ──────────────────────────────────────────────
class StatusResponse(BaseModel):
    gateway: GatewayStatus = GatewayStatus.STOPPED
    bacnet_connected: bool = False
    mqtt_connected: bool = False
    active_mappings: int = 0
    discovered_devices: int = 0
    uptime_seconds: float = 0


class MqttTestRequest(BaseModel):
    broker_host: str
    broker_port: int = 1883
    username: str = ""
    password: str = ""
    use_tls: bool = False


class DiscoveryRequest(BaseModel):
    timeout: int = 10
    scan_mode: str = "full"  # "full" | "range" | "specific"
    low_id: int | None = None   # For range scan: low device instance
    high_id: int | None = None  # For range scan: high device instance
    device_id: int | None = None  # For specific scan: target device instance


class WriteRequest(BaseModel):
    device_id: int
    object_type: str
    object_instance: int
    value: Any
    priority: int = 16  # BACnet priority 1–16


class ReleaseRequest(BaseModel):
    device_id: int
    object_type: str
    object_instance: int
    priority: int | str = 16  # int (1–16) or "all"
