"""MS/TP BACnet service — reads/writes BACnet objects via RS-485 serial (MS/TP).

Provides the same high-level interface as BacnetService (BAC0/IP) but uses
MstpMaster from tools/mstp/mstp_master.py for serial communication.

Thread model:
  MstpMaster blocks on serial I/O, so it runs in a dedicated daemon thread.
  Async callers enqueue requests via asyncio-safe queues and await results.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Import MstpMaster from tools/mstp ─────────────────────────────────────────
_MSTP_DIR = str(Path(__file__).resolve().parent.parent / "tools" / "mstp")
if _MSTP_DIR not in sys.path:
    sys.path.insert(0, _MSTP_DIR)

from mstp_master import (  # noqa: E402
    MstpMaster,
    build_read_property,
    build_write_property,
    build_whois,
    parse_iam,
    parse_read_property_ack,
)

# BACnet object type name → numeric ID (ASHRAE 135-2020 §21)
OBJ_TYPE_MAP: dict[str, int] = {
    "analogInput": 0, "analogOutput": 1, "analogValue": 2,
    "binaryInput": 3, "binaryOutput": 4, "binaryValue": 5,
    "multiStateInput": 13, "multiStateOutput": 14, "multiStateValue": 19,
    "device": 8,
    # Hyphenated forms
    "analog-input": 0, "analog-output": 1, "analog-value": 2,
    "binary-input": 3, "binary-output": 4, "binary-value": 5,
    "multi-state-input": 13, "multi-state-output": 14, "multi-state-value": 19,
}

# Reverse: numeric → camelCase name
OBJ_TYPE_NAMES: dict[int, str] = {
    0: "analogInput", 1: "analogOutput", 2: "analogValue",
    3: "binaryInput", 4: "binaryOutput", 5: "binaryValue",
    8: "device",
    13: "multiStateInput", 14: "multiStateOutput", 19: "multiStateValue",
}

# BACnet property name → numeric ID
PROP_MAP: dict[str, int] = {
    "presentValue": 85,
    "objectName": 77,
    "objectList": 76,
    "description": 28,
    "units": 117,
    "stateText": 110,
    "activeText": 4,
    "inactiveText": 46,
    "priorityArray": 87,
    "relinquishDefault": 104,
    "outOfService": 81,
    "statusFlags": 111,
    "eventState": 36,
}


@dataclass
class MstpDevice:
    """A discovered MS/TP device."""
    mac: int
    device_instance: int
    vendor_id: int | None = None
    max_apdu: int | None = None
    segmentation: int | None = None
    objects: list[tuple[str, int]] = field(default_factory=list)  # [(type_name, instance), ...]


@dataclass
class _ReadRequest:
    """Internal request object for the worker thread."""
    mac: int
    device_instance: int
    obj_type: int
    obj_instance: int
    prop_id: int
    future: asyncio.Future
    loop: asyncio.AbstractEventLoop


@dataclass
class _WriteRequest:
    """Internal request object for write operations."""
    mac: int
    device_instance: int
    obj_type: int
    obj_instance: int
    value: Any
    priority: int
    future: asyncio.Future
    loop: asyncio.AbstractEventLoop


class MstpBacnetService:
    """Async-safe MS/TP BACnet service using MstpMaster in a worker thread.

    Usage:
        svc = MstpBacnetService(port="/dev/ttyUSB0", baudrate=38400, mac=31)
        await svc.start()
        value = await svc.read_object(mac=1, device_instance=10121,
                                       object_type="analogValue", object_instance=1)
        await svc.stop()
    """

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 38400, mac: int = 31):
        self.port = port
        self.baudrate = baudrate
        self.mac = mac
        self._master: MstpMaster | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._connected = False
        self._request_queue: list = []  # Protected by _lock
        self._lock = threading.Lock()
        self._devices: dict[int, MstpDevice] = {}  # mac → device info
        self._stop_event = threading.Event()

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the MS/TP master in a background thread."""
        if self._running:
            logger.warning("MS/TP service already running")
            return

        self._stop_event.clear()
        self._running = True

        self._thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="mstp-service",
        )
        self._thread.start()

        # Wait for serial port to open
        for _ in range(20):  # max 2s
            if self._connected:
                break
            await asyncio.sleep(0.1)

        if self._connected:
            logger.info("MS/TP service started on %s @ %d (MAC=%d)",
                        self.port, self.baudrate, self.mac)
        else:
            logger.error("MS/TP service failed to connect to %s", self.port)

    async def stop(self) -> None:
        """Stop the MS/TP master thread."""
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._connected = False
        logger.info("MS/TP service stopped")

    # ── Worker thread ──────────────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        """Main loop running in dedicated thread — manages MstpMaster lifecycle."""
        try:
            import serial
            self._master = MstpMaster(self.port, self.baudrate, self.mac)
            self._master.open()
            self._connected = True
        except Exception as exc:
            logger.error("MS/TP worker: failed to open serial: %s", exc)
            self._connected = False
            self._running = False
            return

        try:
            idle_since = time.monotonic()
            joined = False

            while not self._stop_event.is_set():
                # Read a frame from the bus
                frame = self._master._reader.read_frame(timeout=0.05)

                if frame is not None:
                    idle_since = time.monotonic()
                    self._master._handle_frame(frame)

                    # Join token ring
                    if not joined and self._master._state == 4:  # USE_TOKEN
                        joined = True
                        logger.info("MS/TP service: joined token ring as MAC %d", self.mac)
                else:
                    silence = time.monotonic() - idle_since
                    if silence > self._master.T_NO_TOKEN and not joined:
                        logger.info("MS/TP service: bus silent — generating token")
                        self._master._state = 4  # USE_TOKEN
                        joined = True

                # Process state machine
                if self._master._state == 4:  # USE_TOKEN
                    self._process_pending_request()
                    self._master._state = 5  # PASS_TOKEN

                if self._master._state == 5:  # PASS_TOKEN
                    self._master._pass_token()
                    self._master._state = 1  # IDLE

        except Exception as exc:
            logger.error("MS/TP worker: fatal error: %s", exc, exc_info=True)
        finally:
            try:
                self._master.close()
            except Exception:
                pass
            self._connected = False
            logger.info("MS/TP worker thread exited")

    def _process_pending_request(self) -> None:
        """Process one pending request while we hold the token."""
        with self._lock:
            if not self._request_queue:
                return
            req = self._request_queue.pop(0)

        if isinstance(req, _ReadRequest):
            self._do_read(req)
        elif isinstance(req, _WriteRequest):
            self._do_write(req)

    def _do_read(self, req: _ReadRequest) -> None:
        """Execute a BACnet ReadProperty on the bus."""
        try:
            # Build BACnet ReadProperty APDU
            apdu = build_read_property(
                req.device_instance, req.obj_type, req.obj_instance,
                prop_id=req.prop_id
            )
            # Queue in master and wait for reply
            self._master._pending_request = apdu
            self._master._pending_dst = req.mac
            self._master._pending_expects_reply = True
            self._master._use_token(None)

            # Check for response
            if self._master._response and self._master._response.valid:
                ack = parse_read_property_ack(self._master._response.data)
                value = ack.get('value') if ack else None
                req.loop.call_soon_threadsafe(req.future.set_result, value)
            else:
                req.loop.call_soon_threadsafe(req.future.set_result, None)

        except Exception as exc:
            logger.error("MS/TP read error (MAC %d, obj %d:%d): %s",
                         req.mac, req.obj_type, req.obj_instance, exc)
            try:
                req.loop.call_soon_threadsafe(req.future.set_result, None)
            except Exception:
                pass

    def _do_write(self, req: _WriteRequest) -> None:
        """Execute a BACnet WriteProperty on the bus."""
        try:
            apdu = build_write_property(
                req.device_instance, req.obj_type, req.obj_instance,
                value=req.value, priority=req.priority
            )
            self._master._pending_request = apdu
            self._master._pending_dst = req.mac
            self._master._pending_expects_reply = True
            self._master._use_token(None)

            # SimpleAck = success
            success = (self._master._response is not None
                       and self._master._response.valid)
            req.loop.call_soon_threadsafe(req.future.set_result, success)

        except Exception as exc:
            logger.error("MS/TP write error (MAC %d): %s", req.mac, exc)
            try:
                req.loop.call_soon_threadsafe(req.future.set_result, False)
            except Exception:
                pass

    # ── Public async API ──────────────────────────────────────────────────────

    async def read_object(
        self,
        mac: int,
        device_instance: int,
        object_type: str,
        object_instance: int,
        property_name: str = "presentValue",
    ) -> Any:
        """Read a single BACnet property from an MS/TP device.

        Returns the parsed value, or None on timeout/error.
        """
        if not self._connected:
            raise RuntimeError("MS/TP service not connected")

        obj_type_id = OBJ_TYPE_MAP.get(object_type)
        if obj_type_id is None:
            logger.error("Unknown object type: %s", object_type)
            return None

        prop_id = PROP_MAP.get(property_name, 85)  # default presentValue

        loop = asyncio.get_running_loop()
        future = loop.create_future()

        req = _ReadRequest(
            mac=mac,
            device_instance=device_instance,
            obj_type=obj_type_id,
            obj_instance=object_instance,
            prop_id=prop_id,
            future=future,
            loop=loop,
        )

        with self._lock:
            self._request_queue.append(req)

        try:
            return await asyncio.wait_for(future, timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("MS/TP read timeout: MAC %d %s:%d.%s",
                           mac, object_type, object_instance, property_name)
            return None

    async def write_object(
        self,
        mac: int,
        device_instance: int,
        object_type: str,
        object_instance: int,
        value: Any,
        priority: int = 16,
    ) -> bool:
        """Write a BACnet property on an MS/TP device.

        Returns True on success.
        """
        if not self._connected:
            raise RuntimeError("MS/TP service not connected")

        obj_type_id = OBJ_TYPE_MAP.get(object_type)
        if obj_type_id is None:
            logger.error("Unknown object type: %s", object_type)
            return False

        loop = asyncio.get_running_loop()
        future = loop.create_future()

        req = _WriteRequest(
            mac=mac,
            device_instance=device_instance,
            obj_type=obj_type_id,
            obj_instance=object_instance,
            value=value,
            priority=priority,
            future=future,
            loop=loop,
        )

        with self._lock:
            self._request_queue.append(req)

        try:
            return await asyncio.wait_for(future, timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("MS/TP write timeout: MAC %d %s:%d",
                           mac, object_type, object_instance)
            return False

    async def discover_devices(self, duration: float = 15.0) -> list[MstpDevice]:
        """Send WhoIs and collect I-Am responses for `duration` seconds.

        Returns list of discovered MS/TP devices.
        """
        if not self._connected:
            raise RuntimeError("MS/TP service not connected")

        # Queue a WhoIs broadcast
        apdu = build_whois()
        with self._lock:
            # Direct inject — WhoIs is unconfirmed, no reply expected
            self._master._pending_request = apdu
            self._master._pending_dst = 0xFF  # broadcast
            self._master._pending_expects_reply = False

        # Wait and collect I-Am responses
        await asyncio.sleep(duration)

        devices = []
        for iam in self._master._iam_responses:
            dev = MstpDevice(
                mac=iam.get('mac', 0),
                device_instance=iam.get('device_instance', 0),
                vendor_id=iam.get('vendor_id'),
                max_apdu=iam.get('max_apdu'),
                segmentation=iam.get('segmentation'),
            )
            self._devices[dev.mac] = dev
            devices.append(dev)

        logger.info("MS/TP discover: found %d devices in %.0fs", len(devices), duration)
        return devices

    def get_device_mac(self, device_instance: int) -> int | None:
        """Look up MAC address for a device instance (from discovery cache)."""
        for mac, dev in self._devices.items():
            if dev.device_instance == device_instance:
                return mac
        return None
