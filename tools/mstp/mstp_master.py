"""MS/TP Master Node — Pure Python, ASHRAE 135-2016 §9.3

Joins the token ring on an RS-485 bus, sends WhoIs broadcasts,
receives I-Am responses, and can issue ReadProperty requests.

Usage:
    python3 mstp_master.py --port /dev/ttyUSB0 --baud 38400 --whois
    python3 mstp_master.py --port /dev/ttyUSB0 --baud 38400 --read 5 analogValue 1
"""

from __future__ import annotations

import argparse
import json
import logging
import struct
import time
from enum import IntEnum, auto
from dataclasses import dataclass, field
from typing import Any

import serial

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# CRC — ASHRAE 135-2016 Annex G verbatim tables
# ═══════════════════════════════════════════════════════════════════════════════

CRC8 = [
    0x00, 0xFE, 0xFF, 0x01, 0xFD, 0x03, 0x02, 0xFC,
    0xF9, 0x07, 0x06, 0xF8, 0x04, 0xFA, 0xFB, 0x05,
    0xF1, 0x0F, 0x0E, 0xF0, 0x0C, 0xF2, 0xF3, 0x0D,
    0x08, 0xF6, 0xF7, 0x09, 0xF5, 0x0B, 0x0A, 0xF4,
    0xE1, 0x1F, 0x1E, 0xE0, 0x1C, 0xE2, 0xE3, 0x1D,
    0x18, 0xE6, 0xE7, 0x19, 0xE5, 0x1B, 0x1A, 0xE4,
    0x10, 0xEE, 0xEF, 0x11, 0xED, 0x13, 0x12, 0xEC,
    0xE9, 0x17, 0x16, 0xE8, 0x14, 0xEA, 0xEB, 0x15,
    0xC1, 0x3F, 0x3E, 0xC0, 0x3C, 0xC2, 0xC3, 0x3D,
    0x38, 0xC6, 0xC7, 0x39, 0xC5, 0x3B, 0x3A, 0xC4,
    0x30, 0xCE, 0xCF, 0x31, 0xCD, 0x33, 0x32, 0xCC,
    0xC9, 0x37, 0x36, 0xC8, 0x34, 0xCA, 0xCB, 0x35,
    0x20, 0xDE, 0xDF, 0x21, 0xDD, 0x23, 0x22, 0xDC,
    0xD9, 0x27, 0x26, 0xD8, 0x24, 0xDA, 0xDB, 0x25,
    0xD1, 0x2F, 0x2E, 0xD0, 0x2C, 0xD2, 0xD3, 0x2D,
    0x28, 0xD6, 0xD7, 0x29, 0xD5, 0x2B, 0x2A, 0xD4,
    0x81, 0x7F, 0x7E, 0x80, 0x7C, 0x82, 0x83, 0x7D,
    0x78, 0x86, 0x87, 0x79, 0x85, 0x7B, 0x7A, 0x84,
    0x70, 0x8E, 0x8F, 0x71, 0x8D, 0x73, 0x72, 0x8C,
    0x89, 0x77, 0x76, 0x88, 0x74, 0x8A, 0x8B, 0x75,
    0x60, 0x9E, 0x9F, 0x61, 0x9D, 0x63, 0x62, 0x9C,
    0x99, 0x67, 0x66, 0x98, 0x64, 0x9A, 0x9B, 0x65,
    0x91, 0x6F, 0x6E, 0x90, 0x6C, 0x92, 0x93, 0x6D,
    0x68, 0x96, 0x97, 0x69, 0x95, 0x6B, 0x6A, 0x94,
    0x40, 0xBE, 0xBF, 0x41, 0xBD, 0x43, 0x42, 0xBC,
    0xB9, 0x47, 0x46, 0xB8, 0x44, 0xBA, 0xBB, 0x45,
    0xB1, 0x4F, 0x4E, 0xB0, 0x4C, 0xB2, 0xB3, 0x4D,
    0x48, 0xB6, 0xB7, 0x49, 0xB5, 0x4B, 0x4A, 0xB4,
    0xA1, 0x5F, 0x5E, 0xA0, 0x5C, 0xA2, 0xA3, 0x5D,
    0x58, 0xA6, 0xA7, 0x59, 0xA5, 0x5B, 0x5A, 0xA4,
    0x50, 0xAE, 0xAF, 0x51, 0xAD, 0x53, 0x52, 0xAC,
    0xA9, 0x57, 0x56, 0xA8, 0x54, 0xAA, 0xAB, 0x55,
]

CRC16 = [
    0x0000, 0x1189, 0x2312, 0x329B, 0x4624, 0x57AD, 0x6536, 0x74BF,
    0x8C48, 0x9DC1, 0xAF5A, 0xBED3, 0xCA6C, 0xDBE5, 0xE97E, 0xF8F7,
    0x1081, 0x0108, 0x3393, 0x221A, 0x56A5, 0x472C, 0x75B7, 0x643E,
    0x9CC9, 0x8D40, 0xBFDB, 0xAE52, 0xDAED, 0xCB64, 0xF9FF, 0xE876,
    0x2102, 0x308B, 0x0210, 0x1399, 0x6726, 0x76AF, 0x4434, 0x55BD,
    0xAD4A, 0xBCC3, 0x8E58, 0x9FD1, 0xEB6E, 0xFAE7, 0xC87C, 0xD9F5,
    0x3183, 0x200A, 0x1291, 0x0318, 0x77A7, 0x662E, 0x54B5, 0x453C,
    0xBDCB, 0xAC42, 0x9ED9, 0x8F50, 0xFBEF, 0xEA66, 0xD8FD, 0xC974,
    0x4204, 0x538D, 0x6116, 0x709F, 0x0420, 0x15A9, 0x2732, 0x36BB,
    0xCE4C, 0xDFC5, 0xED5E, 0xFCD7, 0x8868, 0x99E1, 0xAB7A, 0xBAF3,
    0x5285, 0x430C, 0x7197, 0x601E, 0x14A1, 0x0528, 0x37B3, 0x263A,
    0xDECD, 0xCF44, 0xFDDF, 0xEC56, 0x98E9, 0x8960, 0xBBFB, 0xAA72,
    0x6306, 0x728F, 0x4014, 0x519D, 0x2522, 0x34AB, 0x0630, 0x17B9,
    0xEF4E, 0xFEC7, 0xCC5C, 0xDDD5, 0xA96A, 0xB8E3, 0x8A78, 0x9BF1,
    0x7387, 0x620E, 0x5095, 0x411C, 0x35A3, 0x242A, 0x16B1, 0x0738,
    0xFFCF, 0xEE46, 0xDCDD, 0xCD54, 0xB9EB, 0xA862, 0x9AF9, 0x8B70,
    0x8408, 0x9581, 0xA71A, 0xB693, 0xC22C, 0xD3A5, 0xE13E, 0xF0B7,
    0x0840, 0x19C9, 0x2B52, 0x3ADB, 0x4E64, 0x5FED, 0x6D76, 0x7CFF,
    0x9489, 0x8500, 0xB79B, 0xA612, 0xD2AD, 0xC324, 0xF1BF, 0xE036,
    0x18C1, 0x0948, 0x3BD3, 0x2A5A, 0x5EE5, 0x4F6C, 0x7DF7, 0x6C7E,
    0xA50A, 0xB483, 0x8618, 0x9791, 0xE32E, 0xF2A7, 0xC03C, 0xD1B5,
    0x2942, 0x38CB, 0x0A50, 0x1BD9, 0x6F66, 0x7EEF, 0x4C74, 0x5DFD,
    0xB58B, 0xA402, 0x9699, 0x8710, 0xF3AF, 0xE226, 0xD0BD, 0xC134,
    0x39C3, 0x284A, 0x1AD1, 0x0B58, 0x7FE7, 0x6E6E, 0x5CF5, 0x4D7C,
    0xC60C, 0xD785, 0xE51E, 0xF497, 0x8028, 0x91A1, 0xA33A, 0xB2B3,
    0x4A44, 0x5BCD, 0x6956, 0x78DF, 0x0C60, 0x1DE9, 0x2F72, 0x3EFB,
    0xD68D, 0xC704, 0xF59F, 0xE416, 0x90A9, 0x8120, 0xB3BB, 0xA232,
    0x5AC5, 0x4B4C, 0x79D7, 0x685E, 0x1CE1, 0x0D68, 0x3FF3, 0x2E7A,
    0xE70E, 0xF687, 0xC41C, 0xD595, 0xA12A, 0xB0A3, 0x8238, 0x93B1,
    0x6B46, 0x7ACF, 0x4854, 0x59DD, 0x2D62, 0x3CEB, 0x0E70, 0x1FF9,
    0xF78F, 0xE606, 0xD49D, 0xC514, 0xB1AB, 0xA022, 0x92B9, 0x8330,
    0x7BC7, 0x6A4E, 0x58D5, 0x495C, 0x3DE3, 0x2C6A, 0x1EF1, 0x0F78,
]

def calc_crc8(data: bytes) -> int:
    crc = 0xFF
    for b in data:
        crc = CRC8[crc ^ b]
    return (~crc) & 0xFF

def calc_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc = (crc >> 8) ^ CRC16[(crc ^ b) & 0xFF]
    return (~crc) & 0xFFFF


# ═══════════════════════════════════════════════════════════════════════════════
# Frame types
# ═══════════════════════════════════════════════════════════════════════════════

class FT(IntEnum):
    TOKEN              = 0x00
    POLL_FOR_MASTER    = 0x01
    REPLY_TO_POLL      = 0x02
    TEST_REQUEST       = 0x03
    TEST_RESPONSE      = 0x04
    BACNET_DATA_XR     = 0x05
    BACNET_DATA_NXR    = 0x06
    REPLY_POSTPONED    = 0x07

FT_NAMES = {v: v.name for v in FT}


# ═══════════════════════════════════════════════════════════════════════════════
# Frame builder / parser
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MstpFrame:
    ft: int
    dst: int
    src: int
    data: bytes = b""
    valid: bool = True

    @property
    def name(self) -> str:
        try:
            return FT(self.ft).name
        except ValueError:
            return f"0x{self.ft:02X}"


def build_frame(ft: int, dst: int, src: int, data: bytes = b"") -> bytes:
    """Build a complete MS/TP frame with preamble and CRCs."""
    dlen = len(data)
    header = bytes([ft, dst, src, (dlen >> 8) & 0xFF, dlen & 0xFF])
    hcrc = calc_crc8(header)
    frame = b'\x55\xFF' + header + bytes([hcrc])
    if data:
        dcrc = calc_crc16(data)
        frame += data + struct.pack('<H', dcrc)
    return frame


class FrameReader:
    """Blocking frame reader from serial port with timeout."""

    def __init__(self, ser: serial.Serial):
        self._ser = ser

    def read_frame(self, timeout: float = 0.5) -> MstpFrame | None:
        """Read one complete MS/TP frame. Returns None on timeout."""
        deadline = time.monotonic() + timeout
        # Sync to preamble
        while time.monotonic() < deadline:
            b = self._ser.read(1)
            if not b:
                continue
            if b[0] != 0x55:
                continue
            # Look for 0xFF
            b2 = self._ser.read(1)
            if not b2 or b2[0] != 0xFF:
                continue
            # Read header: ft, dst, src, len_hi, len_lo, hcrc
            hdr = self._ser.read(6)
            if len(hdr) < 6:
                continue
            ft, dst, src, lhi, llo, hcrc = hdr
            dlen = (lhi << 8) | llo
            expected = calc_crc8(bytes([ft, dst, src, lhi, llo]))
            if hcrc != expected:
                continue  # Bad header CRC
            if dlen == 0:
                return MstpFrame(ft=ft, dst=dst, src=src)
            if dlen > 512:
                continue
            # Read data + 2-byte CRC
            payload = self._ser.read(dlen + 2)
            if len(payload) < dlen + 2:
                continue
            data = bytes(payload[:dlen])
            dcrc_recv = payload[dlen] | (payload[dlen + 1] << 8)
            dcrc_exp = calc_crc16(data)
            return MstpFrame(ft=ft, dst=dst, src=src, data=data,
                             valid=(dcrc_recv == dcrc_exp))
        return None  # Timeout


# ═══════════════════════════════════════════════════════════════════════════════
# BACnet NPDU/APDU builders
# ═══════════════════════════════════════════════════════════════════════════════

# BACnet Object Types (subset)
OBJ_TYPES = {
    "analogInput": 0, "analogOutput": 1, "analogValue": 2,
    "binaryInput": 3, "binaryOutput": 4, "binaryValue": 5,
    "device": 8, "multiStateInput": 13, "multiStateOutput": 14,
    "multiStateValue": 19,
}
OBJ_NAMES = {v: k for k, v in OBJ_TYPES.items()}

# BACnet Properties (subset)
PROP_IDS = {
    "objectName": 77, "presentValue": 85, "vendorName": 121,
    "modelName": 70, "firmwareRevision": 44, "systemStatus": 112,
    "objectList": 76, "description": 28, "applicationSoftwareVersion": 12,
}
PROP_NAMES = {v: k for k, v in PROP_IDS.items()}


def build_whois() -> bytes:
    """BACnet WhoIs broadcast: NPDU + APDU (unconfirmed, no limits)."""
    npdu = bytes([0x01, 0x20, 0xFF, 0xFF, 0x00, 0xFF])
    # version=1, ctrl=0x20 (DNET present), DNET=0xFFFF (global broadcast),
    # DLEN=0 (broadcast MAC), hop_count=255
    apdu = bytes([0x10, 0x08])  # Unconfirmed-REQ, WhoIs
    return npdu + apdu


def build_read_property(device_instance: int, obj_type: int, obj_instance: int,
                        prop_id: int, invoke_id: int = 1) -> bytes:
    """BACnet ReadProperty confirmed request."""
    # NPDU: version=1, ctrl=0x04 (expecting reply, no routing)
    npdu = bytes([0x01, 0x04])

    # APDU: Confirmed-REQ (PDU type 0x00)
    # service = ReadProperty (12)
    pdu_type = 0x00  # confirmed-request
    max_resp = 0x05  # 1476 octets
    apdu_hdr = bytes([pdu_type, max_resp, invoke_id, 0x0C])  # service=12=ReadProperty

    # Context tag 0: ObjectIdentifier (4 bytes)
    oid = (obj_type << 22) | (obj_instance & 0x3FFFFF)
    oid_bytes = struct.pack('>I', oid)
    tag0 = bytes([0x0C]) + oid_bytes  # context 0, length 4

    # Context tag 1: PropertyIdentifier (variable length)
    if prop_id <= 0xFF:
        tag1 = bytes([0x19, prop_id])  # context 1, length 1
    else:
        tag1 = bytes([0x1A, (prop_id >> 8) & 0xFF, prop_id & 0xFF])

    return npdu + apdu_hdr + tag0 + tag1


def build_write_property(device_instance: int, obj_type: int, obj_instance: int,
                         prop_id: int, value, priority: int | None = None,
                         invoke_id: int = 1) -> bytes:
    """BACnet WriteProperty confirmed request (service=15)."""
    npdu = bytes([0x01, 0x04])

    pdu_type = 0x00  # confirmed-request
    max_resp = 0x05
    apdu_hdr = bytes([pdu_type, max_resp, invoke_id, 0x0F])  # service=15=WriteProperty

    # Context tag 0: ObjectIdentifier
    oid = (obj_type << 22) | (obj_instance & 0x3FFFFF)
    tag0 = bytes([0x0C]) + struct.pack('>I', oid)

    # Context tag 1: PropertyIdentifier
    if prop_id <= 0xFF:
        tag1 = bytes([0x19, prop_id])
    else:
        tag1 = bytes([0x1A, (prop_id >> 8) & 0xFF, prop_id & 0xFF])

    # Context tag 3 (opening): propertyValue
    tag3_open = bytes([0x3E])
    # Encode value as application-tagged
    if isinstance(value, float):
        val_bytes = bytes([0x44]) + struct.pack('>f', value)  # Real (tag 4, len 4)
    elif isinstance(value, bool):
        val_bytes = bytes([0x11 if value else 0x10])  # Boolean
    elif isinstance(value, int):
        if value < 0:
            # Signed integer
            if -128 <= value <= 127:
                val_bytes = bytes([0x31, value & 0xFF])
            else:
                val_bytes = bytes([0x32]) + struct.pack('>h', value)
        else:
            # Unsigned
            if value <= 0xFF:
                val_bytes = bytes([0x21, value])
            elif value <= 0xFFFF:
                val_bytes = bytes([0x22, (value >> 8) & 0xFF, value & 0xFF])
            else:
                val_bytes = bytes([0x24]) + struct.pack('>I', value)
    elif isinstance(value, str) and value in ('active', 'inactive'):
        # BACnet Enumerated for binary objects
        val_bytes = bytes([0x91, 1 if value == 'active' else 0])
    else:
        # Try as unsigned
        val_bytes = bytes([0x21, int(value)])
    tag3_close = bytes([0x3F])

    result = npdu + apdu_hdr + tag0 + tag1 + tag3_open + val_bytes + tag3_close

    # Context tag 4: priority (optional)
    if priority is not None and 1 <= priority <= 16:
        result += bytes([0x49, priority])

    return result


def build_reinitialize_device(state: int = 0, password: str = "",
                               invoke_id: int = 1) -> bytes:
    """BACnet ReinitializeDevice confirmed request (service=20).
    state: 0=coldstart, 1=warmstart
    """
    npdu = bytes([0x01, 0x04])
    pdu_type = 0x00
    max_resp = 0x05
    apdu_hdr = bytes([pdu_type, max_resp, invoke_id, 0x14])  # service=20

    # Context tag 0: ReinitializedStateOfDevice (Enumerated)
    tag0 = bytes([0x09, state])

    result = npdu + apdu_hdr + tag0

    # Context tag 1: password (optional CharacterString)
    if password:
        pwd_bytes = bytes([0]) + password.encode('utf-8')  # encoding=0 (UTF-8)
        plen = len(pwd_bytes)
        if plen <= 4:
            result += bytes([0x10 | plen]) + pwd_bytes
        elif plen <= 253:
            result += bytes([0x15, plen]) + pwd_bytes

    return result


def parse_iam(data: bytes) -> dict | None:
    """Parse I-Am from a BACnet NPDU+APDU payload."""
    if len(data) < 4 or data[0] != 0x01:
        return None

    # Skip NPDU routing headers
    ctrl = data[1]
    offset = 2
    if ctrl & 0x20:  # DNET
        if offset + 3 > len(data): return None
        offset += 2  # DNET
        dlen = data[offset]; offset += 1
        offset += dlen  # DADR
    if ctrl & 0x08:  # SNET
        if offset + 3 > len(data): return None
        snet = (data[offset] << 8) | data[offset+1]; offset += 2
        slen = data[offset]; offset += 1
        offset += slen
    if ctrl & 0x20:  # hop count
        offset += 1

    # APDU
    if offset + 1 >= len(data): return None
    if data[offset] != 0x10: return None  # not unconfirmed-request
    if data[offset + 1] != 0x00: return None  # not I-Am
    offset += 2

    result = {}
    # Object Identifier (application tag 12, len 4)
    if offset < len(data):
        tag = data[offset]
        tag_num = (tag >> 4) & 0x0F
        tag_len = tag & 0x07
        if tag_num == 12 and tag_len == 4 and offset + 5 <= len(data):
            oid = struct.unpack('>I', data[offset+1:offset+5])[0]
            result['object_type'] = (oid >> 22) & 0x3FF
            result['device_instance'] = oid & 0x3FFFFF
            offset += 5

    # Max APDU (tag 2 = Unsigned)
    if offset < len(data):
        tag = data[offset]; tag_num = (tag >> 4) & 0x0F; tag_len = tag & 0x07
        if tag_num == 2:
            val = 0
            for i in range(tag_len):
                if offset + 1 + i < len(data):
                    val = (val << 8) | data[offset + 1 + i]
            result['max_apdu'] = val
            offset += 1 + tag_len

    # Segmentation (tag 9 = Enumerated)
    if offset < len(data):
        tag = data[offset]; tag_num = (tag >> 4) & 0x0F; tag_len = tag & 0x07
        if tag_num == 9 and offset + 1 < len(data):
            result['segmentation'] = data[offset + 1]
            offset += 1 + tag_len

    # Vendor ID (tag 2 = Unsigned)
    if offset < len(data):
        tag = data[offset]; tag_num = (tag >> 4) & 0x0F; tag_len = tag & 0x07
        if tag_num == 2:
            val = 0
            for i in range(tag_len):
                if offset + 1 + i < len(data):
                    val = (val << 8) | data[offset + 1 + i]
            result['vendor_id'] = val

    return result if 'device_instance' in result else None


def parse_read_property_ack(data: bytes) -> dict | None:
    """Parse ReadProperty-ACK from BACnet NPDU+APDU."""
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
    if pdu_type != 3: return None  # Not complex-ACK (0x30 >> 4 = 3)

    invoke_id = data[offset + 1]
    service = data[offset + 2]
    if service != 12: return None  # Not ReadProperty-ACK
    offset += 3

    result = {"invoke_id": invoke_id}

    # Context tag 0: ObjectIdentifier
    if offset < len(data) and data[offset] == 0x0C:
        oid = struct.unpack('>I', data[offset+1:offset+5])[0]
        result['object_type'] = OBJ_NAMES.get((oid >> 22) & 0x3FF, str((oid >> 22) & 0x3FF))
        result['object_instance'] = oid & 0x3FFFFF
        offset += 5

    # Context tag 1: PropertyIdentifier
    if offset < len(data) and (data[offset] & 0xF8) == 0x18:
        plen = data[offset] & 0x07
        pid = 0
        for i in range(plen):
            pid = (pid << 8) | data[offset + 1 + i]
        result['property'] = PROP_NAMES.get(pid, str(pid))
        offset += 1 + plen

    # Context tag 3 (opening): propertyValue
    if offset < len(data) and data[offset] == 0x3E:
        offset += 1  # opening tag
        # Parse application-tagged value
        if offset < len(data):
            tag = data[offset]
            tag_num = (tag >> 4) & 0x0F
            tag_len = tag & 0x07
            if tag_len == 5:  # Extended length
                if offset + 1 < len(data):
                    tag_len = data[offset + 1]
                    offset += 1
            offset += 1
            value_bytes = data[offset:offset + tag_len]

            if tag_num == 4:  # Real (float)
                if len(value_bytes) >= 4:
                    result['value'] = struct.unpack('>f', value_bytes[:4])[0]
            elif tag_num == 7:  # CharacterString
                if value_bytes and value_bytes[0] == 0:  # UTF-8 encoding
                    result['value'] = value_bytes[1:].decode('utf-8', errors='replace')
                else:
                    result['value'] = value_bytes.decode('utf-8', errors='replace')
            elif tag_num == 2:  # Unsigned
                val = 0
                for b in value_bytes:
                    val = (val << 8) | b
                result['value'] = val
            elif tag_num == 1:  # Boolean
                result['value'] = bool(tag_len)  # len encodes value for boolean
            elif tag_num == 9:  # Enumerated
                val = 0
                for b in value_bytes:
                    val = (val << 8) | b
                result['value'] = val
            elif tag_num == 5:  # Double
                if len(value_bytes) >= 8:
                    result['value'] = struct.unpack('>d', value_bytes[:8])[0]
            elif tag_num == 3:  # Signed integer
                val = int.from_bytes(value_bytes, 'big', signed=True)
                result['value'] = val
            else:
                result['value_raw'] = value_bytes.hex()

    return result if 'property' in result or 'value' in result else None


# ═══════════════════════════════════════════════════════════════════════════════
# MS/TP Master State Machine
# ═══════════════════════════════════════════════════════════════════════════════

class MasterState(IntEnum):
    INITIALIZE     = auto()
    IDLE           = auto()
    NO_TOKEN       = auto()
    USE_TOKEN      = auto()
    WAIT_FOR_REPLY = auto()
    PASS_TOKEN     = auto()
    POLL_FOR_MASTER = auto()


class MstpMaster:
    """MS/TP Master node — joins token ring, sends/receives BACnet frames.

    Simplified implementation of ASHRAE 135-2016 §9.3 suitable for
    a diagnostic/scanning tool. Not a full production-grade stack.
    """

    # Timing parameters (§9.3)
    T_REPLY_TIMEOUT   = 0.255      # 255ms — max wait for reply
    T_USAGE_TIMEOUT   = 0.020      # 20ms — max time to use token
    T_NO_TOKEN        = 0.500      # 500ms — timeout before generating token
    T_FRAME_ABORT     = 0.100      # frame inter-character timeout
    N_POLL            = 50         # poll for master every N token cycles
    N_RETRY_TOKEN     = 1          # retransmit token once
    N_MAX_MASTER      = 127        # max master address

    def __init__(self, port: str, baudrate: int = 38400, mac: int = 127):
        self.port = port
        self.baudrate = baudrate
        self.mac = mac
        self._ser: serial.Serial | None = None
        self._reader: FrameReader | None = None
        self._next_station = (mac + 1) % (self.N_MAX_MASTER + 1)
        self._poll_station = (mac + 1) % (self.N_MAX_MASTER + 1)
        self._token_count = 0
        self._sole_master = False  # True if we're the only master on the bus
        self._pending_request: bytes | None = None
        self._pending_dst: int = 0xFF
        self._pending_expects_reply: bool = False
        self._response: MstpFrame | None = None
        self._iam_responses: list[dict] = []
        self._state = MasterState.INITIALIZE

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def open(self) -> None:
        self._ser = serial.Serial(
            port=self.port, baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE, timeout=0.05,
        )
        self._reader = FrameReader(self._ser)
        logger.info("MS/TP Master opened %s @ %d (MAC=%d)", self.port, self.baudrate, self.mac)

    def close(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()
        logger.info("MS/TP Master closed")

    def _send(self, frame_bytes: bytes) -> None:
        if self._ser:
            self._ser.write(frame_bytes)
            self._ser.flush()

    def _send_frame(self, ft: int, dst: int, data: bytes = b"") -> None:
        self._send(build_frame(ft, dst, self.mac, data))

    # ── Token ring ─────────────────────────────────────────────────────────

    def run(self, duration: float = 30.0, callback=None) -> None:
        """Run the master state machine for `duration` seconds.

        callback(event, data): called on significant events:
          'token'  — we received the token
          'iam'    — I-Am received, data = parsed dict
          'reply'  — ReadProperty-ACK received, data = parsed dict
          'joined' — successfully joined token ring
        """
        self.open()
        try:
            self._state = MasterState.IDLE
            start = time.monotonic()
            joined = False
            idle_since = time.monotonic()

            while time.monotonic() - start < duration:
                frame = self._reader.read_frame(timeout=0.1)

                if frame is not None:
                    idle_since = time.monotonic()
                    self._handle_frame(frame, callback)
                    if not joined and self._state == MasterState.USE_TOKEN:
                        joined = True
                        if callback:
                            callback('joined', {'mac': self.mac})
                else:
                    # No frame received — check timeouts
                    silence = time.monotonic() - idle_since
                    if silence > self.T_NO_TOKEN and not joined:
                        # Bus appears dead — try generating token ourselves
                        logger.info("Bus silent for %.0fms — generating token",
                                    silence * 1000)
                        self._state = MasterState.USE_TOKEN
                        joined = True
                        if callback:
                            callback('joined', {'mac': self.mac})

                # State actions
                if self._state == MasterState.USE_TOKEN:
                    self._use_token(callback)
                    self._state = MasterState.PASS_TOKEN

                if self._state == MasterState.PASS_TOKEN:
                    self._pass_token()
                    self._state = MasterState.IDLE

        finally:
            self.close()

    def _handle_frame(self, frame: MstpFrame, callback=None) -> None:
        """Process a received frame and update state."""
        # Always check for I-Am in BACnet data frames
        if frame.ft in (FT.BACNET_DATA_XR, FT.BACNET_DATA_NXR) and frame.valid and frame.data:
            iam = parse_iam(frame.data)
            if iam:
                iam['mac'] = frame.src
                self._iam_responses.append(iam)
                logger.info("I-Am from MAC %d: Device %d (vendor=%s)",
                            frame.src, iam.get('device_instance', '?'),
                            iam.get('vendor_id', '?'))
                if callback:
                    callback('iam', iam)

        # Check for ReadProperty-ACK responses to our requests
        if (frame.ft == FT.BACNET_DATA_NXR and frame.dst == self.mac
                and frame.valid and frame.data):
            ack = parse_read_property_ack(frame.data)
            if ack:
                self._response = frame
                logger.info("ReadProperty-ACK: %s", ack)
                if callback:
                    callback('reply', ack)

        # Token addressed to us
        if frame.ft == FT.TOKEN and frame.dst == self.mac:
            self._token_count += 1
            self._state = MasterState.USE_TOKEN
            if callback:
                callback('token', {'count': self._token_count})

        # Poll-For-Master addressed to us — reply
        elif frame.ft == FT.POLL_FOR_MASTER and frame.dst == self.mac:
            self._send_frame(FT.REPLY_TO_POLL, frame.src)
            logger.debug("Replied to PFM from MAC %d", frame.src)

    def _use_token(self, callback=None) -> None:
        """We have the token — send pending request if any."""
        if self._pending_request is not None:
            ft = FT.BACNET_DATA_XR if self._pending_expects_reply else FT.BACNET_DATA_NXR
            self._send_frame(ft, self._pending_dst, self._pending_request)
            logger.debug("Sent BACnet frame to %d (%d bytes, type=%s)",
                          self._pending_dst, len(self._pending_request), FT_NAMES.get(ft, '?'))

            if self._pending_expects_reply:
                # Wait for reply
                self._response = None
                reply = self._reader.read_frame(timeout=self.T_REPLY_TIMEOUT)
                if reply and reply.valid:
                    self._handle_frame(reply, callback)

            self._pending_request = None

    def _pass_token(self) -> None:
        """Pass token to next station."""
        self._send_frame(FT.TOKEN, self._next_station)

        # Periodically poll for master
        if self._token_count % self.N_POLL == 0:
            self._poll_for_master()

    def _poll_for_master(self) -> None:
        """Send PFM to discover next station after our successor."""
        ps = (self._next_station + 1) % (self.N_MAX_MASTER + 1)
        if ps == self.mac:
            return
        self._send_frame(FT.POLL_FOR_MASTER, ps)
        reply = self._reader.read_frame(timeout=0.1)
        if reply and reply.ft == FT.REPLY_TO_POLL and reply.src == ps:
            logger.debug("PFM: found new node at MAC %d", ps)

    # ── High-level operations ──────────────────────────────────────────────

    def queue_whois(self) -> None:
        """Queue a WhoIs broadcast to be sent when we get the token."""
        self._pending_request = build_whois()
        self._pending_dst = 0xFF
        self._pending_expects_reply = False

    def queue_read_property(self, dst_mac: int, device_instance: int,
                            obj_type: int, obj_instance: int,
                            prop_id: int, invoke_id: int = 1) -> None:
        """Queue a ReadProperty request."""
        self._pending_request = build_read_property(
            device_instance, obj_type, obj_instance, prop_id, invoke_id)
        self._pending_dst = dst_mac
        self._pending_expects_reply = True

    def get_iam_responses(self) -> list[dict]:
        return list(self._iam_responses)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="MS/TP Master Node — Join token ring and scan")
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=38400)
    parser.add_argument("--mac", type=int, default=127, help="Our MS/TP MAC (0-127)")
    parser.add_argument("--duration", type=int, default=30, help="Run time in seconds")
    parser.add_argument("--whois", action="store_true", help="Send WhoIs broadcast")
    parser.add_argument("--read", nargs=3, metavar=("MAC", "OBJ_TYPE", "INSTANCE"),
                        help="Read presentValue: MAC objType instance")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)-5s %(message)s")

    master = MstpMaster(args.port, args.baud, args.mac)

    events = []
    devices_found = {}
    read_results = []

    def on_event(event, data):
        events.append((event, data))
        if event == 'joined':
            print(f"  ✅ Joined token ring as MAC {data['mac']}")
            # Send WhoIs after joining
            if args.whois:
                master.queue_whois()
                print(f"  ✉  WhoIs broadcast queued")
        elif event == 'token':
            cnt = data['count']
            if cnt <= 3 or cnt % 10 == 0:
                print(f"  🔄 Token #{cnt}")
            # Re-send WhoIs periodically
            if args.whois and cnt in (5, 15, 30):
                master.queue_whois()
        elif event == 'iam':
            dev_id = data.get('device_instance', '?')
            vendor = data.get('vendor_id', '?')
            mac = data.get('mac', '?')
            devices_found[dev_id] = data
            print(f"  📟 I-Am: Device {dev_id} at MAC {mac} (vendor={vendor})")
        elif event == 'reply':
            read_results.append(data)
            prop = data.get('property', '?')
            val = data.get('value', data.get('value_raw', '?'))
            print(f"  📖 {prop} = {val}")

    print(f"\n📡 MS/TP Master — {args.port} @ {args.baud} baud (MAC {args.mac})")
    print(f"   Running for {args.duration}s\n")

    if args.read:
        mac_dst = int(args.read[0])
        obj_type_str = args.read[1]
        obj_inst = int(args.read[2])
        obj_type_id = OBJ_TYPES.get(obj_type_str, int(obj_type_str))
        # Queue the first WhoIs, then schedule read after join
        if args.whois:
            master.queue_whois()

    master.run(duration=args.duration, callback=on_event)

    # Summary
    print(f"\n{'═'*60}")
    print(f"  MS/TP Master Session Summary")
    print(f"{'═'*60}")
    token_events = [e for e in events if e[0] == 'token']
    print(f"  Token cycles: {len(token_events)}")
    print(f"  Devices found: {len(devices_found)}")
    for dev_id, info in sorted(devices_found.items()):
        seg_names = {0: "Both", 1: "Xmit", 2: "Recv", 3: "None"}
        print(f"    📟 Device {dev_id} — MAC {info.get('mac','?')}, "
              f"Vendor {info.get('vendor_id','?')}, "
              f"MaxAPDU {info.get('max_apdu','?')}, "
              f"Seg {seg_names.get(info.get('segmentation'), '?')}")
    if read_results:
        print(f"  Read results: {len(read_results)}")
        for r in read_results:
            print(f"    {r.get('property','?')} = {r.get('value', r.get('value_raw','?'))}")
    print(f"{'═'*60}\n")

    if args.json:
        print(json.dumps({
            "devices": devices_found,
            "reads": read_results,
            "tokens": len(token_events),
        }, indent=2, default=str))


if __name__ == "__main__":
    main()
