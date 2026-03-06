"""BACnet service — wraps BAC0 for device discovery, reading, writing, and priority array."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.models import BacnetConfig, BacnetDevice, BacnetObject

logger = logging.getLogger(__name__)

# Map hyphenated BACnet type names to camelCase (what BAC0 expects)
_TYPE_NORMALIZE = {
    'analog-input': 'analogInput',
    'analog-output': 'analogOutput',
    'analog-value': 'analogValue',
    'binary-input': 'binaryInput',
    'binary-output': 'binaryOutput',
    'binary-value': 'binaryValue',
    'multi-state-input': 'multiStateInput',
    'multi-state-output': 'multiStateOutput',
    'multi-state-value': 'multiStateValue',
}


def _normalize_type(object_type: str) -> str:
    """Convert hyphenated BACnet type to camelCase for BAC0."""
    return _TYPE_NORMALIZE.get(object_type, object_type)


class BacnetService:
    """Thin async wrapper around BAC0 for BACnet/IP communication."""

    def __init__(self, config: BacnetConfig):
        self._config = config
        self._network: Any = None
        self._connected = False
        self._devices: dict[int, BacnetDevice] = {}

    # ── lifecycle ──────────────────────────────
    async def start(self) -> None:
        """Initialise BAC0 network connection."""
        try:
            import BAC0

            # Kill any stale process holding the BACnet UDP port
            self._kill_stale_bacnet_port(self._config.port)

            ip_str = self._config.ip
            if ip_str and ip_str != "0.0.0.0":
                # BAC0 requires IP/mask format, e.g. '10.25.7.21/24'
                mask = self._config.mask
                if mask and '/' not in ip_str:
                    ip_str = f"{ip_str}/{mask}"
            else:
                ip_str = None

            self._network = BAC0.lite(
                ip=ip_str,
                port=self._config.port,
            )
            self._connected = True
            logger.info("BACnet service started on %s:%s", ip_str or 'auto', self._config.port)
        except Exception as exc:
            logger.error("Failed to start BACnet service: %s", exc)
            self._connected = False
            raise

    @staticmethod
    def _kill_stale_bacnet_port(port: int) -> None:
        """Force-release any stale process holding the BACnet UDP port."""
        import subprocess
        try:
            result = subprocess.run(
                ["fuser", "-k", "-n", "udp", str(port)],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                logger.warning("Killed stale process(es) on UDP port %d: %s", port, result.stdout.strip())
                import time
                time.sleep(0.5)
        except FileNotFoundError:
            # fuser not available, try ss
            try:
                result = subprocess.run(
                    ["ss", "-ulnp", "sport", f"= :{port}"],
                    capture_output=True, text=True, timeout=5,
                )
                if 'pid=' in result.stdout:
                    import re
                    pids = re.findall(r'pid=(\d+)', result.stdout)
                    for pid in pids:
                        try:
                            import os, signal
                            os.kill(int(pid), signal.SIGKILL)
                            logger.warning("Killed stale PID %s on UDP port %d", pid, port)
                        except OSError:
                            pass
                    import time
                    time.sleep(0.5)
            except Exception:
                pass
        except Exception as exc:
            logger.debug("Port cleanup check: %s", exc)

    async def stop(self) -> None:
        """Shut down BAC0 network and release UDP socket."""
        if self._network is not None:
            try:
                self._network.disconnect()
            except Exception:
                pass
            # Force cleanup BACpypes3 internal sockets
            try:
                import asyncio
                for task in asyncio.all_tasks():
                    if 'BACpypes' in str(task) or 'BAC0' in str(task):
                        task.cancel()
            except Exception:
                pass
            self._network = None
        self._connected = False
        logger.info("BACnet service stopped.")

    @property
    def connected(self) -> bool:
        return self._connected

    # ── discovery ──────────────────────────────
    async def discover_devices(
        self,
        timeout: int = 10,
        scan_mode: str = "full",
        low_id: int | None = None,
        high_id: int | None = None,
        device_id: int | None = None,
    ) -> list[BacnetDevice]:
        """Broadcast Who-Is and return discovered devices.

        scan_mode:
            'full'     — broadcast WHO-IS to all devices
            'range'    — WHO-IS with device ID range [low_id, high_id]
            'specific' — WHO-IS targeting a single device_id
        """
        if not self._connected or self._network is None:
            raise RuntimeError("BACnet service not started")

        devices: list[BacnetDevice] = []
        try:
            # Send WHO-IS based on scan mode
            if scan_mode == "specific" and device_id is not None:
                logger.info("Scanning for specific device ID: %d", device_id)
                try:
                    await self._network.who_is(device_id, device_id)
                except Exception:
                    self._network.discover(global_broadcast=False)
            elif scan_mode == "range" and low_id is not None and high_id is not None:
                logger.info("Scanning device ID range: %d – %d", low_id, high_id)
                try:
                    await self._network.who_is(low_id, high_id)
                except Exception:
                    self._network.discover(global_broadcast=True)
            else:
                logger.info("Full network scan (broadcast WHO-IS)")
                try:
                    self._network.discover(global_broadcast=True)
                except TypeError:
                    self._network.discover()

            # Wait for responses
            await asyncio.sleep(timeout)

            # Get discovered devices from discoveredDevices dict
            # (net.devices async property is broken in BAC0 2025.09.15 — returns None)
            discovered_dict = self._network.discoveredDevices or {}
            logger.info("Raw discoveredDevices: %s", discovered_dict)

            for key, info in discovered_dict.items():
                try:
                    # key format: 'device,703'
                    # info format: {'object_instance': (ObjectType.device, 703),
                    #               'address': IPv4Address('10.25.7.117'),
                    #               'vendor_id': 36, 'vendor_name': 'unknown'}
                    obj_inst = info.get("object_instance", (None, 0))
                    if isinstance(obj_inst, (list, tuple)) and len(obj_inst) >= 2:
                        instance = int(obj_inst[1])
                    else:
                        instance = int(obj_inst) if obj_inst else 0

                    address = str(info.get("address", ""))
                    vendor_name = str(info.get("vendor_name", ""))
                    vendor_id = info.get("vendor_id", None)

                    # Try to read device name
                    name = ""
                    try:
                        name = await self._network.read(f"{address} device {instance} objectName")
                        name = str(name) if name else ""
                    except Exception:
                        name = key  # fallback to 'device,703'

                    device = BacnetDevice(
                        device_id=instance,
                        device_name=name,
                        address=address,
                        vendor_name=vendor_name if vendor_name != "unknown" else f"Vendor {vendor_id}",
                    )
                    devices.append(device)
                    self._devices[instance] = device
                except Exception as e:
                    logger.warning("Error parsing device: %s (%s)", key, e)

            logger.info("Discovered %d BACnet devices (mode=%s).", len(devices), scan_mode)
        except Exception as exc:
            logger.error("Discovery failed: %s", exc)

        return devices

    # ── read ───────────────────────────────────
    async def read_object(
        self,
        address: str,
        object_type: str,
        object_instance: int,
        property_name: str = "presentValue",
    ) -> Any:
        """Read a single BACnet object property."""
        if not self._connected or self._network is None:
            raise RuntimeError("BACnet service not started")

        try:
            ot = _normalize_type(object_type)
            request_str = f"{address} {ot} {object_instance} {property_name}"
            value = await self._network.read(request_str)
            return value
        except Exception as exc:
            logger.error("Read failed for %s %s %d: %s", address, object_type, object_instance, exc)
            return None

    async def read_priority_array(
        self,
        address: str,
        object_type: str,
        object_instance: int,
    ) -> dict[str, Any]:
        """Read the full 16-level priorityArray of a commandable BACnet object."""
        if not self._connected or self._network is None:
            raise RuntimeError("BACnet service not started")

        result: dict[str, Any] = {}
        try:
            ot = _normalize_type(object_type)
            # First try BAC0 native read_priority_array (async)
            try:
                raw = await self._network.read_priority_array(
                    f"{address} {ot} {object_instance}"
                )
                if raw and hasattr(raw, '__iter__'):
                    for i, val in enumerate(raw, start=1):
                        if i > 16:
                            break
                        result[str(i)] = self._normalise_priority_value(val)
                    return result
            except Exception:
                pass

            # Fallback: read priorityArray property directly
            request_str = f"{address} {ot} {object_instance} priorityArray"
            raw = await self._network.read(request_str)

            if raw is None:
                return result

            if isinstance(raw, (list, tuple)):
                for i, val in enumerate(raw, start=1):
                    result[str(i)] = self._normalise_priority_value(val)
            elif hasattr(raw, '__iter__'):
                for i, val in enumerate(raw, start=1):
                    if i > 16:
                        break
                    result[str(i)] = self._normalise_priority_value(val)
            else:
                # Read each slot individually
                for i in range(1, 17):
                    try:
                        slot_req = f"{address} {ot} {object_instance} priorityArray {i}"
                        val = await self._network.read(slot_req)
                        result[str(i)] = self._normalise_priority_value(val)
                    except Exception:
                        result[str(i)] = None

        except Exception as exc:
            logger.debug("priorityArray read failed for %s %s %d: %s (may not be commandable)",
                         address, object_type, object_instance, exc)

        return result

    @staticmethod
    def _normalise_priority_value(val: Any) -> Any:
        """Convert BACpypes Null / priority-value wrapper to Python None or float/int."""
        if val is None:
            return None

        # Handle PriorityValue objects (bacpypes3)
        # These have a '_choice' attribute indicating which field is active
        if hasattr(val, '_choice'):
            choice = getattr(val, '_choice', 'null')
            if choice == 'null':
                return None
            # Get the value from the active choice field
            inner = getattr(val, choice, None)
            if inner is None:
                return None
            # Convert BACpypes enums/types to Python primitives
            inner_str = str(inner).strip()
            if inner_str.lower() in ('null', 'none', ''):
                return None
            # Try numeric conversion
            try:
                return float(inner) if '.' in inner_str else int(inner)
            except (ValueError, TypeError):
                return inner_str

        val_str = str(val).strip().lower()
        if val_str in ("null", "none", "(null)", ""):
            return None
        # Try numeric parse as fallback
        try:
            return float(val) if '.' in val_str else int(val)
        except (ValueError, TypeError):
            return val_str if val_str not in ("null", "none") else None

    # ── Alarm / Event State ────────────────────
    async def read_event_state(
        self,
        address: str,
        object_type: str,
        object_instance: int,
    ) -> str | None:
        """Read eventState property of a BACnet object.

        Returns: 'normal', 'fault', 'offnormal', 'high-limit', 'low-limit', or None.
        """
        if not self._connected or self._network is None:
            return None
        ot = _normalize_type(object_type)
        try:
            result = await self._network.read(
                f"{address} {ot} {object_instance} eventState",
            )
            if result is None:
                return None
            return str(result).strip().lower()
        except Exception:
            return None  # Object doesn't support eventState

    # ── COV (Change of Value) Subscription ─────
    _cov_callbacks: dict[str, Any] = {}  # key: "address:type:instance"

    async def subscribe_cov(
        self,
        address: str,
        object_type: str,
        object_instance: int,
        callback,
        lifetime: int = 0,  # 0 = indefinite
    ) -> bool:
        """Subscribe to COV notifications for a BACnet object.

        callback: async function(address, object_type, object_instance, value, priority_array)
        """
        if not self._connected or self._network is None:
            return False

        ot = _normalize_type(object_type)
        key = f"{address}:{ot}:{object_instance}"
        self._cov_callbacks[key] = callback

        try:
            # BAC0 supports cov subscription
            await self._network.read(
                f"{address} {ot} {object_instance} presentValue",
            )
            logger.info("COV subscribe: %s (initial read ok, will poll-on-change)", key)
            return True
        except Exception as exc:
            logger.warning("COV subscribe failed for %s: %s (will fall back to poll)", key, exc)
            return False

    async def unsubscribe_cov(
        self,
        address: str,
        object_type: str,
        object_instance: int,
    ) -> bool:
        """Unsubscribe from COV notifications."""
        ot = _normalize_type(object_type)
        key = f"{address}:{ot}:{object_instance}"
        self._cov_callbacks.pop(key, None)
        logger.info("COV unsubscribe: %s", key)
        return True

    def unsubscribe_all_cov(self):
        """Remove all COV subscriptions."""
        count = len(self._cov_callbacks)
        self._cov_callbacks.clear()
        logger.info("Unsubscribed all COV (%d)", count)

    async def read_object_properties(
        self,
        address: str,
        object_type: str,
        object_instance: int,
    ) -> dict[str, Any]:
        """Read extended BACnet properties: units, stateText, description, etc."""
        if not self._connected or self._network is None:
            raise RuntimeError("BACnet service not started")

        props: dict[str, Any] = {}
        prop_names = [
            ("units", "units"),
            ("description", "description"),
            ("stateText", "state_text"),
            ("activeText", "active_text"),
            ("inactiveText", "inactive_text"),
        ]

        ot = _normalize_type(object_type)
        for bacnet_prop, key in prop_names:
            try:
                val = await self._network.read(
                    f"{address} {ot} {object_instance} {bacnet_prop}"
                )
                if val is not None:
                    # Convert BACpypes enum to string
                    val_str = str(val)
                    if val_str.lower() not in ("none", "null", ""):
                        if bacnet_prop == "stateText" and hasattr(val, '__iter__'):
                            props[key] = [str(s) for s in val]
                        else:
                            props[key] = val_str
            except Exception:
                pass  # Property not supported by this object type

        return props

    async def read_object_list(self, address: str, device_id: int) -> list[BacnetObject]:
        """Read the object list of a device."""
        if not self._connected or self._network is None:
            raise RuntimeError("BACnet service not started")

        objects: list[BacnetObject] = []
        try:
            request_str = f"{address} device {device_id} objectList"
            obj_list = await self._network.read(request_str)

            if obj_list:
                for obj_type, obj_inst in obj_list:
                    obj_type_str = str(obj_type)
                    obj = BacnetObject(
                        object_type=obj_type_str,
                        object_instance=obj_inst,
                    )
                    try:
                        name = await self._network.read(f"{address} {obj_type_str} {obj_inst} objectName")
                        obj.object_name = str(name) if name else ""
                    except Exception:
                        pass
                    objects.append(obj)

            logger.info("Read %d objects from device %d", len(objects), device_id)
        except Exception as exc:
            logger.error("Object list read failed for device %d: %s", device_id, exc)

        return objects

    # ── write ──────────────────────────────────
    async def write_object(
        self,
        address: str,
        object_type: str,
        object_instance: int,
        value: Any,
        priority: int = 16,
    ) -> tuple[bool, str]:
        """Write a value to a BACnet object at the given priority (1–16).
        Returns (success, error_message)."""
        if not self._connected or self._network is None:
            return False, "BACnet service not started"

        try:
            ot = _normalize_type(object_type)
            request_str = f"{address} {ot} {object_instance} presentValue {value} - {priority}"
            self._network.write(request_str)
            logger.info("Write OK: %s %s %d = %s @priority %d",
                        address, object_type, object_instance, value, priority)
            return True, ""
        except Exception as exc:
            logger.error("Write failed: %s", exc)
            return False, str(exc)

    # ── release ────────────────────────────────
    async def release_priority(
        self,
        address: str,
        object_type: str,
        object_instance: int,
        priority: int,
    ) -> bool:
        """Release (write null to) a single priority level."""
        if not self._connected or self._network is None:
            raise RuntimeError("BACnet service not started")

        try:
            request_str = f"{address} {object_type} {object_instance} presentValue null - {priority}"
            self._network.write(request_str)
            logger.info("Release OK: %s %s %d @priority %d",
                        address, object_type, object_instance, priority)
            return True
        except Exception as exc:
            logger.error("Release failed @priority %d: %s", priority, exc)
            return False

    async def release_all_priorities(
        self,
        address: str,
        object_type: str,
        object_instance: int,
    ) -> dict[int, bool]:
        """Release all 16 priority levels. Returns {priority: success}."""
        results: dict[int, bool] = {}
        for pri in range(1, 17):
            ok = await self.release_priority(address, object_type, object_instance, pri)
            results[pri] = ok
        return results

    # ── helpers ────────────────────────────────
    def get_device(self, device_id: int) -> BacnetDevice | None:
        return self._devices.get(device_id)

    def get_device_address(self, device_id: int) -> str | None:
        dev = self._devices.get(device_id)
        return dev.address if dev else None

    @property
    def discovered_devices(self) -> list[BacnetDevice]:
        return list(self._devices.values())
