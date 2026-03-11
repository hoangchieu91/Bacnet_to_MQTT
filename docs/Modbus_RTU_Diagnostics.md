# Modbus RTU Sniffer & Diagnostics — Kỹ thuật chi tiết

> Tài liệu kỹ thuật cho bộ công cụ `tools/modbus/`  
> Phiên bản: 1.0 — 2026-03-11

---

## Mục lục
1. [Tổng quan kiến trúc](#1-tổng-quan-kiến-trúc)
2. [Mode 1: Passive Sniffer (1 cổng COM)](#2-mode-1-passive-sniffer)
3. [Mode 2: Inline Proxy (2 cổng COM)](#3-mode-2-inline-proxy)
4. [Bảng so sánh chẩn đoán](#4-bảng-so-sánh-chẩn-đoán)
5. [Chi tiết kỹ thuật phân tích frame](#5-chi-tiết-kỹ-thuật-phân-tích-frame)
6. [Cài đặt và sử dụng](#6-cài-đặt-và-sử-dụng)

---

## 1. Tổng quan kiến trúc

Có 2 mode hoạt động, tùy cách đấu nối phần cứng:

```
┌─────────────────────────────────────────────────────────┐
│  MODE 1: Passive Sniffer (1 COM)                        │
│                                                         │
│  Master ──── RS-485 Bus ──── Slave 1                    │
│                │                Slave 2                  │
│                │                Slave 3                  │
│            ┌───┴───┐                                     │
│            │ USB-485│  ← Tap (chỉ đọc, không gửi)       │
│            │ ComA   │                                    │
│            └───┬───┘                                     │
│                │                                         │
│           ┌────┴────┐                                    │
│           │   Pi 5  │                                    │
│           │ Sniffer │                                    │
│           └─────────┘                                    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  MODE 2: Inline Proxy (2 COM)                           │
│                                                         │
│  Master ──── ComA ─── [ Pi 5 ] ─── ComB ──── Slaves    │
│                       │       │                          │
│              ┌────────┘       └────────┐                 │
│              │  Forward               │                  │
│              │  A→B (Master→Slave)    │                  │
│              │  B→A (Slave→Master)    │                  │
│              │                        │                  │
│              │  + Analyze             │                  │
│              │  + Log                 │                  │
│              │  + Inject (optional)   │                  │
│              └────────────────────────┘                  │
└─────────────────────────────────────────────────────────┘
```

### Phần cứng cần thiết

| Mode | Phần cứng | Ghi chú |
|------|-----------|---------|
| Passive | 1× USB-RS485 adapter | Tap vào bus, chỉ RX |
| Inline Proxy | 2× USB-RS485 adapter | ComA nối Master, ComB nối Slaves |

> **Lưu ý:** Inline Proxy cắt đứt bus RS-485 thành 2 đoạn. Pi 5 trở thành cầu nối trung gian
> — nếu Pi bị tắt hoặc crash, **Master mất liên lạc với Slaves**.

---

## 2. Mode 1: Passive Sniffer

### Nguyên lý hoạt động

USB-RS485 adapter nối **song song** (tap) vào bus RS-485, chỉ bật chân RX, không bật DE/RE (không truyền).

```
Frame detection:
  Raw bytes → Gap detector (3.5 char time) → Frame parser → CRC check → Health analyzer
```

**Modbus RTU frame format:**
```
┌──────────┬───────────────┬─────────────┬──────────┐
│ Slave ID │ Function Code │ Data (N B)  │ CRC-16   │
│  1 byte  │    1 byte     │  0-252 B    │  2 bytes │
└──────────┴───────────────┴─────────────┴──────────┘
                                          LSB first
```

**Inter-frame gap:**
- Modbus RTU phân tách frame bằng khoảng im lặng ≥ **3.5 character times**
- 1 character = 11 bits (start + 8 data + parity + stop)
- Ví dụ: @9600 baud → gap ≥ 4.0ms, @19200 → gap ≥ 2.0ms

### CRC-16 Modbus

Polynomial: `0xA001` (bit-reversed `0x8005`), Initial: `0xFFFF`, LSB-first.

```python
def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc  # 2 bytes, LSB first in frame
```

### Chẩn đoán Passive Sniffer

| # | Code | Mô tả | Cách phát hiện |
|---|------|--------|----------------|
| 1 | `HIGH_CRC_ERRORS` | Dây kém, nhiễu điện | `bad_frames / total_frames > 5%` |
| 2 | `JUNK_BYTES` | Bytes rác trên bus | Bytes không thuộc frame hợp lệ nào |
| 3 | `BUS_SILENCE` | Bus im lặng > 5s | Không nhận được frame nào |
| 4 | `HIGH_BUS_UTILIZATION` | Bus quá tải | `(total_bytes × 11) / (baudrate × elapsed) > 70%` |
| 5 | `CHATTY_MASTER` | Master poll quá nhanh | Request rate > 50/s cho 1 slave |
| 6 | `EXCEPTION_RESPONSES` | Slave báo lỗi | Exception response rate > 10% |
| 7 | `SLOW_RESPONSE` | ⚠️ Heuristic | Dự đoán dựa trên khoảng cách giữa request và frame tiếp theo |
| 8 | `SLAVE_NO_RESPONSE` | ⚠️ Heuristic | Request pending > 5s không có response tương ứng |

> ⚠️ **Giới hạn Mode 1:** Vì passive sniffer nhìn thấy **tất cả traffic trên 1 dây**, không phân biệt được chiều (Master→Slave hay Slave→Master). Chẩn đoán #7 và #8 dùng heuristic — **có thể sai** khi bus bận nhiều slave.

---

## 3. Mode 2: Inline Proxy

### Nguyên lý hoạt động

Pi 5 đứng giữa Master và Slaves, với 2 cổng COM:

```
ComA (Master side)                     ComB (Slave side)
  ┌──────────────┐                    ┌──────────────┐
  │ /dev/ttyUSB0 │◄── Master sends ──►│ /dev/ttyUSB1 │──► Forward to Slaves
  │              │                    │              │
  │              │◄── Forward back ◄──│              │◄── Slaves respond
  └──────────────┘                    └──────────────┘
```

**Luồng xử lý:**

```
1. ComA nhận request từ Master
2. Ghi log: {direction: "M→S", slave_id, func_code, data, timestamp_A}
3. Forward y nguyên sang ComB → Slaves nhận request
4. ComB nhận response từ Slave
5. Ghi log: {direction: "S→M", slave_id, func_code, data, timestamp_B}
6. Forward y nguyên sang ComA → Master nhận response
7. Tính: response_time = timestamp_B - timestamp_A
```

### Chẩn đoán nâng cao (chỉ có ở Mode 2)

Ngoài 8 chẩn đoán của Mode 1, Inline Proxy thêm **7 chẩn đoán mới**:

| # | Code | Mô tả | Cách phát hiện | Giá trị |
|---|------|--------|----------------|---------|
| 9 | `EXACT_RESPONSE_TIME` | Đo chính xác response time | `ts_response_ComB − ts_request_ComA` | Không cần heuristic |
| 10 | `NO_RESPONSE_EXACT` | Slave thật sự không trả lời | Request từ ComA → timeout trên ComB | 100% chính xác, không false positive |
| 11 | `DIRECTION_VIOLATION` | Slave gửi frame không được hỏi | Frame từ ComB mà không có request tương ứng từ ComA | Slave bị lỗi firmware |
| 12 | `LATE_RESPONSE` | Response đến sau timeout của Master | Master đã gửi request mới (trên ComA) trước khi slave trả lời (trên ComB) | Slave quá chậm |
| 13 | `BROADCAST_RESPONSE` | Slave trả lời broadcast (cấm) | Request broadcast (slave=0) từ ComA, nhưng có response từ ComB | Vi phạm spec |
| 14 | `DUPLICATE_SLAVE_ID` | 2 slave cùng địa chỉ | 1 request → 2+ responses trên ComB | Cấu hình sai |
| 15 | `FRAME_CORRUPTION` | Frame bị hỏng trên 1 chiều | CRC OK trên ComA nhưng fail trên ComB (hoặc ngược lại) | Dây 1 chiều bị lỗi |

### Request-Response Matching (chính xác)

Passive mode dùng heuristic để ghép request-response. Inline proxy **biết chắc chắn**:

```
ComA nhận frame → đó là REQUEST (từ Master)
ComB nhận frame → đó là RESPONSE (từ Slave)
```

Bảng ghép cặp:

```
Request Queue:                    Response Queue:
┌──────────────────────────┐     ┌──────────────────────────┐
│ ts=0.000  S=1 FC=03      │ ──► │ ts=0.012  S=1 FC=03      │  ✓ matched, RT=12ms
│ ts=0.050  S=2 FC=03      │ ──► │ ts=0.095  S=2 FC=03      │  ✓ matched, RT=45ms
│ ts=0.100  S=3 FC=03      │ ──► │ (timeout 1000ms)         │  ✗ NO_RESPONSE_EXACT
│ ts=0.150  S=1 FC=06      │ ──► │ ts=0.155  S=1 FC=86      │  ⚠ EXCEPTION (Illegal Addr)
└──────────────────────────┘     └──────────────────────────┘
```

### Kỹ thuật Forward (Chi tiết)

**Yêu cầu quan trọng:**
- Forward phải **trong suốt** (transparent) — Master và Slave không biết Pi ở giữa
- **Không được thêm delay** đáng kể (< 1ms)
- Forward **từng byte** hoặc **từng frame** — cần chọn chiến lược phù hợp

**Chiến lược 1: Byte-level forward (đơn giản, latency thấp)**

```python
# Pseudo-code
async def forward_loop(src_port, dst_port, direction):
    while True:
        data = src_port.read(256)  # non-blocking, timeout=1ms
        if data:
            dst_port.write(data)   # forward ngay lập tức
            analyzer.feed(data, direction)  # phân tích song song
```

- ✅ Latency rất thấp (~0.1ms)
- ✅ Đơn giản, robust
- ❌ Không biết ranh giới frame cho đến khi gap xảy ra

**Chiến lược 2: Frame-level forward (phức tạp hơn, có thể inject)**

```python
# Pseudo-code  
async def forward_loop(src_port, dst_port, direction):
    parser = ModbusFrameParser(baudrate=9600)
    while True:
        data = src_port.read(256)
        if data:
            frames = parser.feed_and_get_frames(data)
            for frame in frames:
                # Có thể modify frame trước khi forward
                if should_inject(frame):
                    dst_port.write(modified_frame)
                else:
                    dst_port.write(frame.raw)
                analyzer.ingest(frame, direction)
```

- ✅ Biết rõ ranh giới frame → có thể inject/modify
- ❌ Thêm delay ~3.5 char times (đợi gap để xác nhận frame kết thúc)
- ❌ Phức tạp hơn, risk buffer overflow khi bus bận

**Khuyến nghị:** Dùng **Chiến lược 1** (byte-level) để đảm bảo transparent. Phân tích frame trong thread riêng.

### Kiến trúc phần mềm Inline Proxy

```
                    ┌─────────────────────────────────────┐
                    │            InlineProxy               │
                    │                                     │
    /dev/ttyUSB0    │  ┌─────────┐     ┌─────────┐       │  /dev/ttyUSB1
  ◄─────────────────┤──│ ComA RX │────►│ ComB TX │───────┤─────────────────►
  Master            │  │         │  │  │         │       │          Slaves
  ─────────────────►├──│ ComA TX │◄───│ ComB RX │───────┤◄─────────────────
                    │  └─────────┘  │  └─────────┘       │
                    │               │                     │
                    │          ┌────┴────┐                │
                    │          │Analyzer │                │
                    │          │ + Logs  │                │
                    │          └─────────┘                │
                    └─────────────────────────────────────┘
```

---

## 4. Bảng so sánh chẩn đoán

| Chẩn đoán | Passive (1 COM) | Inline Proxy (2 COM) | Ghi chú |
|-----------|:-:|:-:|---------|
| CRC errors | ✅ | ✅ | Inline phân biệt được lỗi ở chiều nào |
| Junk bytes | ✅ | ✅ | Inline biết junk từ Master hay Slave |
| Bus silence | ✅ | ✅ | — |
| Bus utilization | ✅ | ✅ | Inline tính riêng cho từng chiều |
| Chatty master | ✅ | ✅ | — |
| Exception rate | ✅ | ✅ | — |
| **Response time** | ⚠️ Heuristic | ✅ **Chính xác** | Inline đo `ts_B − ts_A` |
| **No response** | ⚠️ Heuristic | ✅ **Chính xác** | Không false positive |
| Direction violation | ❌ | ✅ | Slave tự gửi frame |
| Late response | ❌ | ✅ | Response sau timeout |
| Broadcast response | ❌ | ✅ | Vi phạm Modbus spec |
| Duplicate slave ID | ❌ | ✅ | 2+ response cho 1 request |
| Frame corruption | ❌ | ✅ | CRC OK 1 chiều, fail chiều kia |
| **Frame injection** | ❌ | ✅ (opt) | Test response của slave |

---

## 5. Chi tiết kỹ thuật phân tích frame

### Modbus RTU Function Codes

| FC | Tên | Request PDU | Response PDU |
|----|-----|-------------|--------------|
| `0x01` | Read Coils | `addr(2) + qty(2)` = 8B | `byte_count(1) + data(N)` |
| `0x02` | Read Discrete Inputs | `addr(2) + qty(2)` = 8B | `byte_count(1) + data(N)` |
| `0x03` | Read Holding Registers | `addr(2) + qty(2)` = 8B | `byte_count(1) + data(N)` |
| `0x04` | Read Input Registers | `addr(2) + qty(2)` = 8B | `byte_count(1) + data(N)` |
| `0x05` | Write Single Coil | `addr(2) + value(2)` = 8B | Echo request |
| `0x06` | Write Single Register | `addr(2) + value(2)` = 8B | Echo request |
| `0x0F` | Write Multiple Coils | `addr(2) + qty(2) + bc(1) + data(N)` | `addr(2) + qty(2)` = 8B |
| `0x10` | Write Multiple Registers | `addr(2) + qty(2) + bc(1) + data(N)` | `addr(2) + qty(2)` = 8B |

### Exception Response Format

Khi slave trả lỗi, function code có **bit 7 set** (OR with `0x80`):

```
Request:   [01] [03] [00 00] [00 01] [84 0A]     ← Read Holding Reg #0, qty=1
Exception: [01] [83] [02] [C0 F1]                 ← FC=0x83 (0x03|0x80), ExCode=0x02
                                                     → "Illegal Data Address"
```

| Exception Code | Tên | Nguyên nhân phổ biến |
|:-:|------|----------------------|
| 01 | Illegal Function | Slave không hỗ trợ FC này |
| 02 | Illegal Data Address | Đọc/ghi register không tồn tại |
| 03 | Illegal Data Value | Giá trị ghi ngoài phạm vi |
| 04 | Server Device Failure | Lỗi nội bộ slave (hardware) |
| 05 | Acknowledge | Slave đang xử lý, chờ |
| 06 | Server Device Busy | Slave bận, thử lại sau |

### Timing Constraints (theo Modbus Spec)

```
Inter-character timeout:  1.5 × character_time (bytes trong 1 frame phải liên tục)
Inter-frame silence:      3.5 × character_time (gap giữa 2 frame)

@9600:  1 char = 11/9600 = 1.146ms → gap = 4.01ms
@19200: 1 char = 11/19200 = 0.573ms → gap = 2.01ms  
@38400: 1 char = 11/38400 = 0.286ms → gap = 1.75ms (minimum)
@115200: spec says use fixed 1.75ms
```

---

## 6. Cài đặt và sử dụng

### Mode 1: Passive Sniffer

```bash
# Standalone CLI
python3 modbus_sniffer.py --port /dev/ttyUSB0 --baud 9600 --duration 60 --frames

# Dashboard (web UI)
python3 dashboard.py --config config.yaml
# → http://<ip>:8766
```

### Mode 2: Inline Proxy (chưa implement)

> **Trạng thái:** Thiết kế sẵn, chưa implement. Cần thêm `modbus_proxy.py`.

Lệnh dự kiến:
```bash
python3 modbus_proxy.py \
  --master-port /dev/ttyUSB0 \
  --slave-port /dev/ttyUSB1 \
  --baud 9600 \
  --duration 120
```

### Config file (`config.yaml`)

```yaml
serial:
  port: /dev/ttyUSB0        # Mode 1: sniffer port
  baudrate: 9600
  parity: "N"
  stopbits: 1

# Mode 2 (khi implement proxy)
proxy:
  enabled: false
  master_port: /dev/ttyUSB0  # Nối Master
  slave_port: /dev/ttyUSB1   # Nối Slaves
  forward_strategy: "byte"   # "byte" hoặc "frame"

sniffer:
  enabled: true
  diag_interval_s: 5

api:
  port: 8766
```

### Đấu nối phần cứng Mode 2

```
┌──────────┐         ┌─────────────────────────┐         ┌──────────┐
│          │  A(+)   │  USB-RS485 #1 (ComA)    │         │          │
│  MASTER  ├────────►│  /dev/ttyUSB0           │         │  SLAVE 1 │
│  PLC /   │  B(-)   │  Pi 5 nhận request      │         │  VFD /   │
│  SCADA   ├────────►│                         │         │  Meter   │
│          │  GND    │         forward ──────┐ │         │          │
│          ├────────►│                       │ │         │          │
└──────────┘         │  USB-RS485 #2 (ComB)  │ │         └──────────┘
                     │  /dev/ttyUSB1         ▼ │  A(+)    ┌──────────┐
                     │  Pi 5 gửi tới slaves  ──┼────────►│  SLAVE 2 │
                     │                         │  B(-)   │          │
                     │  ◄── response ──────────┼────────►│          │
                     └─────────────────────────┘  GND    └──────────┘
```

> ⚠️ **Quan trọng:** Đảm bảo GND chung giữa 2 adapter và tất cả các thiết bị.

---

## Phụ lục: Tóm tắt kiến trúc code

```
tools/modbus/
├── modbus_sniffer.py    ← CRC-16, FrameParser, HealthAnalyzer, ModbusSniffer
├── dashboard.py         ← FastAPI server (port 8766)
├── modbus_proxy.py      ← [TODO] Inline proxy với 2 COM ports
├── config.yaml          ← Cấu hình serial + sniffer + proxy
├── requirements.txt
├── modbus-rtu-tools.service
└── static/
    └── index.html       ← Dashboard UI (dark theme, 3 tabs)
```
