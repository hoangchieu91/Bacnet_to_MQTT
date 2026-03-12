"""Modbus RTU Register Scanner — Active polling tool

Dùng để dò thiết bị Modbus khi KHÔNG có tài liệu:
  • Scan Slave ID: tìm thiết bị trên bus (1-247)
  • Scan Registers: quét từng thanh ghi, tìm vùng hợp lệ
  • Read Values: đọc giá trị, hiện đa định dạng

⚠️  CHÚ Ý: Tool này GỬI dữ liệu ra bus (active mode).
    Chỉ dùng khi Pi kết nối trực tiếp với thiết bị,
    KHÔNG dùng khi có master khác đang poll.

Usage (standalone):
  python3 modbus_scanner.py --port /dev/ttyUSB0 --baud 9600 --slave 1 --start 0 --count 100
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Optional

import serial

logger = logging.getLogger(__name__)


# ── CRC-16 ────────────────────────────────────────────────────────────────────

def _build_crc16_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        table.append(crc)
    return table

_CRC_TABLE = _build_crc16_table()

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc = (crc >> 8) ^ _CRC_TABLE[(crc ^ b) & 0xFF]
    return crc

def build_request(slave: int, fc: int, start_reg: int, count: int) -> bytes:
    """Build a Modbus RTU request frame."""
    pdu = struct.pack(">BBHH", slave, fc, start_reg, count)
    c = crc16(pdu)
    return pdu + struct.pack("<H", c)


# ── Data types ────────────────────────────────────────────────────────────────

FC_NAMES = {3: "Holding Registers", 4: "Input Registers"}

def decode_registers(raw_regs: list[int], byte_order: str = "AB_CD") -> list[dict]:
    """Decode a list of 16-bit register values into multiple formats."""

    def make_u32(r0, r1, order):
        if order == "AB_CD": return (r0 << 16) | r1
        if order == "CD_AB": return (r1 << 16) | r0
        if order == "BA_DC":
            return (((r0 & 0xFF) << 8 | (r0 >> 8)) << 16) | ((r1 & 0xFF) << 8 | (r1 >> 8))
        if order == "DC_BA":
            return (((r1 & 0xFF) << 8 | (r1 >> 8)) << 16) | ((r0 & 0xFF) << 8 | (r0 >> 8))
        return (r0 << 16) | r1

    def to_f32(u32):
        try: return round(struct.unpack(">f", struct.pack(">I", u32))[0], 6)
        except: return None

    def to_f64(u64):
        try: return round(struct.unpack(">d", struct.pack(">Q", u64))[0], 10)
        except: return None

    result = []
    for idx, val in enumerate(raw_regs):
        entry = {
            "reg": idx,
            "raw_hex": f"{val:04X}",
            "uint16": val,
            "int16": val if val < 0x8000 else val - 0x10000,
        }
        if idx < len(raw_regs) - 1:
            r0, r1 = raw_regs[idx], raw_regs[idx+1]
            u32 = make_u32(r0, r1, byte_order)
            entry["uint32"] = u32
            entry["int32"] = u32 if u32 < 0x80000000 else u32 - 0x100000000
            entry["float32"] = to_f32(u32)
            # Inverted
            u32_inv = make_u32(r1, r0, byte_order)
            entry["float32_inv"] = to_f32(u32_inv)

        if idx < len(raw_regs) - 3:
            r0, r1, r2, r3 = raw_regs[idx:idx+4]
            if byte_order == "AB_CD":
                u64 = (r0 << 48) | (r1 << 32) | (r2 << 16) | r3
            elif byte_order == "CD_AB":
                u64 = (r3 << 48) | (r2 << 32) | (r1 << 16) | r0
            else:
                u64 = (r0 << 48) | (r1 << 32) | (r2 << 16) | r3
            entry["float64"] = to_f64(u64)

        result.append(entry)
    return result


# ── Scanner Engine ────────────────────────────────────────────────────────────

@dataclass
class ScanResult:
    slave_id: int
    fc: int
    start_reg: int
    registers: list[dict] = field(default_factory=list)
    raw_values: list[int] = field(default_factory=list)
    error: str = ""
    response_ms: float = 0


class ModbusScanner:
    """Active Modbus RTU scanner — sends requests and reads responses."""

    def __init__(self, port: str, baudrate: int = 9600, timeout_ms: float = 500,
                 parity: str = "N", stopbits: int = 1):
        self.port = port
        self.baudrate = baudrate
        self.timeout_ms = timeout_ms
        self.parity = parity
        self.stopbits = stopbits
        self._ser: serial.Serial | None = None
        self._lock = asyncio.Lock()

        # Calculate inter-frame delay (3.5 chars)
        char_time = 11.0 / baudrate  # 11 bits per char
        self._t35 = max(char_time * 3.5, 0.00175)  # min 1.75ms

    def _open(self) -> serial.Serial:
        """Open serial port."""
        if self._ser and self._ser.is_open:
            return self._ser
        parity_map = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}
        self._ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            parity=parity_map.get(self.parity, serial.PARITY_NONE),
            stopbits=self.stopbits,
            timeout=self.timeout_ms / 1000,
        )
        return self._ser

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
            self._ser = None

    async def read_registers(self, slave_id: int, fc: int, start_reg: int,
                             count: int, byte_order: str = "AB_CD") -> ScanResult:
        """Read registers from a slave. Returns ScanResult."""
        async with self._lock:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._read_sync, slave_id, fc, start_reg, count, byte_order)

    def _read_sync(self, slave_id: int, fc: int, start_reg: int,
                   count: int, byte_order: str) -> ScanResult:
        result = ScanResult(slave_id=slave_id, fc=fc, start_reg=start_reg)
        try:
            ser = self._open()
            ser.reset_input_buffer()

            # Build and send request
            req = build_request(slave_id, fc, start_reg, count)
            time.sleep(self._t35)  # Inter-frame gap
            ser.write(req)
            ser.flush()

            # Read response
            t0 = time.perf_counter()
            # Wait for header: slave(1) + fc(1) + byte_count(1) = 3 bytes
            header = ser.read(3)
            if len(header) < 3:
                result.error = "TIMEOUT"
                result.response_ms = (time.perf_counter() - t0) * 1000
                return result

            resp_slave, resp_fc, byte_count = header[0], header[1], header[2]

            # Check for exception response
            if resp_fc & 0x80:
                exc_code = byte_count  # In exception, 3rd byte is exception code
                EXC_NAMES = {1: "Illegal Function", 2: "Illegal Data Address",
                             3: "Illegal Data Value", 4: "Server Device Failure"}
                result.error = f"EXCEPTION: {EXC_NAMES.get(exc_code, f'0x{exc_code:02X}')}"
                # Read remaining CRC
                ser.read(2)
                result.response_ms = (time.perf_counter() - t0) * 1000
                return result

            # Read data + CRC
            remaining = byte_count + 2  # data + CRC(2)
            data = ser.read(remaining)
            result.response_ms = (time.perf_counter() - t0) * 1000

            if len(data) < remaining:
                result.error = f"SHORT_RESPONSE ({3+len(data)}/{3+remaining} bytes)"
                return result

            # Verify CRC
            full_resp = header + data
            payload_crc = crc16(full_resp[:-2])
            recv_crc = full_resp[-2] | (full_resp[-1] << 8)
            if payload_crc != recv_crc:
                result.error = "CRC_ERROR"
                return result

            # Parse register values
            reg_data = data[:-2]  # Remove CRC
            raw_values = []
            for i in range(0, len(reg_data) - 1, 2):
                raw_values.append((reg_data[i] << 8) | reg_data[i+1])

            result.raw_values = raw_values
            result.registers = decode_registers(raw_values, byte_order)
            # Update register numbers to be absolute
            for r in result.registers:
                r["reg"] = start_reg + r["reg"]

        except serial.SerialException as e:
            result.error = f"SERIAL_ERROR: {e}"
        except Exception as e:
            result.error = f"ERROR: {e}"

        return result

    async def scan_slaves(self, fc: int = 3, test_reg: int = 0,
                          start_id: int = 1, end_id: int = 247,
                          on_progress=None) -> list[dict]:
        """Scan for active slave IDs on the bus."""
        found = []
        total = end_id - start_id + 1

        for sid in range(start_id, end_id + 1):
            result = await self.read_registers(sid, fc, test_reg, 1)
            entry = {
                "slave_id": sid,
                "online": not bool(result.error),
                "error": result.error,
                "response_ms": round(result.response_ms, 1),
                "value": result.raw_values[0] if result.raw_values else None,
            }
            if entry["online"]:
                found.append(entry)
                logger.info("[Scanner] Found slave %d (%.1fms)", sid, result.response_ms)

            if on_progress:
                progress = (sid - start_id + 1) / total * 100
                await on_progress(sid, progress, entry)

        return found

    async def scan_registers(self, slave_id: int, fc: int, start_reg: int,
                             end_reg: int, block_size: int = 10,
                             byte_order: str = "AB_CD",
                             on_progress=None) -> dict:
        """Scan a range of registers to find valid ones."""
        all_regs = []
        valid_ranges = []
        errors = []
        current_valid_start = None
        total = end_reg - start_reg

        reg = start_reg
        while reg <= end_reg:
            count = min(block_size, end_reg - reg + 1)
            result = await self.read_registers(slave_id, fc, reg, count, byte_order)

            if not result.error:
                if current_valid_start is None:
                    current_valid_start = reg
                for r in result.registers:
                    all_regs.append(r)
            else:
                if current_valid_start is not None:
                    valid_ranges.append({"start": current_valid_start, "end": reg - 1})
                    current_valid_start = None

                if result.error != "TIMEOUT" and "Illegal Data Address" not in result.error:
                    errors.append({"reg": reg, "error": result.error})

                # If Illegal Address, try single register mode to find exact boundary
                if "Illegal Data Address" in result.error and count > 1:
                    for single_reg in range(reg, reg + count):
                        if single_reg > end_reg:
                            break
                        sr = await self.read_registers(slave_id, fc, single_reg, 1, byte_order)
                        if not sr.error:
                            if current_valid_start is None:
                                current_valid_start = single_reg
                            all_regs.extend(sr.registers)
                        else:
                            if current_valid_start is not None:
                                valid_ranges.append({"start": current_valid_start, "end": single_reg - 1})
                                current_valid_start = None

            if on_progress:
                progress = min((reg - start_reg) / max(total, 1) * 100, 100)
                await on_progress(reg, progress)

            reg += count

        if current_valid_start is not None:
            valid_ranges.append({"start": current_valid_start, "end": min(reg - 1, end_reg)})

        return {
            "slave_id": slave_id,
            "fc": fc,
            "fc_name": FC_NAMES.get(fc, f"FC={fc}"),
            "scan_range": {"start": start_reg, "end": end_reg},
            "valid_ranges": valid_ranges,
            "total_valid": len(all_regs),
            "registers": all_regs,
            "errors": errors[:20],
            "byte_order": byte_order,
        }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Modbus RTU Register Scanner")
    parser.add_argument("--port", required=True, help="Serial port")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--slave", type=int, help="Slave ID to scan")
    parser.add_argument("--fc", type=int, default=3, choices=[3, 4])
    parser.add_argument("--start", type=int, default=0, help="Start register")
    parser.add_argument("--count", type=int, default=100, help="Number of registers")
    parser.add_argument("--scan-slaves", action="store_true", help="Scan for slave IDs")
    parser.add_argument("--timeout", type=float, default=500, help="Timeout (ms)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    async def main():
        scanner = ModbusScanner(args.port, args.baud, args.timeout)

        if args.scan_slaves:
            print(f"\n  🔍 Scanning slaves on {args.port} @ {args.baud}...")
            async def on_slave_progress(sid, pct, entry):
                if entry["online"]:
                    print(f"  ✅ Slave {sid} ONLINE ({entry['response_ms']:.0f}ms)")
            found = await scanner.scan_slaves(args.fc, args.start, on_progress=on_slave_progress)
            print(f"\n  Found {len(found)} devices")
        elif args.slave:
            print(f"\n  📊 Reading slave {args.slave}, regs {args.start}-{args.start+args.count-1}...")
            async def on_reg_progress(reg, pct):
                if int(pct) % 10 == 0:
                    print(f"  ... {pct:.0f}%")

            result = await scanner.scan_registers(
                args.slave, args.fc, args.start, args.start + args.count - 1,
                on_progress=on_reg_progress)

            print(f"\n  Valid ranges: {result['valid_ranges']}")
            print(f"  Total registers: {result['total_valid']}\n")
            for r in result['registers'][:50]:
                f32 = r.get('float32', '')
                print(f"  [{r['reg']:>5}]  {r['raw_hex']}  uint16={r['uint16']:<8} "
                      f"int16={r['int16']:<8} float32={f32}")

        scanner.close()

    asyncio.run(main())
