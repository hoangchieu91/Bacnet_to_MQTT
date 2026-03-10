"""BACnet File Transfer — AtomicWriteFile / AtomicReadFile

Ghi file (application, config) lên BACnet device qua chuẩn ASHRAE 135.
Hỗ trợ cả BACnet/IP (địa chỉ IP) và BACnet MS/TP (qua router).

Áp dụng cho:
  - Distech Controls CPO-RL4, CPO-PC6A (application file upload)
  - Bất kỳ device BACnet nào hỗ trợ File Object + AtomicWriteFile

Giới hạn:
  - Firmware low-level (binary OTA) cần ECY Configurator / Niagara N4
  - Tool này chỉ ghi vào File Object (standard BACnet, RFC không giới hạn)
  - Device phải có File Object tồn tại; nếu không có → WriteFile sẽ fail

Dùng:
  python3 bacnet_file_transfer.py upload \\
      --address 192.168.20.50 \\
      --file-instance 1 \\
      --path /path/to/application.app

  python3 bacnet_file_transfer.py download \\
      --address 192.168.20.50 \\
      --file-instance 1 \\
      --output /tmp/downloaded.app
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
DEFAULT_CHUNK_SIZE   = 128    # bytes per AtomicWriteFile chunk (safe for MS/TP)
IP_CHUNK_SIZE        = 512    # lớn hơn cho BACnet/IP
MAX_RETRIES          = 3
RETRY_DELAY_S        = 1.0


# ── Progress dataclass ────────────────────────────────────────────────────────

@dataclass
class TransferProgress:
    file_path: str
    file_size: int
    bytes_transferred: int = 0
    chunks_done: int = 0
    chunks_total: int = 0
    status: str = "pending"      # pending | active | done | failed
    error: str = ""
    start_time: float = field(default_factory=time.time)

    @property
    def pct(self) -> float:
        if self.file_size == 0:
            return 100.0
        return min(self.bytes_transferred / self.file_size * 100, 100.0)

    @property
    def elapsed_s(self) -> float:
        return time.time() - self.start_time

    @property
    def kbps(self) -> float:
        elapsed = self.elapsed_s
        if elapsed < 0.01:
            return 0.0
        return (self.bytes_transferred / 1024) / elapsed

    def to_dict(self) -> dict:
        return {
            "file_path":          self.file_path,
            "file_size":          self.file_size,
            "bytes_transferred":  self.bytes_transferred,
            "chunks_done":        self.chunks_done,
            "chunks_total":       self.chunks_total,
            "pct":                round(self.pct, 1),
            "status":             self.status,
            "error":              self.error,
            "elapsed_s":          round(self.elapsed_s, 1),
            "kbps":               round(self.kbps, 1),
        }


# ── File Transfer Engine ──────────────────────────────────────────────────────

class BacnetFileTransfer:
    """Upload / download files via standard BACnet AtomicWriteFile / AtomicReadFile.

    Requires BAC0 instance already started (can be the same as scanner/bridge).

    Chunk size:
      - MS/TP: 128 bytes (max APDU after routing overhead)
      - BACnet/IP: 512 bytes (or up to device's maxApduLength)
    """

    def __init__(
        self,
        bacnet: object | None = None,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 38400,
        node_address: int = 127,
        progress_cb: object = None,   # Callable[[TransferProgress], None]
    ):
        self._bacnet_arg = bacnet
        self._port        = port
        self._baudrate    = baudrate
        self._node_address = node_address
        self._progress_cb  = progress_cb
        self._bacnet: object | None = bacnet
        self._owned_bacnet = False

    @classmethod
    def from_config(cls, config_path: str = "config.yaml", bacnet=None, **kw) -> "BacnetFileTransfer":
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        s = cfg.get("serial", {})
        return cls(
            bacnet=bacnet,
            port=s.get("port", "/dev/ttyUSB0"),
            baudrate=s.get("baudrate", 38400),
            node_address=s.get("node_address", 127),
            **kw,
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def _ensure_bacnet(self) -> None:
        if self._bacnet:
            return
        import BAC0
        loop = asyncio.get_event_loop()
        logger.info("[FileXfer] Starting BAC0 on %s @ %d", self._port, self._baudrate)
        self._bacnet = await loop.run_in_executor(
            None,
            lambda: BAC0.lite(
                port=self._port,
                baudrate=self._baudrate,
                mstpAddress=self._node_address,
            )
        )
        self._owned_bacnet = True
        await asyncio.sleep(3)   # token ring join

    async def close(self) -> None:
        if self._owned_bacnet and self._bacnet:
            try:
                self._bacnet.disconnect()
            except Exception:
                pass
            self._bacnet = None

    # ── Helper: chunk data ─────────────────────────────────────────────────

    @staticmethod
    def _is_ip(address: str) -> bool:
        return "." in address or address.startswith("[")

    def _chunk_size(self, address: str) -> int:
        return IP_CHUNK_SIZE if self._is_ip(address) else DEFAULT_CHUNK_SIZE

    # ── Upload (AtomicWriteFile) ───────────────────────────────────────────

    async def upload(
        self,
        address: str,
        file_path: str | Path,
        file_object_instance: int = 1,
        start_position: int = 0,
    ) -> TransferProgress:
        """Write a local file to a BACnet File Object on the target device.

        Args:
            address:               Device address (IP or MS/TP network:node)
            file_path:             Local file to upload
            file_object_instance:  BACnet File Object instance number
            start_position:        Byte offset to start writing (0 = overwrite)
        Returns:
            TransferProgress with final status
        """
        await self._ensure_bacnet()
        file_path = Path(file_path)
        file_size = file_path.stat().st_size
        chunk_size = self._chunk_size(address)
        chunks_total = max(1, math.ceil(file_size / chunk_size))

        prog = TransferProgress(
            file_path=str(file_path),
            file_size=file_size,
            chunks_total=chunks_total,
            status="active",
        )
        self._notify(prog)

        logger.info("[FileXfer] Upload %s → device %s FileObj:%d  size=%d chunk=%d",
                    file_path.name, address, file_object_instance, file_size, chunk_size)

        with open(file_path, "rb") as fh:
            position = start_position
            loop = asyncio.get_event_loop()

            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break

                # AtomicWriteFile: streamAccess, fileStartPosition, fileData
                success = False
                for attempt in range(MAX_RETRIES):
                    try:
                        await loop.run_in_executor(
                            None,
                            lambda p=position, c=chunk: self._write_file_chunk(
                                address, file_object_instance, p, c
                            )
                        )
                        success = True
                        break
                    except Exception as exc:
                        logger.warning("[FileXfer] Chunk @%d attempt %d/%d failed: %s",
                                       position, attempt+1, MAX_RETRIES, exc)
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_DELAY_S)

                if not success:
                    prog.status = "failed"
                    prog.error  = f"Chunk @{position} failed after {MAX_RETRIES} retries"
                    self._notify(prog)
                    return prog

                position += len(chunk)
                prog.bytes_transferred = position
                prog.chunks_done += 1
                self._notify(prog)
                logger.debug("[FileXfer] ↑ %d/%d bytes (%.1f%%)",
                             position, file_size, prog.pct)

        prog.status = "done"
        logger.info("[FileXfer] Upload complete: %d bytes in %.1fs (%.1f KB/s)",
                    file_size, prog.elapsed_s, prog.kbps)
        self._notify(prog)
        return prog

    def _write_file_chunk(
        self, address: str, instance: int, position: int, data: bytes
    ) -> None:
        """Synchronous: build and send AtomicWriteFile APDU via BAC0."""
        # BAC0 wraps bacpypes3. We construct the request via network.write
        # AtomicWriteFile format: "addr file,instance streamAccess position data"
        # BAC0 lite does not expose AtomicWriteFile directly → use raw bacpypes3
        from bacpypes3.apdu import AtomicWriteFileRequest
        from bacpypes3.basetypes import FileAccessMethod
        from bacpypes3.primitivedata import OctetString, Integer
        from bacpypes3.constructeddata import SequenceOf

        # Construct request
        request = AtomicWriteFileRequest(
            fileIdentifier=("file", instance),
            accessMethod=FileAccessMethod(
                streamAccess={
                    "fileStartPosition": Integer(position),
                    "fileData": OctetString(data),
                }
            ),
        )
        request.pduDestination = self._parse_address(address)

        # Send via BAC0 bacpypes3 network
        net = getattr(self._bacnet, "_network", None) or \
              getattr(self._bacnet, "this_application", None)
        if net is None:
            raise RuntimeError("BAC0 network not accessible")

        # Using BAC0's internal request mechanism
        # (bacpypes3 AppController handles PDU dispatch)
        import asyncio as _as
        future = asyncio.run_coroutine_threadsafe(
            net.request(request), _as.get_event_loop()
        )
        future.result(timeout=10)

    def _parse_address(self, address: str):
        """Convert string address to BACpypes3 Address object."""
        from bacpypes3.pdu import Address
        return Address(address)

    # ── Download (AtomicReadFile) ──────────────────────────────────────────

    async def download(
        self,
        address: str,
        file_object_instance: int,
        output_path: str | Path,
        chunk_size: int | None = None,
    ) -> TransferProgress:
        """Read a File Object from a BACnet device to a local file."""
        await self._ensure_bacnet()
        output_path = Path(output_path)
        cs = chunk_size or self._chunk_size(address)

        # First: read file size from File Object property "fileSize"
        loop = asyncio.get_event_loop()
        try:
            file_size = await loop.run_in_executor(
                None,
                lambda: self._bacnet.read(
                    f"{address} file {file_object_instance} fileSize"
                )
            )
            file_size = int(file_size)
        except Exception:
            file_size = -1  # Unknown size

        chunks_total = max(1, math.ceil(file_size / cs)) if file_size > 0 else 0
        prog = TransferProgress(
            file_path=str(output_path),
            file_size=file_size,
            chunks_total=chunks_total,
            status="active",
        )
        self._notify(prog)

        logger.info("[FileXfer] Download device %s FileObj:%d → %s",
                    address, file_object_instance, output_path.name)

        with open(output_path, "wb") as fh:
            position = 0
            while True:
                try:
                    chunk_data = await loop.run_in_executor(
                        None,
                        lambda p=position: self._read_file_chunk(
                            address, file_object_instance, p, cs
                        )
                    )
                except Exception as exc:
                    prog.status = "failed"
                    prog.error  = str(exc)
                    self._notify(prog)
                    return prog

                if not chunk_data:
                    break
                fh.write(chunk_data)
                position += len(chunk_data)
                prog.bytes_transferred = position
                prog.chunks_done += 1
                self._notify(prog)

                if len(chunk_data) < cs:
                    break  # Last chunk — file end

        prog.status = "done"
        self._notify(prog)
        return prog

    def _read_file_chunk(self, address: str, instance: int, position: int, size: int) -> bytes:
        from bacpypes3.apdu import AtomicReadFileRequest
        from bacpypes3.basetypes import FileAccessMethod
        from bacpypes3.primitivedata import Integer

        request = AtomicReadFileRequest(
            fileIdentifier=("file", instance),
            accessMethod=FileAccessMethod(
                streamAccess={
                    "fileStartPosition": Integer(position),
                    "requestedOctetCount": Integer(size),
                }
            )
        )
        request.pduDestination = self._parse_address(address)
        net = getattr(self._bacnet, "_network", None) or \
              getattr(self._bacnet, "this_application", None)
        future = asyncio.run_coroutine_threadsafe(
            net.request(request), asyncio.get_event_loop()
        )
        response = future.result(timeout=10)
        return bytes(response.accessMethod.streamAccess["fileData"])

    # ── Reload trigger ─────────────────────────────────────────────────────

    async def trigger_reload(
        self, address: str, device_instance: int, reload_property: str | None = None
    ) -> bool:
        """Send WriteProperty to trigger device application reload after upload.

        Distech Controls CPO: write programChange or reinitializeDevice.
        Default: send ReinitializeDevice (standard BACnet service).
        """
        loop = asyncio.get_event_loop()
        try:
            if reload_property:
                # Write to a specific property (device-specific)
                await loop.run_in_executor(
                    None,
                    lambda: self._bacnet.write(
                        f"{address} device {device_instance} {reload_property} = 1"
                    )
                )
            else:
                # Standard: ReinitializeDevice with warmStart
                await loop.run_in_executor(
                    None,
                    lambda: self._bacnet.write(
                        f"{address} device {device_instance} reinitializeDevice = warmStart"
                    )
                )
            logger.info("[FileXfer] Reload triggered on device %d @ %s", device_instance, address)
            return True
        except Exception as exc:
            logger.error("[FileXfer] Reload failed: %s", exc)
            return False

    # ── Progress callback ──────────────────────────────────────────────────

    def _notify(self, prog: TransferProgress) -> None:
        if self._progress_cb:
            try:
                self._progress_cb(prog)
            except Exception:
                pass


# ── FastAPI upload endpoint helpers ──────────────────────────────────────────

async def handle_upload_request(
    address: str,
    file_instance: int,
    file_bytes: bytes,
    filename: str,
    bacnet=None,
    config_path: str = "config.yaml",
    progress_cb=None,
) -> TransferProgress:
    """Helper for dashboard: receive raw file bytes and upload to device."""
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    xfer = BacnetFileTransfer.from_config(
        config_path, bacnet=bacnet, progress_cb=progress_cb
    )
    try:
        result = await xfer.upload(address, tmp_path, file_object_instance=file_instance)
    finally:
        os.unlink(tmp_path)
        if xfer._owned_bacnet:
            await xfer.close()
    return result


# ── CLI entrypoint ────────────────────────────────────────────────────────────

async def _cli_main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="BACnet File Transfer (AtomicWriteFile/ReadFile)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("upload", help="Upload file to BACnet device")
    up.add_argument("--address", required=True, help="Device address (IP or net:node)")
    up.add_argument("--file-instance", type=int, default=1, help="File object instance")
    up.add_argument("--path", required=True, help="Local file to upload")
    up.add_argument("--config", default="config.yaml")
    up.add_argument("--reload", action="store_true", help="Send ReinitializeDevice after upload")
    up.add_argument("--device-instance", type=int, default=0)

    dn = sub.add_parser("download", help="Download file from BACnet device")
    dn.add_argument("--address", required=True)
    dn.add_argument("--file-instance", type=int, default=1)
    dn.add_argument("--output", required=True, help="Local output path")
    dn.add_argument("--config", default="config.yaml")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    def _print_progress(prog: TransferProgress) -> None:
        bar_len = 30
        filled  = int(bar_len * prog.pct / 100)
        bar     = "█" * filled + "░" * (bar_len - filled)
        print(f"\r  [{bar}] {prog.pct:5.1f}%  {prog.kbps:.1f} KB/s", end="", flush=True)

    xfer = BacnetFileTransfer.from_config(args.config, progress_cb=_print_progress)

    if args.cmd == "upload":
        print(f"\n📤 Uploading {args.path} → {args.address} / File:{args.file_instance}")
        prog = await xfer.upload(args.address, args.path, args.file_instance)
        print()
        if prog.status == "done":
            print(f"  ✅ Done: {prog.bytes_transferred} bytes in {prog.elapsed_s:.1f}s ({prog.kbps:.1f} KB/s)")
            if getattr(args, "reload", False) and args.device_instance:
                print(f"  🔄 Sending ReinitializeDevice to device {args.device_instance}...")
                ok = await xfer.trigger_reload(args.address, args.device_instance)
                print(f"  {'✅ Sent' if ok else '❌ Failed'}")
        else:
            print(f"  ❌ Failed: {prog.error}")

    elif args.cmd == "download":
        print(f"\n📥 Downloading {args.address}/File:{args.file_instance} → {args.output}")
        prog = await xfer.download(args.address, args.file_instance, args.output)
        print()
        if prog.status == "done":
            print(f"  ✅ Done: {prog.bytes_transferred} bytes in {prog.elapsed_s:.1f}s")
        else:
            print(f"  ❌ Failed: {prog.error}")

    await xfer.close()


if __name__ == "__main__":
    try:
        asyncio.run(_cli_main())
    except KeyboardInterrupt:
        print("\nCancelled.")
