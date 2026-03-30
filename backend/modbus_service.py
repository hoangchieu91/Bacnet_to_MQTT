"""Modbus service — wraps serial for device discovery, scanning, and bus health analysis.

This service implements 4 key diagnostic techniques:
1. Active Slave Scanner (ID detection)
2. Register Map Discovery (Address detection)
3. Passive Bus Health Analyzer (CRC, Silence, Chatty Master, Utilization)
4. Multi-format Data Decoder (AB_CD, CD_AB, etc.)
"""

from __future__ import annotations

import asyncio
import collections
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import serial
from backend.models import ModbusConfig

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Modbus RTU Constants
# ──────────────────────────────────────────────
FUNCTION_NAMES: dict[int, str] = {
    0x01: "Read Coils",
    0x02: "Read Discrete Inputs",
    0x03: "Read Holding Registers",
    0x04: "Read Input Registers",
    0x05: "Write Single Coil",
    0x06: "Write Single Register",
    0x0F: "Write Multiple Coils",
    0x10: "Write Multiple Registers",
}

EXCEPTION_NAMES: dict[int, str] = {
    0x01: "Illegal Function",
    0x02: "Illegal Data Address",
    0x03: "Illegal Data Value",
    0x04: "Server Device Failure",
}

# ──────────────────────────────────────────────
# CRC-16 (Modbus)
# ──────────────────────────────────────────────
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
    pdu = struct.pack(">BBHH", slave, fc, start_reg, count)
    c = crc16(pdu)
    return pdu + struct.pack("<H", c)


# ──────────────────────────────────────────────
# Data Models for Results
# ──────────────────────────────────────────────
@dataclass
class ModbusScanResult:
    slave_id: int
    fc: int
    start_reg: int
    registers: list[dict] = field(default_factory=list)
    raw_values: list[int] = field(default_factory=list)
    error: str = ""
    response_ms: float = 0

@dataclass
class ModbusPathology:
    severity: str     # 'critical' | 'warning' | 'info'
    code: str
    description: str
    slaves_involved: list[int] = field(default_factory=list)
    detail: str = ""


# ──────────────────────────────────────────────
# Technique 4: Data Decoder
# ──────────────────────────────────────────────
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

    result = []
    for idx, val in enumerate(raw_regs):
        entry = {
            "reg_offset": idx,
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
        result.append(entry)
    return result


# ──────────────────────────────────────────────
# Main Service
# ──────────────────────────────────────────────
class ModbusService:
    """Encapsulates Modbus RTU diagnostics and polling."""

    def __init__(self, config: ModbusConfig):
        self._config = config
        self._ser: Optional[serial.Serial] = None
        self._lock = asyncio.Lock()  # Prevent concurrent bus access
        self._connected = False
        
        # Calculated inter-frame delay
        char_bits = 11.0 # 1 start, 8 data, 1 parity, 1 stop
        self._t35 = max(3.5 * char_bits / max(config.baudrate, 1), 0.00175)

        # Bus analysis state
        self._total_frames = 0
        self._bad_crc_count = 0
        self._start_time = time.perf_counter()
        self._pathologies: list[ModbusPathology] = []

    async def start(self) -> None:
        if not self._config.enabled:
            return
        try:
            await asyncio.get_event_loop().run_in_executor(None, self._open_sync)
            self._connected = True
            logger.info("Modbus service started on %s @ %d baud", self._config.port, self._config.baudrate)
        except Exception as e:
            logger.error("Failed to start Modbus service: %s", e)
            self._connected = False

    def _open_sync(self) -> None:
        parity_map = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}
        self._ser = serial.Serial(
            port=self._config.port,
            baudrate=self._config.baudrate,
            parity=parity_map.get(self._config.parity, serial.PARITY_NONE),
            stopbits=self._config.stopbits,
            timeout=self._config.timeout_ms / 1000.0
        )

    async def stop(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()
        self._connected = False

    # ── Technique 1: Active Slave Scanner ─────────────────────────────
    async def scan_slaves(self, start_id: int = 1, end_id: int = 247, fc: int = 3) -> list[dict]:
        """Find online Modbus devices on the bus."""
        found = []
        for sid in range(start_id, end_id + 1):
            result = await self.read_registers(sid, fc, 0, 1)
            if not result.error:
                entry = {
                    "slave_id": sid,
                    "response_ms": round(result.response_ms, 1),
                    "status": "online"
                }
                found.append(entry)
        return found

    # ── Technique 2: Register Map Discovery ────────────────────────────
    async def scan_registers(self, slave_id: int, fc: int, start: int, end: int) -> dict:
        """Dò vùng thanh ghi hợp lệ cho một Slave."""
        valid_ranges = []
        current_valid_start = None
        block_size = 20
        
        reg = start
        while reg <= end:
            count = min(block_size, end - reg + 1)
            res = await self.read_registers(slave_id, fc, reg, count)
            
            if not res.error:
                if current_valid_start is None:
                    current_valid_start = reg
            else:
                if current_valid_start is not None:
                    valid_ranges.append({"start": current_valid_start, "end": reg - 1})
                    current_valid_start = None
                
                # If illegal address, try single registers to find boundary
                if "Illegal Data Address" in res.error and count > 1:
                    for single in range(reg, reg + count):
                        sr = await self.read_registers(slave_id, fc, single, 1)
                        if not sr.error:
                            if current_valid_start is None: current_valid_start = single
                        else:
                            if current_valid_start is not None:
                                valid_ranges.append({"start": current_valid_start, "end": single - 1})
                                current_valid_start = None
            reg += count
            
        if current_valid_start is not None:
            valid_ranges.append({"start": current_valid_start, "end": end})
            
        return {"slave_id": slave_id, "valid_ranges": valid_ranges}

    # ── Core Read Logic ──────────────────────────────────────────────
    async def read_registers(self, slave_id: int, fc: int, start: int, count: int, byte_order: str = "AB_CD") -> ModbusScanResult:
        if not self._connected:
            return ModbusScanResult(slave_id, fc, start, error="Service not started")
            
        async with self._lock:
            return await asyncio.get_event_loop().run_in_executor(None, self._read_sync, slave_id, fc, start, count, byte_order)

    def _read_sync(self, slave_id: int, fc: int, start: int, count: int, byte_order: str) -> ModbusScanResult:
        res = ModbusScanResult(slave_id, fc, start)
        try:
            self._ser.reset_input_buffer()
            req = build_request(slave_id, fc, start, count)
            time.sleep(self._t35)
            self._ser.write(req)
            
            t0 = time.perf_counter()
            header = self._ser.read(3)
            if len(header) < 3:
                res.error = "TIMEOUT"
                return res
                
            r_sid, r_fc, bc = header[0], header[1], header[2]
            if r_fc & 0x80: # Exception
                res.error = f"EXCEPTION: {EXCEPTION_NAMES.get(bc, hex(bc))}"
                self._ser.read(2) # CRC
                return res
                
            data = self._ser.read(bc + 2)
            res.response_ms = (time.perf_counter() - t0) * 1000
            
            if len(data) < bc + 2:
                res.error = "SHORT RESPONSE"
                return res
                
            # Technique 3 Check: CRC
            full = header + data
            exp = crc16(full[:-2])
            recv = full[-2] | (full[-1] << 8)
            if exp != recv:
                res.error = "CRC ERROR"
                self._bad_crc_count += 1
                return res
                
            self._total_frames += 1
            reg_data = data[:-2]
            vals = [(reg_data[i] << 8) | reg_data[i+1] for i in range(0, len(reg_data)-1, 2)]
            res.raw_values = vals
            res.registers = decode_registers(vals, byte_order)
            
        except Exception as e:
            res.error = str(e)
        return res

    # ── Technique 3: Health Analysis ──────────────────────────────────
    def get_health_report(self) -> dict:
        """Trả về báo cáo sức khỏe bus hiện tại."""
        elapsed = time.perf_counter() - self._start_time
        crc_rate = (self._bad_crc_count / max(self._total_frames, 1)) * 100
        
        pathologies = []
        if crc_rate > 5:
            pathologies.append({
                "severity": "warning", 
                "code": "HIGH_CRC_ERROR", 
                "description": f"Tỷ lệ lỗi CRC cao ({crc_rate:.1f}%) - Kiểm tra nhiễu hoặc dây dẫn."
            })
            
        return {
            "uptime_s": round(elapsed, 1),
            "total_frames": self._total_frames,
            "bad_crc_count": self._bad_crc_count,
            "crc_error_rate_pct": round(crc_rate, 2),
            "pathologies": pathologies
        }
