"""MS/TP Network Scanner — Raspberry Pi 3 + USB-RS485

Mục đích:
  - Tham gia vào MS/TP token ring như 1 master node
  - Gửi WhoIs broadcast, thu IАm responses từ tất cả devices
  - Đọc properties: objectName, vendorName, modelName, systemStatus
  - Đo round-trip time (RTT)
  - Trả về dict {node_address: NodeInfo}

Dùng:
  scanner = MstpScanner.from_config("config.yaml")
  results = await scanner.scan()

Yêu cầu:
  - USB-RS485 adapter → /dev/ttyUSB0
  - Baud phải trùng với network (default 38400)
  - Node address 127 (hoặc bất kỳ số chưa ai dùng)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class NodeInfo:
    address: int
    name: str = ""
    vendor: str = ""
    model: str = ""
    firmware: str = ""
    system_status: str = ""
    rtt_ms: float = -1.0
    last_seen: float = 0.0
    read_errors: int = 0
    online: bool = False
    object_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ── Scanner ───────────────────────────────────────────────────────────────────

class MstpScanner:
    """BACnet MS/TP bus scanner.

    Joins the token ring as a master node, broadcasts WhoIs, collects IАm
    responses and reads device properties from each responding node.
    """

    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 38400,
        node_address: int = 127,
        timeout: float = 10.0,
        node_range: tuple[int, int] = (0, 127),
        timeout_per_node: float = 1.5,
    ):
        self.port = port
        self.baudrate = baudrate
        self.node_address = node_address
        self.timeout = timeout
        self.node_range = node_range
        self.timeout_per_node = timeout_per_node
        self._bacnet: Any = None

    @classmethod
    def from_config(cls, config_path: str = "config.yaml") -> "MstpScanner":
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        serial = cfg.get("serial", {})
        scan   = cfg.get("scan", {})
        nr = scan.get("node_range", [0, 127])
        return cls(
            port=serial.get("port", "/dev/ttyUSB0"),
            baudrate=serial.get("baudrate", 38400),
            node_address=serial.get("node_address", 127),
            timeout=serial.get("timeout", 10),
            node_range=(nr[0], nr[1]),
            timeout_per_node=scan.get("timeout_per_node", 1.5),
        )

    # ── BAC0 lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialise BAC0 on the MS/TP port and join the token ring."""
        import BAC0
        logger.info("[Scanner] Starting BAC0 on %s @ %d baud (node=%d)",
                    self.port, self.baudrate, self.node_address)
        try:
            # BAC0 accepts 'port' for MS/TP and 'mstpAddress' for node id
            # Use UDP 47809 to avoid conflict with main gateway on 47808
            self._bacnet = BAC0.lite(
                port=self.port,
                baudrate=self.baudrate,
                mstpAddress=self.node_address,
                ip="0.0.0.0/24:47809",
            )
            # Allow 3s for token ring join before first scan
            await asyncio.sleep(3)
            logger.info("[Scanner] BAC0 started — joined token ring")
        except Exception as exc:
            logger.error("[Scanner] BAC0 start failed: %s", exc)
            raise

    async def stop(self) -> None:
        if self._bacnet:
            try:
                self._bacnet.disconnect()
            except Exception:
                pass
            self._bacnet = None
            logger.info("[Scanner] BAC0 disconnected")

    # ── Scan ───────────────────────────────────────────────────────────────

    async def scan(self) -> dict[int, NodeInfo]:
        """Perform a full bus scan.

        Returns: {node_address: NodeInfo}
        """
        if self._bacnet is None:
            await self.start()

        logger.info("[Scanner] Starting scan (node range %d–%d) ...", *self.node_range)
        t_start = time.perf_counter()

        # Step 1: WhoIs broadcast — BAC0 returns list of (name, address) tuples
        discovered: dict[int, NodeInfo] = {}
        try:
            devices = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._bacnet.discover(limits=self.node_range)
            )
            now = time.time()
            for entry in (devices or []):
                # BAC0 returns [(name, address_str), ...]
                # address_str for MS/TP: "8700:5" (network:node)
                try:
                    name, addr_str = str(entry[0]), str(entry[1])
                    # Extract MS/TP node number (last segment after ':')
                    node_id = int(addr_str.split(":")[-1]) if ":" in addr_str else int(addr_str)
                    node = NodeInfo(address=node_id, name=name, last_seen=now, online=True)
                    discovered[node_id] = node
                    logger.info("[Scanner] Found node %d → %s", node_id, name)
                except Exception as pe:
                    logger.debug("[Scanner] Parse error for %s: %s", entry, pe)
        except Exception as exc:
            logger.error("[Scanner] WhoIs failed: %s", exc)

        # Step 2: Read extended properties per discovered node
        for node_id, node in discovered.items():
            await self._read_node_properties(node)

        elapsed = time.perf_counter() - t_start
        logger.info("[Scanner] Scan complete: %d nodes in %.1fs", len(discovered), elapsed)
        return discovered

    async def _read_node_properties(self, node: NodeInfo) -> None:
        """Read extended device properties and measure RTT for a single node."""
        if not self._bacnet:
            return

        # Try to get the BAC0 device wrapper
        dev_key = None
        for key in self._bacnet.devices:
            # BAC0 device keys: (name, address) or just address
            if str(node.address) in str(key):
                dev_key = key
                break

        if dev_key is None:
            logger.debug("[Scanner] No device wrapper found for node %d", node.address)
            return

        props = [
            ("vendorName",                 "vendor"),
            ("modelName",                  "model"),
            ("applicationSoftwareVersion", "firmware"),
            ("systemStatus",               "system_status"),
        ]

        for bacnet_prop, field_name in props:
            t0 = time.perf_counter()
            try:
                val = await asyncio.get_event_loop().run_in_executor(
                    None, lambda p=bacnet_prop: self._bacnet[dev_key][p].value
                )
                rtt = (time.perf_counter() - t0) * 1000
                if val is not None:
                    setattr(node, field_name, str(val).strip())
                if field_name == "vendor":   # Use first successful read as RTT sample
                    node.rtt_ms = round(rtt, 1)
            except Exception as exc:
                logger.debug("[Scanner] Node %d prop %s: %s", node.address, bacnet_prop, exc)
                node.read_errors += 1

        # Try to count objects
        try:
            obj_list = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._bacnet[dev_key]["objectList"].value
            )
            node.object_count = len(obj_list) if obj_list else 0
        except Exception:
            pass

    # ── Convenience: context manager ──────────────────────────────────────

    async def __aenter__(self) -> "MstpScanner":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()


# ── CLI entrypoint ────────────────────────────────────────────────────────────

async def _cli_main() -> None:
    import argparse, json

    parser = argparse.ArgumentParser(description="BACnet MS/TP Network Scanner")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--port",          help="Override serial port")
    parser.add_argument("--baud",   type=int, help="Override baud rate")
    parser.add_argument("--json",   action="store_true", help="Output as JSON")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    scanner = MstpScanner.from_config(args.config)
    if args.port:
        scanner.port = args.port
    if args.baud:
        scanner.baudrate = args.baud

    async with scanner:
        results = await scanner.scan()

    if args.json:
        print(json.dumps({str(k): v.to_dict() for k, v in results.items()}, indent=2))
    else:
        print(f"\n{'─'*60}")
        print(f"  MS/TP Scan Results — {len(results)} devices found")
        print(f"{'─'*60}")
        if not results:
            print("  (no devices responded)")
        for addr, node in sorted(results.items()):
            status_icon = "🟢" if node.online else "🔴"
            print(f"  {status_icon} Node {addr:3d}  {node.name or '(unknown)'}")
            print(f"         Vendor: {node.vendor or '?'}  Model: {node.model or '?'}")
            print(f"         RTT: {node.rtt_ms:.1f}ms  Objects: {node.object_count}")
        print(f"{'─'*60}\n")


if __name__ == "__main__":
    asyncio.run(_cli_main())
