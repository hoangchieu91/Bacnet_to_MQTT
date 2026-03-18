"""Auto-Discovery & Point Mapping — Phase 2

Tận dụng MstpMaster (mstp_master.py) để join token ring, gửi WhoIs,
đọc Object_List rồi quét tên/đơn vị/mô tả cho từng điểm đo.

Usage from dashboard:
    from point_discovery import discover_device
    results = discover_device("/dev/ttyUSB0", 38400, target_mac=5)
"""

from __future__ import annotations

import csv
import io
import logging
import struct
import time
import threading
from dataclasses import dataclass, field
from typing import Any

from mstp_master import (
    MstpMaster, MstpFrame, FT, build_frame, build_read_property,
    parse_read_property_ack, parse_iam, calc_crc8, calc_crc16,
    OBJ_TYPES, OBJ_NAMES, PROP_IDS, PROP_NAMES,
)

logger = logging.getLogger(__name__)

# BACnet Engineering Units (common subset for BMS)
ENGINEERING_UNITS = {
    0: "sqMeters", 17: "sqFeet", 18: "sqInches",
    62: "degreesCelsius", 63: "degreesFahrenheit", 64: "degreesKelvin",
    65: "degreesRankine",
    73: "noUnits",
    91: "percent", 95: "percentRelativeHumidity",
    98: "psi", 99: "bar", 100: "kPa",
    117: "wattsPerSqMeter",
    118: "lumens", 119: "lux",
    121: "wattsPerSqFoot",
    127: "cubicFeetPerMinute", 128: "cubicMetersPerHour",
    131: "litersPerSecond", 132: "litersPerMinute",
    155: "watts", 156: "kilowatts",
    157: "megawatts", 158: "btusPerHour",
    162: "amperes", 163: "milliamperes", 165: "volts",
    166: "kilovolts", 167: "megavolts",
    169: "hertz",
    175: "revPerMinute",
    189: "hours", 190: "minutes", 191: "seconds",
}

# Additional object type names
EXTENDED_OBJ_NAMES = {
    0: "analogInput", 1: "analogOutput", 2: "analogValue",
    3: "binaryInput", 4: "binaryOutput", 5: "binaryValue",
    8: "device",
    13: "multiStateInput", 14: "multiStateOutput", 19: "multiStateValue",
    10: "file", 12: "loop", 15: "notificationClass",
    16: "program", 17: "schedule", 20: "trendLog",
    6: "calendar", 7: "command", 9: "eventEnrollment",
    11: "group",
}


@dataclass
class DiscoveredPoint:
    """A single discovered BACnet point/object."""
    object_type: int
    object_type_name: str
    instance: int
    name: str = ""
    description: str = ""
    unit: str = ""
    unit_id: int | None = None
    present_value: Any = None

    def to_dict(self) -> dict:
        return {
            "object_type": self.object_type,
            "object_type_name": self.object_type_name,
            "instance": self.instance,
            "name": self.name,
            "description": self.description,
            "unit": self.unit,
            "unit_id": self.unit_id,
            "present_value": self.present_value,
        }


@dataclass
class DiscoveryResult:
    """Result of a device discovery scan."""
    mac: int
    device_instance: int | None = None
    vendor_id: int | None = None
    vendor_name: str = ""
    model_name: str = ""
    firmware: str = ""
    object_count: int = 0
    points: list[DiscoveredPoint] = field(default_factory=list)
    status: str = "pending"  # pending | running | done | error
    error: str = ""
    progress: float = 0.0  # 0..100
    started_at: float = 0.0
    finished_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "mac": self.mac,
            "device_instance": self.device_instance,
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "model_name": self.model_name,
            "firmware": self.firmware,
            "object_count": self.object_count,
            "points": [p.to_dict() for p in self.points],
            "status": self.status,
            "error": self.error,
            "progress": round(self.progress, 1),
            "duration_s": round(self.finished_at - self.started_at, 1) if self.finished_at else 0,
        }

    def to_csv(self) -> str:
        """Export points as CSV string."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Object Type", "Instance", "Name", "Description", "Unit", "Present Value"])
        for p in self.points:
            writer.writerow([
                p.object_type_name, p.instance,
                p.name, p.description, p.unit,
                p.present_value if p.present_value is not None else "",
            ])
        return output.getvalue()


def _parse_object_list_ack(data: bytes) -> list[tuple[int, int]] | None:
    """Parse ReadProperty-ACK for objectList → list of (obj_type, instance)."""
    if len(data) < 4 or data[0] != 0x01:
        return None

    ctrl = data[1]
    offset = 2
    if ctrl & 0x20:
        offset += 2; dlen = data[offset]; offset += 1 + dlen
    if ctrl & 0x08:
        offset += 2; slen = data[offset]; offset += 1 + slen
    if ctrl & 0x20:
        offset += 1

    if offset >= len(data): return None
    pdu_type = (data[offset] >> 4) & 0x0F
    if pdu_type != 3: return None  # Not ComplexAck

    invoke_id = data[offset + 1]
    service = data[offset + 2]
    if service != 12: return None  # Not ReadProperty
    offset += 3

    # Skip context tag 0 (ObjectIdentifier)
    if offset < len(data) and data[offset] == 0x0C:
        offset += 5
    # Skip context tag 1 (PropertyIdentifier)
    if offset < len(data) and (data[offset] & 0xF8) == 0x18:
        plen = data[offset] & 0x07
        offset += 1 + plen

    # Context tag 3 (opening): propertyValue
    if offset >= len(data) or data[offset] != 0x3E:
        return None
    offset += 1  # skip opening tag

    objects = []
    while offset < len(data) and data[offset] != 0x3F:  # 0x3F = closing tag
        tag = data[offset]
        tag_num = (tag >> 4) & 0x0F
        tag_len = tag & 0x07

        if tag_num == 12 and tag_len == 4 and offset + 5 <= len(data):
            oid = struct.unpack('>I', data[offset+1:offset+5])[0]
            obj_type = (oid >> 22) & 0x3FF
            instance = oid & 0x3FFFFF
            objects.append((obj_type, instance))
            offset += 5
        else:
            # Skip unknown tag
            offset += 1 + tag_len
            if tag_len >= 5:  # Extended length
                offset += 1

    return objects if objects else None


def _parse_units_ack(data: bytes) -> int | None:
    """Parse ReadProperty-ACK for units (Enumerated value)."""
    ack = parse_read_property_ack(data)
    if ack and 'value' in ack:
        return ack['value']
    return None


def discover_device(
    port: str,
    baudrate: int,
    target_mac: int,
    my_mac: int = 127,
    duration: float = 60.0,
    result: DiscoveryResult | None = None,
) -> DiscoveryResult:
    """Run a full discovery scan on a target MS/TP device.
    
    This is a BLOCKING call that takes control of the serial port.
    The sniffer MUST be paused before calling this.
    """
    if result is None:
        result = DiscoveryResult(mac=target_mac)
    
    result.status = "running"
    result.started_at = time.time()
    result.progress = 0

    master = MstpMaster(port, baudrate, my_mac)

    # Phase tracking
    phase = {"joined": False, "whois_sent": False, "got_iam": False,
             "obj_list_requested": False, "obj_list": None,
             "current_obj_idx": 0, "prop_queue": [],
             "token_count": 0}

    def on_event(event, data):
        if event == 'joined':
            phase['joined'] = True
            logger.info("[Discovery] Joined token ring as MAC %d", my_mac)
            # Queue WhoIs
            master.queue_whois()
            phase['whois_sent'] = True
            result.progress = 5

        elif event == 'iam':
            dev_id = data.get('device_instance')
            if dev_id is not None:
                result.device_instance = dev_id
                result.vendor_id = data.get('vendor_id')
                phase['got_iam'] = True
                logger.info("[Discovery] Found Device %d at MAC %d", dev_id, data.get('mac', '?'))
                result.progress = 10

        elif event == 'token':
            phase['token_count'] += 1
            tc = phase['token_count']

            # Retry WhoIs if no I-Am yet
            if not phase['got_iam'] and tc in (5, 15, 30):
                master.queue_whois()

            # After I-Am, read device properties then Object_List
            if phase['got_iam'] and not phase['obj_list_requested'] and tc > 3:
                # Read Object_List
                dev_inst = result.device_instance
                master.queue_read_property(
                    target_mac, dev_inst, 8, dev_inst, PROP_IDS["objectList"])
                phase['obj_list_requested'] = True
                result.progress = 15
                logger.info("[Discovery] Requesting Object_List from Device %d", dev_inst)

        elif event == 'reply':
            prop = data.get('property', '')
            
            # Check if this is Object_List response
            if prop == 'objectList' and 'value' not in data:
                # The standard parser might not handle object list arrays
                # We'll handle via raw frame in _handle_frame override
                pass
            elif prop == 'objectName':
                if phase['current_obj_idx'] < len(result.points):
                    result.points[phase['current_obj_idx']].name = str(data.get('value', ''))
            elif prop == 'description':
                if phase['current_obj_idx'] < len(result.points):
                    result.points[phase['current_obj_idx']].description = str(data.get('value', ''))
            elif prop == 'presentValue':
                if phase['current_obj_idx'] < len(result.points):
                    val = data.get('value', data.get('value_raw', ''))
                    result.points[phase['current_obj_idx']].present_value = val

    try:
        master.open()

        # Override _handle_frame to capture raw Object_List ACK
        original_handle = master._handle_frame

        def patched_handle(frame: MstpFrame, callback=None):
            original_handle(frame, callback)
            # Try to parse Object_List from raw frame data
            if (frame.ft == FT.BACNET_DATA_NXR and frame.dst == my_mac
                    and frame.valid and frame.data
                    and phase['obj_list_requested'] and phase['obj_list'] is None):
                obj_list = _parse_object_list_ack(frame.data)
                if obj_list:
                    phase['obj_list'] = obj_list
                    result.object_count = len(obj_list)
                    logger.info("[Discovery] Got Object_List: %d objects", len(obj_list))
                    # Create DiscoveredPoint objects
                    for obj_type, inst in obj_list:
                        type_name = EXTENDED_OBJ_NAMES.get(obj_type, f"type_{obj_type}")
                        result.points.append(DiscoveredPoint(
                            object_type=obj_type,
                            object_type_name=type_name,
                            instance=inst,
                        ))
                    result.progress = 25

        master._handle_frame = patched_handle

        # Run master state machine
        master._state = 1  # IDLE
        start = time.monotonic()
        idle_since = time.monotonic()
        joined = False

        while time.monotonic() - start < duration:
            frame = master._reader.read_frame(timeout=0.1)

            if frame is not None:
                idle_since = time.monotonic()
                master._handle_frame(frame, on_event)
                if not joined and master._state == 4:  # USE_TOKEN
                    joined = True
                    on_event('joined', {'mac': my_mac})
            else:
                silence = time.monotonic() - idle_since
                if silence > master.T_NO_TOKEN and not joined:
                    master._state = 4  # USE_TOKEN
                    joined = True
                    on_event('joined', {'mac': my_mac})

            # State actions
            if master._state == 4:  # USE_TOKEN
                # If we have obj_list, queue next property read
                if phase['obj_list'] and phase['current_obj_idx'] < len(result.points):
                    idx = phase['current_obj_idx']
                    pt = result.points[idx]
                    # Read objectName for current object
                    dev_inst = result.device_instance
                    master.queue_read_property(
                        target_mac, dev_inst, pt.object_type, pt.instance,
                        PROP_IDS["objectName"],
                        invoke_id=(idx % 250) + 1
                    )
                    result.progress = 25 + (idx / len(result.points)) * 70
                    phase['current_obj_idx'] += 1

                master._use_token(on_event)
                master._state = 5  # PASS_TOKEN

            if master._state == 5:  # PASS_TOKEN
                master._pass_token()
                master._state = 2  # IDLE

            # Token to us
            if frame and frame.ft == FT.TOKEN and frame.dst == my_mac:
                master._token_count += 1
                master._state = 4  # USE_TOKEN
                on_event('token', {'count': master._token_count})

            # All done?
            if (phase['obj_list'] is not None
                    and phase['current_obj_idx'] >= len(result.points)):
                logger.info("[Discovery] All objects scanned!")
                break

            # Timeout if no I-Am after 20s
            elapsed = time.monotonic() - start
            if not phase['got_iam'] and elapsed > 20:
                result.status = "error"
                result.error = f"No I-Am from MAC {target_mac} after 20s"
                result.finished_at = time.time()
                return result

    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
        logger.error("[Discovery] Error: %s", exc)
    finally:
        try:
            master.close()
        except Exception:
            pass

    result.status = "done"
    result.progress = 100
    result.finished_at = time.time()
    logger.info("[Discovery] Complete: %d points in %.1fs",
                len(result.points), result.finished_at - result.started_at)
    return result


class DiscoveryRunner:
    """Thread-safe discovery runner that coordinates with sniffer."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._result: DiscoveryResult | None = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def result(self) -> DiscoveryResult | None:
        return self._result

    def start(self, port: str, baudrate: int, target_mac: int,
              my_mac: int = 127, duration: float = 60.0) -> None:
        if self.is_running:
            raise RuntimeError("Discovery already running")

        self._result = DiscoveryResult(mac=target_mac)

        def _run():
            discover_device(port, baudrate, target_mac, my_mac, duration, self._result)

        self._thread = threading.Thread(target=_run, daemon=True, name="discovery")
        self._thread.start()

    def wait(self, timeout: float = 120.0) -> DiscoveryResult | None:
        if self._thread:
            self._thread.join(timeout=timeout)
        return self._result
