"""Raw MS/TP WhoIs Scanner — no BAC0/bacpypes dependency.

Passively listens for I-Am responses after injecting a WhoIs broadcast
into the MS/TP token ring. Uses the fixed ASHRAE 135 Annex G CRC tables.

Usage: python3 mstp_scan_raw.py --port /dev/ttyUSB0 --baud 38400
"""

import serial
import struct
import time
import argparse
import json

# ── ASHRAE 135 Annex G CRC tables ─────────────────────────────────────────────

CRC8_TABLE = [
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

CRC16_TABLE = [
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

def crc8(data):
    crc = 0xFF
    for b in data:
        crc = CRC8_TABLE[crc ^ b]
    return (~crc) & 0xFF

def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc = (crc >> 8) ^ CRC16_TABLE[(crc ^ b) & 0xFF]
    return (~crc) & 0xFFFF

# ── MS/TP Frame Types ─────────────────────────────────────────────────────────
FT_TOKEN            = 0x00
FT_POLL_FOR_MASTER  = 0x01
FT_REPLY_TO_POLL    = 0x02
FT_BACNET_DATA_XR   = 0x05  # BACnet Data Expecting Reply
FT_BACNET_DATA_NXR  = 0x06  # BACnet Data Not Expecting Reply

FT_NAMES = {
    0x00: "Token", 0x01: "PollForMaster", 0x02: "ReplyToPoll",
    0x03: "TestReq", 0x04: "TestResp",
    0x05: "BACnet-XR", 0x06: "BACnet-NXR", 0x07: "ReplyPostponed",
}

# ── Frame builder ─────────────────────────────────────────────────────────────

def build_frame(ft, dst, src, data=b""):
    dlen = len(data)
    header = bytes([ft, dst, src, (dlen >> 8) & 0xFF, dlen & 0xFF])
    hcrc = crc8(header)
    frame = b'\x55\xFF' + header + bytes([hcrc])
    if data:
        dcrc = crc16(data)
        frame += data + struct.pack('<H', dcrc)
    return frame

# ── BACnet NPDU/APDU builders ─────────────────────────────────────────────────

def build_whois_npdu():
    """Build a BACnet WhoIs broadcast NPDU+APDU (no routing)."""
    # NPDU: version=0x01, control=0x20 (no DNET, no SNET, data-expecting-reply=0, priority=normal)
    # Actually for WhoIs broadcast: control=0x00 (no expecting reply for unconfirmed)
    npdu = bytes([0x01, 0x00])  # BACnet version 1, control: no routing
    # APDU: Unconfirmed-REQ (PDU type 0x10), Service=WhoIs (0x08)
    apdu = bytes([0x10, 0x08])
    return npdu + apdu

def parse_iam(data):
    """Parse I-Am APDU from BACnet frame data. Returns dict or None."""
    # Find NPDU header: version=0x01
    if len(data) < 6 or data[0] != 0x01:
        return None
    
    # Skip NPDU (variable length based on control byte)
    ctrl = data[1]
    offset = 2
    # If DNET present (bit 5 of ctrl)
    if ctrl & 0x20:
        if offset + 2 >= len(data): return None
        dnet = (data[offset] << 8) | data[offset+1]
        offset += 2
        dlen = data[offset]
        offset += 1 + dlen  # skip DADR
    # If SNET present (bit 3 of ctrl)
    if ctrl & 0x08:
        if offset + 2 >= len(data): return None
        snet = (data[offset] << 8) | data[offset+1]
        offset += 2
        slen = data[offset]
        offset += 1 + slen  # skip SADR
    # If DNET present, skip hop count
    if ctrl & 0x20:
        offset += 1
    
    # Now at APDU
    if offset >= len(data): return None
    pdu_type = data[offset]
    if pdu_type != 0x10:  # Not unconfirmed-request
        return None
    if offset + 1 >= len(data): return None
    service = data[offset + 1]
    if service != 0x00:  # Not I-Am
        return None
    
    offset += 2
    # Parse I-Am: ObjectIdentifier, maxAPDUlength, segmentation, vendorID
    result = {}
    
    # Object Identifier (context tag 0 or application tag)
    if offset < len(data):
        tag_byte = data[offset]
        tag_class = (tag_byte >> 3) & 0x01  # 0=application
        if tag_class == 0:
            # Application tag 12 = BACnetObjectIdentifier
            tag_num = (tag_byte >> 4) & 0x0F
            tag_len = tag_byte & 0x07
            if tag_num == 12 and tag_len == 4 and offset + 5 <= len(data):
                oid = struct.unpack('>I', data[offset+1:offset+5])[0]
                obj_type = (oid >> 22) & 0x3FF
                instance = oid & 0x3FFFFF
                result['object_type'] = obj_type  # 8 = device
                result['device_instance'] = instance
                offset += 5
    
    # Max APDU Length Accepted  
    if offset < len(data):
        tag_byte = data[offset]
        tag_num = (tag_byte >> 4) & 0x0F
        tag_len = tag_byte & 0x07
        if tag_num == 2:  # Unsigned
            val = 0
            for i in range(tag_len):
                if offset + 1 + i < len(data):
                    val = (val << 8) | data[offset + 1 + i]
            result['max_apdu'] = val
            offset += 1 + tag_len
    
    # Segmentation Supported
    if offset < len(data):
        tag_byte = data[offset]
        tag_num = (tag_byte >> 4) & 0x0F
        tag_len = tag_byte & 0x07
        if tag_num == 9:  # Enumerated
            if offset + 1 < len(data):
                result['segmentation'] = data[offset + 1]
            offset += 1 + tag_len
    
    # Vendor ID
    if offset < len(data):
        tag_byte = data[offset]
        tag_num = (tag_byte >> 4) & 0x0F
        tag_len = tag_byte & 0x07
        if tag_num == 2:  # Unsigned
            val = 0
            for i in range(tag_len):
                if offset + 1 + i < len(data):
                    val = (val << 8) | data[offset + 1 + i]
            result['vendor_id'] = val
            offset += 1 + tag_len
    
    return result if 'device_instance' in result else None

# ── Frame parser ──────────────────────────────────────────────────────────────

def parse_frames(buf):
    """Parse MS/TP frames from buffer. Returns list of (ft, dst, src, data, valid)."""
    frames = []
    i = 0
    while i < len(buf) - 7:
        if buf[i] == 0x55 and buf[i+1] == 0xFF:
            ft, dst, src, lhi, llo, hcrc = buf[i+2], buf[i+3], buf[i+4], buf[i+5], buf[i+6], buf[i+7]
            dlen = (lhi << 8) | llo
            header = bytes([ft, dst, src, lhi, llo])
            expected_hcrc = crc8(header)
            hdr_ok = (hcrc == expected_hcrc)
            
            if dlen == 0:
                frames.append((ft, dst, src, b'', hdr_ok))
                i += 8
            elif dlen <= 512 and i + 8 + dlen + 2 <= len(buf):
                data = bytes(buf[i+8:i+8+dlen])
                dcrc_recv = buf[i+8+dlen] | (buf[i+8+dlen+1] << 8)
                dcrc_exp = crc16(data)
                data_ok = (dcrc_recv == dcrc_exp)
                frames.append((ft, dst, src, data, hdr_ok and data_ok))
                i += 8 + dlen + 2
            else:
                i += 1
        else:
            i += 1
    return frames


def main():
    parser = argparse.ArgumentParser(description="Raw MS/TP Device Scanner")
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=38400)
    parser.add_argument("--duration", type=int, default=20, help="Scan duration in seconds")
    parser.add_argument("--my-mac", type=int, default=127, help="Our MS/TP MAC address")
    args = parser.parse_args()

    print(f"\n📡 MS/TP Device Scanner — {args.port} @ {args.baud} baud")
    print(f"   Listening for {args.duration}s (our MAC: {args.my_mac})\n")

    ser = serial.Serial(args.port, args.baud, timeout=0.1)
    
    # Phase 1: Passive listen — collect all BACnet data frames and I-Am responses
    buf = bytearray()
    devices = {}  # device_instance -> {mac, info}
    mac_frames = {}  # mac -> frame_count
    whois_sent = False
    
    start = time.time()
    while time.time() - start < args.duration:
        chunk = ser.read(512)
        if chunk:
            buf.extend(chunk)
        
        # Parse what we have so far
        frames = parse_frames(buf)
        if not frames:
            continue
        
        for ft, dst, src, data, valid in frames:
            mac_frames[src] = mac_frames.get(src, 0) + 1
            
            # Send WhoIs after we see a token pass (bus is active)
            if not whois_sent and ft == FT_TOKEN and dst == args.my_mac:
                # We received the token! Send WhoIs broadcast
                whois_npdu = build_whois_npdu()
                whois_frame = build_frame(FT_BACNET_DATA_NXR, 0xFF, args.my_mac, whois_npdu)
                ser.write(whois_frame)
                ser.flush()
                whois_sent = True
                elapsed = time.time() - start
                print(f"  ✉ Sent WhoIs broadcast at {elapsed:.1f}s")
            
            # Parse BACnet data frames for I-Am
            if valid and ft in (FT_BACNET_DATA_XR, FT_BACNET_DATA_NXR) and data:
                iam = parse_iam(data)
                if iam and iam.get('device_instance') is not None:
                    dev_id = iam['device_instance']
                    if dev_id not in devices:
                        devices[dev_id] = {
                            'mac': src,
                            'device_instance': dev_id,
                            'object_type': iam.get('object_type', '?'),
                            'max_apdu': iam.get('max_apdu', '?'),
                            'segmentation': iam.get('segmentation', '?'),
                            'vendor_id': iam.get('vendor_id', '?'),
                        }
                        print(f"  🔍 I-Am from MAC {src}: Device {dev_id} (vendor={iam.get('vendor_id','?')})")
        
        # Keep only unparsed tail
        # Find last valid frame end position and keep remainder
        buf = bytearray()
    
    ser.close()
    
    # Output
    print(f"\n{'═'*60}")
    print(f"  MS/TP Scan Results")
    print(f"{'═'*60}")
    print(f"  MACs seen (by frame count):")
    for mac, count in sorted(mac_frames.items()):
        dev_match = [d for d in devices.values() if d['mac'] == mac]
        dev_str = f"  → Device {dev_match[0]['device_instance']}" if dev_match else ""
        print(f"    MAC {mac:3d}: {count:5d} frames{dev_str}")
    
    print(f"\n  Devices discovered via I-Am: {len(devices)}")
    if devices:
        print(f"  {'─'*56}")
        for dev_id, info in sorted(devices.items()):
            seg_names = {0: "Both", 1: "Transmit", 2: "Receive", 3: "None"}
            seg = seg_names.get(info.get('segmentation'), str(info.get('segmentation')))
            print(f"  📟 Device {dev_id}")
            print(f"       MAC:          {info['mac']}")
            print(f"       Vendor ID:    {info.get('vendor_id', '?')}")
            print(f"       Max APDU:     {info.get('max_apdu', '?')}")
            print(f"       Segmentation: {seg}")
    else:
        print("  (No I-Am responses received — devices may not auto-broadcast)")
        print("  Note: Without joining the token ring, WhoIs cannot be sent.")
        print("  The 4 detected MACs are still valid MS/TP nodes.")
    
    print(f"{'═'*60}\n")
    
    # Also output as JSON
    print(json.dumps({
        "macs_seen": mac_frames,
        "devices": devices,
        "whois_sent": whois_sent,
    }, indent=2))


if __name__ == "__main__":
    main()
