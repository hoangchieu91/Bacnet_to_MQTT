"""BACnet APDU Decoder — giải mã hội thoại BACnet trên bus MS/TP

Decode hoàn toàn OFFLINE — chỉ parse bytes theo chuẩn ASHRAE 135-2016.
Không cần BACpypes, BAC0, hay bất kỳ thư viện ngoài nào.

Hỗ trợ decode:
  • ReadProperty / ReadPropertyMultiple
  • WriteProperty / WritePropertyMultiple
  • SubscribeCOV / COVNotification
  • Who-Is / I-Am / Who-Has / I-Have
  • SimpleAck / ComplexAck / Error / Reject / Abort

Kết quả trả về dạng human-readable string:
  "MAC 5 → MAC 0: WriteProperty Binary-Output:1 Present-Value = Active"
  "MAC 3 → MAC 34: ReadProperty Analog-Input:1 Present-Value"
  "MAC 34 → MAC 3: ComplexAck AI:1 Present-Value = 24.5"
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# BACnet Object Types (ASHRAE 135-2016 Table 12-41)
# ═══════════════════════════════════════════════════════════════════════════════

OBJECT_TYPES: dict[int, str] = {
    0: "AI", 1: "AO", 2: "AV",
    3: "BI", 4: "BO", 5: "BV",
    6: "Calendar", 7: "Command",
    8: "Device", 9: "Event-Enrollment",
    10: "File", 11: "Group",
    12: "Loop", 13: "MSI", 14: "MSO",
    15: "Notification-Class", 16: "Program",
    17: "Schedule", 19: "MSV",
    20: "Trend-Log", 23: "Accumulator",
    24: "Pulse-Converter",
    45: "Access-Door",
    54: "Channel",
    56: "Network-Port",
}

OBJECT_TYPES_FULL: dict[int, str] = {
    0: "Analog Input", 1: "Analog Output", 2: "Analog Value",
    3: "Binary Input", 4: "Binary Output", 5: "Binary Value",
    8: "Device", 13: "Multi-State Input", 14: "Multi-State Output",
    19: "Multi-State Value", 17: "Schedule", 6: "Calendar",
    20: "Trend-Log", 10: "File", 12: "Loop",
}


# ═══════════════════════════════════════════════════════════════════════════════
# BACnet Property Identifiers (common ones)
# ═══════════════════════════════════════════════════════════════════════════════

PROPERTY_IDS: dict[int, str] = {
    28: "Description",
    36: "Event-State",
    46: "Max-Pres-Value",
    45: "Min-Pres-Value",
    55: "Object-List",
    70: "Polarity",
    72: "Active-Text",
    73: "Inactive-Text",
    75: "Object-Identifier",
    76: "Object-Name",
    77: "Object-Type",
    79: "Out-Of-Service",
    81: "Priority-Array",
    85: "Present-Value",
    87: "Reliability",
    91: "Status-Flags",
    103: "Units",
    104: "Update-Interval",
    106: "Vendor-Identifier",
    112: "System-Status",
    117: "Number-Of-States",
    120: "Active-COV-Subscriptions",
    121: "Backup-Failure-Timeout",
    139: "Protocol-Object-Types-Supported",
    152: "Max-Segments-Accepted",
    155: "Protocol-Revision",
    168: "Profile-Name",
    371: "COV-Increment",
}


# ═══════════════════════════════════════════════════════════════════════════════
# BACnet Service Choices
# ═══════════════════════════════════════════════════════════════════════════════

CONFIRMED_SERVICES: dict[int, str] = {
    0: "AcknowledgeAlarm",
    2: "COV-Notification (Conf)",
    4: "ForwardedNPDU",
    5: "SubscribeCOV",
    6: "AtomicReadFile",
    7: "AtomicWriteFile",
    8: "AddListElement",
    9: "RemoveListElement",
    10: "CreateObject",
    11: "DeleteObject",
    12: "ReadProperty",
    14: "ReadPropertyMultiple",
    15: "WriteProperty",
    16: "WritePropertyMultiple",
    17: "DeviceCommunicationControl",
    18: "ConfTextMessage",
    20: "ReinitializeDevice",
    26: "ReadRange",
    28: "SubscribeCOVProperty",
}

UNCONFIRMED_SERVICES: dict[int, str] = {
    0: "I-Am",
    1: "I-Have",
    2: "COV-Notification (Unconf)",
    3: "EventNotification (Unconf)",
    5: "PrivateTransfer (Unconf)",
    6: "TextMessage (Unconf)",
    7: "TimeSynch",
    8: "Who-Has",
    9: "Who-Is",
    11: "UTCTimeSynch",
    14: "IAm-Router-To-Network",
}


# ═══════════════════════════════════════════════════════════════════════════════
# BACnet Units (common)
# ═══════════════════════════════════════════════════════════════════════════════

UNITS: dict[int, str] = {
    55: "°C", 62: "°F", 64: "°F·day",
    18: "kWh", 48: "W", 2: "mA",
    47: "V", 85: "%", 95: "ppm",
    98: "CFM", 0: "m²", 72: "bar",
    73: "mbar", 74: "Pascal", 138: "min",
    72: "hours", 73: "seconds",
}


# ═══════════════════════════════════════════════════════════════════════════════
# Decoded Message
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DecodedMessage:
    """One decoded BACnet conversation message."""
    ts: float = 0.0
    src_mac: int = 0
    dst_mac: int = 0
    direction: str = "→"  # → or ←
    pdu_type: str = ""     # ConfirmedReq, UnconfirmedReq, SimpleAck, ComplexAck, Error, Reject, Abort
    service: str = ""      # ReadProperty, WriteProperty, I-Am, Who-Is, etc.
    invoke_id: int | None = None
    obj_type: str = ""     # AI, BO, Device, etc.
    obj_instance: int | None = None
    property_name: str = ""
    array_index: int | None = None
    value: str = ""        # Decoded value string
    raw_summary: str = ""  # Full human-readable line
    error: str = ""        # If decode failed

    def to_dict(self) -> dict:
        return {
            "ts": round(self.ts, 4),
            "src": self.src_mac,
            "dst": self.dst_mac,
            "pdu_type": self.pdu_type,
            "service": self.service,
            "invoke_id": self.invoke_id,
            "object": f"{self.obj_type}:{self.obj_instance}" if self.obj_instance is not None else "",
            "property": self.property_name,
            "array_index": self.array_index,
            "value": self.value,
            "summary": self.raw_summary,
            "error": self.error,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# BACnet Tag Parser helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_tag(data: bytes, offset: int) -> tuple[int, int, bool, int]:
    """Parse a BACnet tag at offset. Returns (tag_number, length, is_context, new_offset)."""
    if offset >= len(data):
        raise IndexError("Tag beyond data")
    t = data[offset]
    tag_num = (t >> 4) & 0x0F
    cls = bool(t & 0x08)  # context vs application
    length = t & 0x07
    offset += 1

    # Extended tag number
    if tag_num == 0x0F and offset < len(data):
        tag_num = data[offset]
        offset += 1

    # Extended length
    if length == 5 and offset < len(data):
        length = data[offset]
        offset += 1
        if length == 254 and offset + 2 <= len(data):
            length = struct.unpack(">H", data[offset:offset+2])[0]
            offset += 2
        elif length == 255 and offset + 4 <= len(data):
            length = struct.unpack(">I", data[offset:offset+4])[0]
            offset += 4

    return tag_num, length, cls, offset


def _read_unsigned(data: bytes, offset: int, length: int) -> int:
    """Read unsigned integer of given length."""
    val = 0
    for i in range(length):
        if offset + i < len(data):
            val = (val << 8) | data[offset + i]
    return val


def _read_signed(data: bytes, offset: int, length: int) -> int:
    """Read signed integer."""
    val = _read_unsigned(data, offset, length)
    if length > 0 and val >= (1 << (length * 8 - 1)):
        val -= (1 << (length * 8))
    return val


def _read_real(data: bytes, offset: int) -> float:
    """Read IEEE 754 float."""
    if offset + 4 <= len(data):
        return struct.unpack(">f", data[offset:offset+4])[0]
    return 0.0


def _read_double(data: bytes, offset: int) -> float:
    """Read IEEE 754 double."""
    if offset + 8 <= len(data):
        return struct.unpack(">d", data[offset:offset+8])[0]
    return 0.0


def _decode_object_id(data: bytes, offset: int) -> tuple[int, int]:
    """Decode BACnet Object Identifier (4 bytes) → (obj_type, instance)."""
    if offset + 4 > len(data):
        return 0, 0
    oid = struct.unpack(">I", data[offset:offset+4])[0]
    return (oid >> 22) & 0x3FF, oid & 0x3FFFFF


def _decode_app_value(data: bytes, offset: int, tag_num: int, length: int) -> str:
    """Decode an application-tagged value to readable string."""
    try:
        if tag_num == 0:  # Null
            return "Null"
        elif tag_num == 1:  # Boolean
            return "True" if (length > 0 or (offset < len(data) and data[offset])) else "False"
        elif tag_num == 2:  # Unsigned
            return str(_read_unsigned(data, offset, length))
        elif tag_num == 3:  # Signed
            return str(_read_signed(data, offset, length))
        elif tag_num == 4:  # Real
            return f"{_read_real(data, offset):.2f}"
        elif tag_num == 5:  # Double
            return f"{_read_double(data, offset):.4f}"
        elif tag_num == 6:  # Octet string
            return data[offset:offset+length].hex()
        elif tag_num == 7:  # Character string
            if offset + length <= len(data) and length > 1:
                encoding = data[offset]
                text = data[offset+1:offset+length]
                if encoding == 0:  # UTF-8
                    return text.decode("utf-8", errors="replace")
                return text.decode("latin-1", errors="replace")
            return ""
        elif tag_num == 8:  # Bit string
            return f"bits({length})"
        elif tag_num == 9:  # Enumerated
            val = _read_unsigned(data, offset, length)
            # Common enums
            if val == 0:
                return "Inactive"
            elif val == 1:
                return "Active"
            return f"enum({val})"
        elif tag_num == 10:  # Date
            if length >= 4:
                return f"{data[offset]+1900}-{data[offset+1]}-{data[offset+2]}"
            return "date"
        elif tag_num == 11:  # Time
            if length >= 4:
                return f"{data[offset]:02d}:{data[offset+1]:02d}:{data[offset+2]:02d}"
            return "time"
        elif tag_num == 12:  # Object Identifier
            ot, inst = _decode_object_id(data, offset)
            return f"{OBJECT_TYPES.get(ot, f'type{ot}')}:{inst}"
    except Exception:
        pass
    return f"raw({length}b)"


# ═══════════════════════════════════════════════════════════════════════════════
# NPDU Parser
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_npdu(data: bytes) -> tuple[int, int | None, int | None]:
    """Parse BACnet NPDU header. Returns (apdu_offset, src_net, dst_net).

    NPDU format:
      version(1) control(1) [dnet(2) dlen(1) dadr(n)] [snet(2) slen(1) sadr(n)] [hop(1)]
    """
    if len(data) < 2:
        return 2, None, None

    version = data[0]
    control = data[1]
    offset = 2

    dst_net = None
    src_net = None

    # Destination specifier
    if control & 0x20:
        if offset + 3 > len(data):
            return offset, src_net, dst_net
        dst_net = (data[offset] << 8) | data[offset + 1]
        dlen = data[offset + 2]
        offset += 3 + dlen  # skip dadr

    # Source specifier
    if control & 0x08:
        if offset + 3 > len(data):
            return offset, src_net, dst_net
        src_net = (data[offset] << 8) | data[offset + 1]
        slen = data[offset + 2]
        offset += 3 + slen  # skip sadr

    # Hop count
    if control & 0x20:
        offset += 1  # skip hop count

    return offset, src_net, dst_net


# ═══════════════════════════════════════════════════════════════════════════════
# APDU Decoder
# ═══════════════════════════════════════════════════════════════════════════════

def decode_bacnet_frame(data: bytes, src_mac: int, dst_mac: int, ts: float = 0.0) -> DecodedMessage | None:
    """Decode a BACnet MS/TP data payload into human-readable message.

    Args:
        data: The MS/TP frame data payload (after header, before data CRC).
        src_mac: Source MAC address from MS/TP frame header.
        dst_mac: Destination MAC address from MS/TP frame header.
        ts: Timestamp of the frame.

    Returns:
        DecodedMessage or None if not decodable.
    """
    if not data or len(data) < 3:
        return None

    msg = DecodedMessage(ts=ts, src_mac=src_mac, dst_mac=dst_mac)

    try:
        # Parse NPDU
        apdu_offset, src_net, dst_net = _parse_npdu(data)
        if apdu_offset >= len(data):
            return None

        apdu = data[apdu_offset:]
        if len(apdu) < 2:
            return None

        pdu_type_raw = (apdu[0] >> 4) & 0x0F

        if pdu_type_raw == 0:  # Confirmed Request
            _decode_confirmed_request(apdu, msg)
        elif pdu_type_raw == 1:  # Unconfirmed Request
            _decode_unconfirmed_request(apdu, msg)
        elif pdu_type_raw == 2:  # SimpleAck
            _decode_simple_ack(apdu, msg)
        elif pdu_type_raw == 3:  # ComplexAck
            _decode_complex_ack(apdu, msg)
        elif pdu_type_raw == 4:  # Segment Ack
            msg.pdu_type = "SegmentAck"
            msg.service = "SegmentAck"
        elif pdu_type_raw == 5:  # Error
            _decode_error(apdu, msg)
        elif pdu_type_raw == 6:  # Reject
            msg.pdu_type = "Reject"
            msg.invoke_id = apdu[1] if len(apdu) > 1 else None
            reason = apdu[2] if len(apdu) > 2 else 0
            msg.service = f"Reject (reason={reason})"
        elif pdu_type_raw == 7:  # Abort
            msg.pdu_type = "Abort"
            msg.invoke_id = apdu[1] if len(apdu) > 1 else None
            reason = apdu[2] if len(apdu) > 2 else 0
            msg.service = f"Abort (reason={reason})"
        else:
            return None

        # Build human-readable summary
        _build_summary(msg)
        return msg

    except Exception as e:
        msg.error = str(e)
        msg.raw_summary = f"MAC {src_mac} → MAC {dst_mac}: [decode error: {e}]"
        return msg


def _decode_confirmed_request(apdu: bytes, msg: DecodedMessage) -> None:
    """Decode Confirmed-Request PDU."""
    msg.pdu_type = "ConfirmedReq"
    if len(apdu) < 4:
        return

    # Byte 0: type/flags, Byte 1: max-segs/max-apdu, Byte 2: invoke-id, Byte 3: service
    seg = bool(apdu[0] & 0x08)
    msg.invoke_id = apdu[2]
    service_choice = apdu[3]
    msg.service = CONFIRMED_SERVICES.get(service_choice, f"service({service_choice})")

    offset = 4

    if service_choice == 12:  # ReadProperty
        _decode_read_property_req(apdu, offset, msg)
    elif service_choice == 15:  # WriteProperty
        _decode_write_property_req(apdu, offset, msg)
    elif service_choice == 14:  # ReadPropertyMultiple
        _decode_rpm_req(apdu, offset, msg)
    elif service_choice == 5:  # SubscribeCOV
        _decode_subscribe_cov(apdu, offset, msg)
    elif service_choice == 16:  # WritePropertyMultiple
        msg.service = "WritePropertyMultiple"
    elif service_choice == 20:  # ReinitializeDevice
        msg.service = "ReinitializeDevice"
    elif service_choice == 17:  # DeviceCommunicationControl
        msg.service = "DeviceCommunicationControl"


def _decode_unconfirmed_request(apdu: bytes, msg: DecodedMessage) -> None:
    """Decode Unconfirmed-Request PDU."""
    msg.pdu_type = "UnconfirmedReq"
    if len(apdu) < 2:
        return

    service_choice = apdu[1]
    msg.service = UNCONFIRMED_SERVICES.get(service_choice, f"unconf({service_choice})")

    offset = 2

    if service_choice == 0:  # I-Am
        _decode_i_am(apdu, offset, msg)
    elif service_choice == 9:  # Who-Is
        _decode_who_is(apdu, offset, msg)
    elif service_choice == 2:  # Unconfirmed COV Notification
        _decode_cov_notification(apdu, offset, msg)
    elif service_choice == 7:  # TimeSynch
        msg.service = "TimeSynch"
    elif service_choice == 11:  # UTCTimeSynch
        msg.service = "UTCTimeSynch"


def _decode_simple_ack(apdu: bytes, msg: DecodedMessage) -> None:
    """Decode SimpleAck PDU."""
    msg.pdu_type = "SimpleAck"
    if len(apdu) >= 3:
        msg.invoke_id = apdu[1]
        service_choice = apdu[2]
        msg.service = CONFIRMED_SERVICES.get(service_choice, f"service({service_choice})")


def _decode_complex_ack(apdu: bytes, msg: DecodedMessage) -> None:
    """Decode ComplexAck PDU."""
    msg.pdu_type = "ComplexAck"
    if len(apdu) < 3:
        return

    msg.invoke_id = apdu[1]
    service_choice = apdu[2]
    msg.service = CONFIRMED_SERVICES.get(service_choice, f"service({service_choice})")

    offset = 3

    if service_choice == 12:  # ReadProperty Ack
        _decode_read_property_ack(apdu, offset, msg)
    elif service_choice == 14:  # ReadPropertyMultiple Ack
        _decode_rpm_ack(apdu, offset, msg)


def _decode_error(apdu: bytes, msg: DecodedMessage) -> None:
    """Decode Error PDU."""
    msg.pdu_type = "Error"
    if len(apdu) >= 3:
        msg.invoke_id = apdu[1]
        service_choice = apdu[2]
        msg.service = CONFIRMED_SERVICES.get(service_choice, f"service({service_choice})")
    # Error has error-class and error-code as app-tagged enums
    offset = 3
    if offset + 3 <= len(apdu):
        try:
            tag_num, length, _, off2 = _parse_tag(apdu, offset)
            error_class = _read_unsigned(apdu, off2, length)
            offset = off2 + length
            tag_num, length, _, off2 = _parse_tag(apdu, offset)
            error_code = _read_unsigned(apdu, off2, length)
            ERROR_CLASSES = {0:"device",1:"object",2:"property",3:"resources",4:"security",5:"services",6:"vt"}
            msg.value = f"class={ERROR_CLASSES.get(error_class,str(error_class))} code={error_code}"
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Service-specific decoders
# ═══════════════════════════════════════════════════════════════════════════════

def _decode_read_property_req(apdu: bytes, offset: int, msg: DecodedMessage) -> None:
    """ReadProperty Request: [0] ObjectIdentifier, [1] PropertyIdentifier, [2] ArrayIndex (optional)."""
    try:
        # [0] Object Identifier (context tag 0, length 4)
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 0 and is_ctx:
            ot, inst = _decode_object_id(apdu, off)
            msg.obj_type = OBJECT_TYPES.get(ot, f"type{ot}")
            msg.obj_instance = inst
            offset = off + length

        # [1] Property Identifier
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 1 and is_ctx:
            prop_id = _read_unsigned(apdu, off, length)
            msg.property_name = PROPERTY_IDS.get(prop_id, f"prop({prop_id})")
            offset = off + length

        # [2] Array Index (optional)
        if offset < len(apdu):
            tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
            if tag_num == 2 and is_ctx:
                msg.array_index = _read_unsigned(apdu, off, length)
    except Exception:
        pass


def _decode_read_property_ack(apdu: bytes, offset: int, msg: DecodedMessage) -> None:
    """ReadProperty ACK: [0] ObjectId, [1] PropertyId, [2] ArrayIndex, [3] Value."""
    try:
        # [0] Object Identifier
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 0 and is_ctx:
            ot, inst = _decode_object_id(apdu, off)
            msg.obj_type = OBJECT_TYPES.get(ot, f"type{ot}")
            msg.obj_instance = inst
            offset = off + length

        # [1] Property Identifier
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 1 and is_ctx:
            prop_id = _read_unsigned(apdu, off, length)
            msg.property_name = PROPERTY_IDS.get(prop_id, f"prop({prop_id})")
            offset = off + length

        # [2] Array Index (optional)
        if offset < len(apdu):
            tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
            if tag_num == 2 and is_ctx:
                msg.array_index = _read_unsigned(apdu, off, length)
                offset = off + length

        # [3] Opening tag for value
        if offset < len(apdu):
            tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
            if tag_num == 3 and is_ctx and length == 6:  # opening tag
                offset = off
                # Try to read first app-tagged value inside
                if offset < len(apdu):
                    tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
                    if not is_ctx:
                        msg.value = _decode_app_value(apdu, off, tag_num, length)
    except Exception:
        pass


def _decode_write_property_req(apdu: bytes, offset: int, msg: DecodedMessage) -> None:
    """WriteProperty: [0] ObjectId, [1] PropertyId, [2] ArrayIndex, [3] Value, [4] Priority."""
    try:
        # [0] Object Identifier
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 0 and is_ctx:
            ot, inst = _decode_object_id(apdu, off)
            msg.obj_type = OBJECT_TYPES.get(ot, f"type{ot}")
            msg.obj_instance = inst
            offset = off + length

        # [1] Property Identifier
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 1 and is_ctx:
            prop_id = _read_unsigned(apdu, off, length)
            msg.property_name = PROPERTY_IDS.get(prop_id, f"prop({prop_id})")
            offset = off + length

        # [2] Array Index (optional)
        if offset < len(apdu):
            tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
            if tag_num == 2 and is_ctx:
                msg.array_index = _read_unsigned(apdu, off, length)
                offset = off + length

        # [3] Opening tag for value
        if offset < len(apdu):
            tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
            if tag_num == 3 and is_ctx and length == 6:  # opening tag
                offset = off
                if offset < len(apdu):
                    tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
                    if not is_ctx:
                        msg.value = _decode_app_value(apdu, off, tag_num, length)
                        offset = off + length
                # Skip closing tag
                if offset < len(apdu):
                    _parse_tag(apdu, offset)  # closing tag [3]

        # [4] Priority (optional)
        if offset < len(apdu):
            tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
            if tag_num == 4 and is_ctx:
                priority = _read_unsigned(apdu, off, length)
                msg.value += f" @priority={priority}"
    except Exception:
        pass


def _decode_i_am(apdu: bytes, offset: int, msg: DecodedMessage) -> None:
    """I-Am: ObjectId, MaxAPDU, Segmentation, VendorID."""
    try:
        # Object Identifier (app tag 12)
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 12 and not is_ctx:
            ot, inst = _decode_object_id(apdu, off)
            msg.obj_type = OBJECT_TYPES.get(ot, f"type{ot}")
            msg.obj_instance = inst
            offset = off + length

        # Max APDU
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 2 and not is_ctx:
            max_apdu = _read_unsigned(apdu, off, length)
            offset = off + length

        # Segmentation
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 9 and not is_ctx:
            seg = _read_unsigned(apdu, off, length)
            offset = off + length

        # Vendor ID
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 2 and not is_ctx:
            vendor_id = _read_unsigned(apdu, off, length)
            msg.value = f"Device:{inst} vendor={vendor_id}"
    except Exception:
        pass


def _decode_who_is(apdu: bytes, offset: int, msg: DecodedMessage) -> None:
    """Who-Is: optional range [0] low, [1] high."""
    try:
        if offset >= len(apdu):
            msg.value = "all devices"
            return
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 0 and is_ctx:
            low = _read_unsigned(apdu, off, length)
            offset = off + length
            tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
            if tag_num == 1 and is_ctx:
                high = _read_unsigned(apdu, off, length)
                msg.value = f"range {low}–{high}"
        else:
            msg.value = "all devices"
    except Exception:
        msg.value = "all devices"


def _decode_cov_notification(apdu: bytes, offset: int, msg: DecodedMessage) -> None:
    """COV Notification (unconfirmed)."""
    msg.service = "COV-Notification"
    try:
        # [0] Subscriber Process ID
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 0 and is_ctx:
            offset = off + length

        # [1] Initiating Device
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 1 and is_ctx:
            ot, inst = _decode_object_id(apdu, off)
            offset = off + length

        # [2] Monitored Object
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 2 and is_ctx:
            ot, inst = _decode_object_id(apdu, off)
            msg.obj_type = OBJECT_TYPES.get(ot, f"type{ot}")
            msg.obj_instance = inst
            offset = off + length

        # [3] Time Remaining
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 3 and is_ctx:
            time_remaining = _read_unsigned(apdu, off, length)
            offset = off + length

        # [4] Opening tag for list of values
        if offset < len(apdu):
            tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
            if tag_num == 4 and is_ctx and length == 6:  # opening tag
                offset = off
                # Try to read first property+value pair
                values = []
                for _ in range(4):  # max 4 properties
                    if offset >= len(apdu):
                        break
                    tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
                    if tag_num == 4 and is_ctx and length == 7:  # closing tag
                        break
                    if tag_num == 0 and is_ctx:  # property ID
                        prop_id = _read_unsigned(apdu, off, length)
                        prop_name = PROPERTY_IDS.get(prop_id, f"prop({prop_id})")
                        offset = off + length
                        # Skip through to find value
                        if offset < len(apdu):
                            tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
                            if tag_num == 2 and is_ctx and length == 6:  # opening [2]
                                offset = off
                                if offset < len(apdu):
                                    tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
                                    if not is_ctx:
                                        val = _decode_app_value(apdu, off, tag_num, length)
                                        values.append(f"{prop_name}={val}")
                                        offset = off + length
                                # skip closing [2]
                                if offset < len(apdu):
                                    offset += 1
                    else:
                        offset = off + length
                if values:
                    msg.value = ", ".join(values)
    except Exception:
        pass


def _decode_subscribe_cov(apdu: bytes, offset: int, msg: DecodedMessage) -> None:
    """SubscribeCOV request."""
    msg.service = "SubscribeCOV"
    try:
        # [0] SubscriberProcessID
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 0 and is_ctx:
            offset = off + length

        # [1] Monitored Object
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 1 and is_ctx:
            ot, inst = _decode_object_id(apdu, off)
            msg.obj_type = OBJECT_TYPES.get(ot, f"type{ot}")
            msg.obj_instance = inst
            offset = off + length

        # [2] Issue Confirmed Notifications
        if offset < len(apdu):
            tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
            if tag_num == 2 and is_ctx:
                confirmed = bool(_read_unsigned(apdu, off, length))
                msg.value = "confirmed" if confirmed else "unconfirmed"
                offset = off + length

        # [3] Lifetime
        if offset < len(apdu):
            tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
            if tag_num == 3 and is_ctx:
                lifetime = _read_unsigned(apdu, off, length)
                msg.value += f" lifetime={lifetime}s"
    except Exception:
        pass


def _decode_rpm_req(apdu: bytes, offset: int, msg: DecodedMessage) -> None:
    """ReadPropertyMultiple request — decode first object+property."""
    msg.service = "ReadPropertyMultiple"
    try:
        # [0] First object identifier
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 0 and is_ctx:
            ot, inst = _decode_object_id(apdu, off)
            msg.obj_type = OBJECT_TYPES.get(ot, f"type{ot}")
            msg.obj_instance = inst
            offset = off + length

        # [1] Opening tag for list of properties
        if offset < len(apdu):
            tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
            if tag_num == 1 and is_ctx and length == 6:  # opening
                offset = off
                props = []
                for _ in range(8):
                    if offset >= len(apdu):
                        break
                    tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
                    if tag_num == 1 and is_ctx and length == 7:  # closing
                        break
                    if tag_num == 0 and is_ctx:
                        prop_id = _read_unsigned(apdu, off, length)
                        props.append(PROPERTY_IDS.get(prop_id, f"prop({prop_id})"))
                    offset = off + length
                if props:
                    msg.property_name = ", ".join(props[:5])
                    if len(props) > 5:
                        msg.property_name += f" +{len(props)-5} more"
    except Exception:
        pass


def _decode_rpm_ack(apdu: bytes, offset: int, msg: DecodedMessage) -> None:
    """ReadPropertyMultiple ACK — decode first object + first value."""
    msg.service = "ReadPropertyMultiple"
    try:
        tag_num, length, is_ctx, off = _parse_tag(apdu, offset)
        if tag_num == 0 and is_ctx:
            ot, inst = _decode_object_id(apdu, off)
            msg.obj_type = OBJECT_TYPES.get(ot, f"type{ot}")
            msg.obj_instance = inst
            msg.value = f"{OBJECT_TYPES.get(ot, f'type{ot}')}:{inst} (multi)"
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# Summary builder
# ═══════════════════════════════════════════════════════════════════════════════

def _build_summary(msg: DecodedMessage) -> None:
    """Build human-readable one-line summary."""
    parts = [f"MAC {msg.src_mac} → MAC {msg.dst_mac}:"]

    if msg.pdu_type in ("SimpleAck",):
        parts.append(f"✓ {msg.service}")
    elif msg.pdu_type == "Error":
        parts.append(f"✗ {msg.service}")
        if msg.value:
            parts.append(f"({msg.value})")
    elif msg.pdu_type == "Reject":
        parts.append(f"✗ {msg.service}")
    elif msg.pdu_type == "Abort":
        parts.append(f"⊘ {msg.service}")
    else:
        if msg.pdu_type == "ComplexAck":
            parts.append(f"← {msg.service}")
        elif msg.pdu_type == "ConfirmedReq":
            parts.append(msg.service)
        elif msg.pdu_type == "UnconfirmedReq":
            parts.append(msg.service)
        else:
            parts.append(f"[{msg.pdu_type}] {msg.service}")

    if msg.obj_type and msg.obj_instance is not None:
        parts.append(f"{msg.obj_type}:{msg.obj_instance}")

    if msg.property_name:
        parts.append(msg.property_name)

    if msg.array_index is not None:
        parts.append(f"[{msg.array_index}]")

    if msg.value:
        parts.append(f"= {msg.value}")

    msg.raw_summary = " ".join(parts)
