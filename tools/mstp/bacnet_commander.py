"""BACnet Commander — Send ReadProperty/WriteProperty/ReinitializeDevice via MS/TP

Runs MstpMaster in a background thread, pausing the sniffer as needed.
Used by dashboard API endpoints for Phase 4: Active Diagnostics.
"""

from __future__ import annotations

import logging
import time
import threading
from typing import Any

from mstp_master import (
    MstpMaster, MstpFrame, FT, build_frame,
    build_read_property, build_write_property, build_reinitialize_device,
    parse_read_property_ack, OBJ_TYPES, PROP_IDS,
)

logger = logging.getLogger(__name__)


def execute_command(
    port: str,
    baudrate: int,
    target_mac: int,
    command: str,
    my_mac: int = 127,
    timeout: float = 15.0,
    **kwargs,
) -> dict:
    """Execute a single BACnet command via MS/TP master.
    
    command: 'read', 'write', 'reinit'
    Returns: dict with 'status', 'value' or 'error'
    
    This is BLOCKING and takes exclusive control of the serial port.
    """
    result = {"status": "error", "command": command, "target_mac": target_mac}
    
    # Resolve object type
    obj_type = kwargs.get("obj_type", 0)
    if isinstance(obj_type, str):
        obj_type = OBJ_TYPES.get(obj_type, int(obj_type))
    obj_instance = kwargs.get("obj_instance", 0)
    prop_id = kwargs.get("prop_id", 85)  # default: presentValue
    if isinstance(prop_id, str):
        prop_id = PROP_IDS.get(prop_id, int(prop_id))
    device_instance = kwargs.get("device_instance", 0)
    
    master = MstpMaster(port, baudrate, my_mac)
    responses = []
    
    def on_event(event, data):
        if event == 'joined':
            logger.info("[Commander] Joined token ring")
            # Queue the command
            if command == 'read':
                master.queue_read_property(
                    target_mac, device_instance, obj_type, obj_instance, prop_id
                )
            elif command == 'write':
                value = kwargs.get("value")
                priority = kwargs.get("priority")
                data_bytes = build_write_property(
                    device_instance, obj_type, obj_instance, prop_id,
                    value, priority
                )
                master._pending_request = data_bytes
                master._pending_dst = target_mac
                master._pending_expects_reply = True
            elif command == 'reinit':
                state = kwargs.get("reinit_state", 1)  # 1=warmstart
                password = kwargs.get("password", "")
                data_bytes = build_reinitialize_device(state, password)
                master._pending_request = data_bytes
                master._pending_dst = target_mac
                master._pending_expects_reply = True

        elif event == 'reply':
            responses.append(data)
            
        elif event == 'token':
            tc = data['count']
            # Retry command if no response after a few tokens
            if not responses and tc in (5, 10):
                if command == 'read':
                    master.queue_read_property(
                        target_mac, device_instance, obj_type, obj_instance, prop_id
                    )

    try:
        master.run(duration=timeout, callback=on_event)
    except Exception as exc:
        result["error"] = str(exc)
        logger.error("[Commander] Error: %s", exc)
        return result
    
    if responses:
        resp = responses[0]
        result["status"] = "ok"
        if "value" in resp:
            result["value"] = resp["value"]
        if "property" in resp:
            result["property"] = resp["property"]
        if "value_raw" in resp:
            result["value_raw"] = resp["value_raw"]
    elif command == 'write' or command == 'reinit':
        # WriteProperty/Reinit may return SimpleAck (no parsed reply)
        result["status"] = "ok"
        result["note"] = "Command sent (no ACK parsed)"
    else:
        result["error"] = f"No response from MAC {target_mac} within {timeout}s"
    
    return result


class CommandRunner:
    """Thread-safe command runner."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._result: dict | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def result(self) -> dict | None:
        return self._result

    def start(self, port: str, baudrate: int, target_mac: int,
              command: str, my_mac: int = 127, **kwargs) -> None:
        if self.is_running:
            raise RuntimeError("Command already running")
        self._result = None

        def _run():
            self._result = execute_command(
                port, baudrate, target_mac, command, my_mac, **kwargs
            )

        self._thread = threading.Thread(target=_run, daemon=True, name="commander")
        self._thread.start()

    def wait(self, timeout: float = 30.0) -> dict | None:
        if self._thread:
            self._thread.join(timeout=timeout)
        return self._result
