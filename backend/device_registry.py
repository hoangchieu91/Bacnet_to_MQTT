"""
DeviceRegistry — Persistent BACnet Device Store

Saves discovered devices to data/device_registry.json so they survive restarts.
- Called by BacnetService after each discovery
- Loaded at startup by main.py and passed to bacnet_service
- Stores: device_id, name, address, vendor, network_id, and discovered objects list

The registry is the single source of truth for "known devices".
The in-memory discovered_devices list is still authoritative for LIVE status.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path("data/device_registry.json")


class DeviceRegistry:
    """
    Persistent registry of BACnet devices discovered over time.

    Schema of registry.json:
    {
        "devices": {
            "10121": {
                "device_id": 10121,
                "device_name": "FCU-01",
                "address": "192.168.1.101",
                "vendor_name": "...",
                "model_name": "...",
                "network_id": "IP",
                "last_seen": "2026-03-07T...",
                "objects": [
                    {"object_type": "analogInput", "object_instance": 0, "object_name": "..."},
                    ...
                ]
            }
        }
    }
    """

    def __init__(self, path: Path | str = DEFAULT_PATH):
        self._path = Path(path)
        self._data: dict[int, dict] = {}
        self._load()

    # ─────────────────────────────────────────────
    # Load / Save
    # ─────────────────────────────────────────────
    def _load(self) -> None:
        if not self._path.exists():
            logger.info(f"DeviceRegistry: no registry at {self._path}, starting fresh")
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
            devices = raw.get("devices", {})
            self._data = {int(k): v for k, v in devices.items()}
            logger.info(f"DeviceRegistry: loaded {len(self._data)} devices from {self._path}")
        except Exception as e:
            logger.error(f"DeviceRegistry: load failed: {e}")

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"devices": {str(k): v for k, v in self._data.items()}}, f, indent=2, default=str)
            tmp.replace(self._path)
        except Exception as e:
            logger.error(f"DeviceRegistry: save failed: {e}")

    # ─────────────────────────────────────────────
    # Write
    # ─────────────────────────────────────────────
    def upsert_device(self, device_id: int, **fields) -> None:
        """Insert or update device metadata."""
        existing = self._data.get(device_id, {"device_id": device_id})
        existing.update(fields)
        existing["last_seen"] = datetime.now(timezone.utc).isoformat()
        self._data[device_id] = existing
        self._save()

    def upsert_objects(self, device_id: int, objects: list[dict]) -> None:
        """Update the objects list for a device (replace entirely)."""
        if device_id not in self._data:
            self._data[device_id] = {"device_id": device_id}
        self._data[device_id]["objects"] = objects
        self._data[device_id]["last_seen"] = datetime.now(timezone.utc).isoformat()
        self._save()

    def remove_device(self, device_id: int) -> bool:
        if device_id in self._data:
            del self._data[device_id]
            self._save()
            return True
        return False

    # ─────────────────────────────────────────────
    # Read
    # ─────────────────────────────────────────────
    def all_devices(self) -> list[dict]:
        """Return all registered devices (sorted by device_id)."""
        return sorted(self._data.values(), key=lambda d: d.get("device_id", 0))

    def get_device(self, device_id: int) -> dict | None:
        return self._data.get(device_id)

    def get_objects(self, device_id: int) -> list[dict]:
        return self._data.get(device_id, {}).get("objects", [])

    def total(self) -> int:
        return len(self._data)
