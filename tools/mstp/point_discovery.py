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


def _skip_npdu(data: bytes) -> int | None:
    """Return offset of APDU start after skipping NPDU routing headers.
    Returns None if data too short or invalid NPDU."""
    if len(data) < 2 or data[0] != 0x01:
        return None
    ctrl = data[1]
    off = 2
    # DNET + DADR
    if ctrl & 0x20:
        if off + 3 > len(data): return None
        off += 2  # DNET (2 bytes)
        if off >= len(data): return None
        dlen = data[off]; off += 1 + dlen  # DADR length + DADR
    # SNET + SADR
    if ctrl & 0x08:
        if off + 3 > len(data): return None
        off += 2  # SNET
        if off >= len(data): return None
        slen = data[off]; off += 1 + slen
    # Hop count (only present if DNET was present)
    if ctrl & 0x20:
        off += 1
    return off if off < len(data) else None


def _parse_object_list_ack(data: bytes) -> list[tuple[int, int]] | None:
    """Parse ReadProperty-ACK for objectList → list of (obj_type, instance)."""
    off = _skip_npdu(data)
    if off is None: return None

    pdu_type = (data[off] >> 4) & 0x0F
    if pdu_type != 3: return None  # Not ComplexAck
    if off + 3 > len(data): return None

    service = data[off + 2]
    if service != 12: return None  # Not ReadProperty
    off += 3

    # Skip context tag 0 (ObjectIdentifier) — 0x0C + 4 bytes
    if off < len(data) and data[off] == 0x0C:
        off += 5
    # Skip context tag 1 (PropertyIdentifier) — 0x19 xx or 0x1A xx xx
    if off < len(data) and (data[off] & 0xF8) == 0x18:
        plen = data[off] & 0x07
        off += 1 + plen
    # Skip optional context tag 2 (arrayIndex)
    if off < len(data) and (data[off] & 0xF8) == 0x28:
        alen = data[off] & 0x07
        off += 1 + alen

    # Context tag 3 (opening 0x3E): propertyValue
    if off >= len(data) or data[off] != 0x3E:
        return None
    off += 1  # skip opening tag

    objects = []
    while off < len(data) and data[off] != 0x3F:  # 0x3F = closing tag
        tag = data[off]
        tag_num = (tag >> 4) & 0x0F
        tag_len = tag & 0x07
        if tag_num == 12 and tag_len == 4 and off + 5 <= len(data):
            oid = struct.unpack('>I', data[off+1:off+5])[0]
            obj_type = (oid >> 22) & 0x3FF
            instance = oid & 0x3FFFFF
            objects.append((obj_type, instance))
            off += 5
        else:
            off += 1 + (tag_len if tag_len < 5 else data[off+1] + 1 if off+1 < len(data) else 0)
            if off <= 0: break  # safety

    logger.info("[Discovery] _parse_object_list_ack found %d objects", len(objects))
    return objects if objects else None


def _parse_object_count_ack(data: bytes) -> int | None:
    """Parse ReadProperty-ACK for objectList array index 0 → item count."""
    ack = parse_read_property_ack(data)
    if ack and 'value' in ack:
        try:
            return int(ack['value'])
        except (ValueError, TypeError):
            pass
    return None


def _parse_units_ack(data: bytes) -> int | None:
    """Parse ReadProperty-ACK for units (Enumerated value)."""
    ack = parse_read_property_ack(data)
    if ack and 'value' in ack:
        return ack['value']
    return None


def _parse_single_oid_ack(data: bytes) -> tuple[int, int] | None:
    """Parse ReadProperty-ACK for objectList[N] → single (obj_type, instance).
    The value is an ObjectIdentifier (application tag 12, 4 bytes)."""
    off = _skip_npdu(data)
    if off is None:
        return None
    pdu_type = (data[off] >> 4) & 0x0F
    if pdu_type != 3:
        return None  # Not ComplexAck
    if off + 3 > len(data):
        return None
    service = data[off + 2]
    if service != 12:
        return None  # Not ReadProperty
    off += 3

    # Skip context tag 0 (ObjectIdentifier) — 0x0C + 4 bytes
    if off < len(data) and data[off] == 0x0C:
        off += 5
    # Skip context tag 1 (PropertyIdentifier)
    if off < len(data) and (data[off] & 0xF8) == 0x18:
        plen = data[off] & 0x07
        off += 1 + plen
    # Skip context tag 2 (arrayIndex)
    if off < len(data) and (data[off] & 0xF8) == 0x28:
        alen = data[off] & 0x07
        off += 1 + alen

    # Context tag 3 (opening 0x3E): propertyValue
    if off >= len(data) or data[off] != 0x3E:
        return None
    off += 1

    # Application tag 12 (ObjectIdentifier): tag=0xC4, 4 bytes
    if off < len(data):
        tag = data[off]
        tag_num = (tag >> 4) & 0x0F
        tag_len = tag & 0x07
        if tag_num == 12 and tag_len == 4 and off + 5 <= len(data):
            oid = struct.unpack('>I', data[off + 1:off + 5])[0]
            obj_type = (oid >> 22) & 0x3FF
            instance = oid & 0x3FFFFF
            return (obj_type, instance)

    return None


def discover_device(
    port: str,
    baudrate: int,
    target_mac: int,
    my_mac: int = 127,
    duration: float = 60.0,
    result: DiscoveryResult | None = None,
    known_device_instance: int | None = None,
) -> DiscoveryResult:
    """Run a full discovery scan on a target MS/TP device.
    
    This is a BLOCKING call that takes control of the serial port.
    The sniffer MUST be paused before calling this.
    
    If known_device_instance is provided (from sniffer data), skip WhoIs phase.
    """
    if result is None:
        result = DiscoveryResult(mac=target_mac)
    
    result.status = "running"
    result.started_at = time.time()
    result.progress = 0
    logger.info("[Discovery] Starting scan MAC %d (known_dev=%s) port=%s baud=%d my_mac=%d",
                target_mac, known_device_instance, port, baudrate, my_mac)

    master = MstpMaster(port, baudrate, my_mac)

    # Phase tracking
    # Strategy: Read objectList[0] for count, then objectList[1..N] one-by-one
    # to avoid segmentation (MS/TP devices can't segment large Object_List)
    phase = {"joined": False, "whois_sent": False, "got_iam": False,
             "count_requested": False, "obj_count": None,
             "obj_fetch_idx": 1,  # 1-based BACnet array index
             "obj_list_done": False,
             "name_fetch_idx": 0,  # index into result.points
             "token_count": 0,
             "retry_count": 0}

    # If we know the device instance from sniffer, skip I-Am phase
    if known_device_instance is not None:
        result.device_instance = known_device_instance
        phase['got_iam'] = True
        logger.info("[Discovery] Using known device instance %d (from sniffer)", known_device_instance)
        result.progress = 10

    def on_event(event, data):
        if event == 'joined':
            phase['joined'] = True
            logger.info("[Discovery] Joined token ring as MAC %d", my_mac)
            if not phase['got_iam']:
                master.queue_whois()
                phase['whois_sent'] = True
            result.progress = 5

        elif event == 'iam':
            dev_id = data.get('device_instance')
            src_mac = data.get('mac')
            logger.info("[Discovery] Received I-Am: Device %s from MAC %s", dev_id, src_mac)
            # Only accept I-Am that originates from our target_mac
            if dev_id is not None and not phase['got_iam'] and src_mac == target_mac:
                result.device_instance = dev_id
                result.vendor_id = data.get('vendor_id')
                phase['got_iam'] = True
                logger.info("[Discovery] ✓ Accepted Device %d from target MAC %d", dev_id, target_mac)
                result.progress = 10

        elif event == 'token':
            phase['token_count'] += 1
            tc = phase['token_count']

            # Retry WhoIs if no I-Am yet
            if not phase['got_iam'] and tc in (5, 15, 30):
                logger.info("[Discovery] Retry WhoIs (token #%d)", tc)
                master.queue_whois()

        elif event == 'reply':
            prop = data.get('property', '')
            logger.debug("[Discovery] Reply: prop=%s value=%s", prop, data.get('value', '?'))
            if prop == 'objectName':
                idx = phase['name_fetch_idx']
                if idx < len(result.points):
                    result.points[idx].name = str(data.get('value', ''))
            elif prop == 'description':
                idx = phase['name_fetch_idx']
                if idx < len(result.points):
                    result.points[idx].description = str(data.get('value', ''))
            elif prop == 'presentValue':
                idx = phase['name_fetch_idx']
                if idx < len(result.points):
                    val = data.get('value', data.get('value_raw', ''))
                    result.points[idx].present_value = val

    try:
        master.open()
        logger.info("[Discovery] Serial port opened")

        # Override _handle_frame to capture raw BACnet frames for element-by-element parsing
        original_handle = master._handle_frame

        def patched_handle(frame: MstpFrame, callback=None):
            original_handle(frame, callback)
            # Parse replies addressed to us
            if (frame.ft in (FT.BACNET_DATA_NXR, FT.BACNET_DATA_XR)
                    and frame.dst == my_mac and frame.valid and frame.data):

                # Check for Error PDU
                npdu_off = _skip_npdu(frame.data)
                if npdu_off is not None and npdu_off < len(frame.data):
                    pdu_type = (frame.data[npdu_off] >> 4) & 0x0F
                    if pdu_type == 5:  # ErrorPDU
                        logger.warning("[Discovery] Device returned Error (hex: %s)",
                                       frame.data[:30].hex())
                        # Allow retry on next token
                        if phase['count_requested'] and phase['obj_count'] is None:
                            phase['count_requested'] = False
                            phase['retry_count'] += 1
                        return

                # Phase 1: Parse objectList[0] (count)
                if phase['count_requested'] and phase['obj_count'] is None:
                    count = _parse_object_count_ack(frame.data)
                    if count is not None:
                        phase['obj_count'] = count
                        result.object_count = count
                        logger.info("[Discovery] ✓ objectList count = %d", count)
                        result.progress = 15
                    return

                # Phase 2: Parse objectList[N] (single OID)
                if phase['obj_count'] is not None and not phase['obj_list_done']:
                    oid = _parse_single_oid_ack(frame.data)
                    # Fallback: use parse_read_property_ack's value_raw (4-byte OID hex)
                    if oid is None:
                        ack = parse_read_property_ack(frame.data)
                        if ack and ack.get('property') == 'objectList':
                            raw = ack.get('value_raw', '')
                            if len(raw) == 8:  # 4 bytes = 8 hex chars
                                try:
                                    oid_int = int(raw, 16)
                                    obj_type = (oid_int >> 22) & 0x3FF
                                    instance = oid_int & 0x3FFFFF
                                    oid = (obj_type, instance)
                                except ValueError:
                                    pass
                    if oid:
                        obj_type, inst = oid
                        type_name = EXTENDED_OBJ_NAMES.get(obj_type, f"type_{obj_type}")
                        result.points.append(DiscoveredPoint(
                            object_type=obj_type,
                            object_type_name=type_name,
                            instance=inst,
                        ))
                        pct = 15 + (len(result.points) / phase['obj_count']) * 50
                        result.progress = min(pct, 65)
                        logger.debug("[Discovery] objectList[%d] = %s:%d (%d/%d)",
                                     phase['obj_fetch_idx'] - 1, type_name, inst,
                                     len(result.points), phase['obj_count'])
                    return

        master._handle_frame = patched_handle

        # Run master state machine
        master._state = 1  # IDLE
        start = time.monotonic()
        idle_since = time.monotonic()
        joined = False
        count_request_time = None

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
                dev_inst = result.device_instance

                # Step 1: Request objectList[0] (count)
                if (phase['got_iam'] and not phase['count_requested']
                        and phase['token_count'] > 2):
                    logger.info("[Discovery] Requesting objectList[0] (count) Device %d MAC %d",
                                dev_inst, target_mac)
                    master.queue_read_property(
                        target_mac, dev_inst, 8, dev_inst,
                        PROP_IDS["objectList"], array_index=0)
                    phase['count_requested'] = True
                    count_request_time = time.monotonic()

                # Retry count if error was received
                elif (phase['count_requested'] and phase['obj_count'] is None
                      and not phase.get('_count_pending')
                      and phase['retry_count'] > 0 and phase['retry_count'] <= 3):
                    logger.info("[Discovery] Retrying objectList[0] (attempt %d)", phase['retry_count'] + 1)
                    master.queue_read_property(
                        target_mac, dev_inst, 8, dev_inst,
                        PROP_IDS["objectList"], array_index=0)

                # Step 2: Read objectList[N] one by one
                elif (phase['obj_count'] is not None and not phase['obj_list_done']
                      and phase['obj_fetch_idx'] <= phase['obj_count']):
                    idx = phase['obj_fetch_idx']
                    master.queue_read_property(
                        target_mac, dev_inst, 8, dev_inst,
                        PROP_IDS["objectList"],
                        invoke_id=(idx % 250) + 1,
                        array_index=idx)
                    phase['obj_fetch_idx'] += 1

                # Mark obj_list as done when all fetched
                elif (phase['obj_count'] is not None
                      and phase['obj_fetch_idx'] > phase['obj_count']
                      and len(result.points) >= phase['obj_count']
                      and not phase['obj_list_done']):
                    phase['obj_list_done'] = True
                    logger.info("[Discovery] ✓ All %d objects enumerated", len(result.points))
                    result.progress = 65

                # Step 3: Read objectName for each point
                elif phase['obj_list_done'] and phase['name_fetch_idx'] < len(result.points):
                    idx = phase['name_fetch_idx']
                    pt = result.points[idx]
                    master.queue_read_property(
                        target_mac, dev_inst, pt.object_type, pt.instance,
                        PROP_IDS["objectName"],
                        invoke_id=(idx % 250) + 1)
                    pct = 65 + (idx / len(result.points)) * 30
                    result.progress = min(pct, 95)
                    phase['name_fetch_idx'] += 1

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

            # Check if obj_list fetch needs 'done' flag (some elements might not parse)
            if (phase['obj_count'] is not None
                    and phase['obj_fetch_idx'] > phase['obj_count']
                    and not phase['obj_list_done']):
                # Give a small grace period then mark done with what we got
                if time.monotonic() - start > 10:
                    phase['obj_list_done'] = True
                    logger.info("[Discovery] objectList fetch done: %d/%d parsed",
                                len(result.points), phase['obj_count'])

            # All done? (names fetched for all points)
            if (phase['obj_list_done']
                    and phase['name_fetch_idx'] >= len(result.points)):
                logger.info("[Discovery] All %d objects scanned with names!", len(result.points))
                break

            # Timeout if no I-Am after 30s
            elapsed = time.monotonic() - start
            if not phase['got_iam'] and elapsed > 30:
                result.status = "error"
                result.error = f"No I-Am from MAC {target_mac} after 30s"
                result.finished_at = time.time()
                logger.error("[Discovery] %s", result.error)
                return result

            # Timeout if objectList[0] count not received after 20s
            if (phase['count_requested'] and phase['obj_count'] is None
                    and count_request_time and time.monotonic() - count_request_time > 20):
                result.status = "error"
                result.error = "objectList count timeout after 20s"
                result.finished_at = time.time()
                logger.error("[Discovery] %s", result.error)
                return result

    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
        logger.error("[Discovery] Error: %s", exc, exc_info=True)
    finally:
        try:
            master.close()
        except Exception:
            pass

    if result.status != "error":
        result.status = "done"
    result.progress = 100
    result.finished_at = time.time()
    logger.info("[Discovery] Complete: %d points in %.1fs (status=%s)",
                len(result.points), result.finished_at - result.started_at, result.status)
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
              my_mac: int = 127, duration: float = 60.0,
              known_device_instance: int | None = None) -> None:
        if self.is_running:
            raise RuntimeError("Discovery already running")

        self._result = DiscoveryResult(mac=target_mac)

        def _run():
            discover_device(port, baudrate, target_mac, my_mac, duration,
                            self._result, known_device_instance)

        self._thread = threading.Thread(target=_run, daemon=True, name="discovery")
        self._thread.start()

    def wait(self, timeout: float = 120.0) -> DiscoveryResult | None:
        if self._thread:
            self._thread.join(timeout=timeout)
        return self._result
