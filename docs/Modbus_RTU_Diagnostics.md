# Modbus RTU Sniffer & Diagnostics — Deep Dive kỹ thuật

> Tài liệu giải thích **bản chất** kỹ thuật từ tầng vật lý đến tầng ứng dụng.  
> Đọc xong sẽ hiểu hoàn toàn cách sniffer bắt frame, parse, và chẩn đoán lỗi bus.

---

## Mục lục
1. [Tầng vật lý: RS-485](#1-tầng-vật-lý-rs-485)
2. [Tầng Data Link: Modbus RTU Frame](#2-tầng-data-link-modbus-rtu-frame)
3. [CRC-16 Modbus — Tại sao và như thế nào](#3-crc-16-modbus)
4. [Kỹ thuật Sniffer: Bắt frame từ dòng byte thô](#4-kỹ-thuật-sniffer)
5. [Kỹ thuật Diagnostics: 8 quy tắc chẩn đoán](#5-kỹ-thuật-diagnostics)
6. [Inline Proxy: Kỹ thuật MITM cho Modbus RTU](#6-inline-proxy)
7. [Dual-Pi: Cross-correlation giữa 2 điểm quan sát](#7-dual-pi)
8. [So sánh tổng hợp 3 mode](#8-so-sánh-tổng-hợp)

---

## 1. Tầng vật lý: RS-485

### RS-485 là gì?

RS-485 là chuẩn **điện** (không phải protocol) cho truyền dữ liệu nối tiếp trên **differential pair** (2 dây: A và B).

```
         ┌────────────────── RS-485 Bus ──────────────────┐
         │                                                │
   A ────┤─────────────────────────────────────────────── │
         │    differential voltage                        │
   B ────┤─────────────────────────────────────────────── │
         │                                                │
  GND ───┤─────────────────────────────────────────────── │
         │                                                │
      Master            Slave 1          Slave 2       Slave N
      (TX/RX)           (TX/RX)          (TX/RX)       (TX/RX)
```

**Tại sao differential?** — Vì nhiễu tác động LÊN CẢ 2 dây giống nhau. Receiver đo **hiệu điện áp (V_A − V_B)**, nên nhiễu common-mode bị triệt tiêu:

```
Không nhiễu:           Có nhiễu (+2V):
  A = 3.5V               A = 5.5V   (+2V)
  B = 1.5V               B = 3.5V   (+2V)
  V_A - V_B = 2.0V       V_A - V_B = 2.0V  ← vẫn giống!
```

### Half-duplex

RS-485 2-wire là **half-duplex**: một thời điểm chỉ có 1 thiết bị được phép truyền (driver enable = HIGH). Tất cả thiết bị khác phải ở chế độ nhận.

Đây là lý do Modbus RTU dùng mô hình **Master-Slave**: chỉ Master được quyền bắt đầu giao tiếp. Slave chỉ trả lời khi được hỏi.

```
Timeline:
  ──────┬─────────┬──────────┬─────────┬──────────┬─────────┬──────
        │  Req 1  │ gap  │  Rsp 1  │ gap  │  Req 2  │ gap
        │ Master  │      │ Slave 1 │      │ Master  │
        │ TX (DE) │      │ TX (DE) │      │ TX (DE) │
  ──────┴─────────┴──────┴─────────┴──────┴─────────┴──────

  Bus collision: nếu 2 thiết bị bật DE cùng lúc → tín hiệu bị phá → CRC fail
```

### Termination (điện trở đầu cuối)

RS-485 cần **120Ω termination** ở 2 đầu bus để chống phản xạ sóng:

```
  Master ─[120Ω]── A ──────────────────── A ──[120Ω]── Slave cuối
                   B ──────────────────── B
```

Nếu thiếu termination: tín hiệu bị phản xạ → **CRC errors ngẫu nhiên** (sniffer sẽ phát hiện).

### Sniffer tap vào đâu?

USB-RS485 adapter nối **song song** vào bus. Vì RS-485 cho phép **tối đa 32 unit loads** trên 1 bus, adapter thêm 1 unit load. Adapter chỉ bật chân **RX** (receiver enable), không bật **DE** (driver enable) → **không bao giờ truyền**, chỉ nghe.

```
  Master ──── A ───┬──────── Slave 1
                   │
  (sniffer) ──── A ┘   ← Tap: chỉ RX, DE=LOW
```

---

## 2. Tầng Data Link: Modbus RTU Frame

### Bản chất: không có start/stop delimiter

**Đây là khác biệt lớn nhất** giữa Modbus RTU và các protocol khác:

| Protocol | Frame delimiter |
|----------|----------------|
| HTTP | `\r\n\r\n` (header end) |
| BACnet MS/TP | Preamble `0x55 0xFF` |
| Modbus ASCII | `:` (start) + `\r\n` (end) |
| **Modbus RTU** | **Không có!** Dùng im lặng (silence) |

Modbus RTU frame bắt đầu và kết thúc bằng **khoảng im lặng ≥ 3.5 character times**. Đây là thách thức lớn nhất khi viết sniffer.

### Frame structure

```
┌──────────────────────── Modbus RTU ADU ────────────────────────┐
│                                                                │
│  ┌──────────┬───────────────┬─────────────┬──────────────────┐ │
│  │ Slave ID │ Function Code │   Data      │    CRC-16        │ │
│  │ 1 byte   │   1 byte      │ 0-252 bytes │   2 bytes        │ │
│  │ (0-247)  │  (1-127)      │             │ (LSB first)      │ │
│  └──────────┴───────────────┴─────────────┴──────────────────┘ │
│                                                                │
│  Max ADU size: 256 bytes (1 + 1 + 252 + 2)                    │
│  Min ADU size: 4 bytes   (1 + 1 + 0 + 2)                     │
└────────────────────────────────────────────────────────────────┘
```

### Slave ID

```
  0         = Broadcast (tất cả slave nhận, KHÔNG được trả lời)
  1 - 247   = Unicast (slave trả lời nếu address match)
  248 - 255 = Reserved
```

### Function Code

```
  Bit 7 = 0 → Normal request/response
  Bit 7 = 1 → Exception response (slave báo lỗi)

  Ví dụ:
    FC = 0x03 → Read Holding Registers (normal)
    FC = 0x83 → Exception response cho FC 0x03 (0x03 | 0x80)
```

### Timing rules (quan trọng cho sniffer)

```
Character time = 11 bits / baudrate
  (1 start + 8 data + 1 parity + 1 stop = 11 bits)

┌─────────┬────────────────┬───────────┬───────────┐
│ Baudrate│ Char time      │ 1.5× (max │ 3.5× (min │
│         │                │ intra-    │ inter-    │
│         │                │ frame gap)│ frame gap)│
├─────────┼────────────────┼───────────┼───────────┤
│  9600   │ 1.146 ms       │ 1.719 ms  │ 4.010 ms  │
│ 19200   │ 0.573 ms       │ 0.859 ms  │ 2.005 ms  │
│ 38400   │ 0.286 ms       │ 0.430 ms  │ 1.750 ms* │
│ 115200  │ 0.095 ms       │ 0.143 ms  │ 1.750 ms* │
└─────────┴────────────────┴───────────┴───────────┘

* Spec bổ sung: ≥38400 baud → dùng fixed 1.75ms thay vì tính
```

Ý nghĩa:
- **Intra-frame gap < 1.5 char**: bytes TRONG cùng 1 frame phải liên tục
- **Inter-frame gap ≥ 3.5 char**: khoảng im lặng GIỮA 2 frame

```
  ─── frame 1 ───      gap ≥ 3.5T      ─── frame 2 ───
  [01 03 00 00 00 01 84 0A]    ~~~~    [01 03 02 00 64 B9 AF]
  ▲                        ▲   ▲  ▲   ▲
  │                        │   │  │   │
  frame start              │  3.5T min │
                     frame end     frame start
```

---

## 3. CRC-16 Modbus

### Tại sao cần CRC?

RS-485 bus có thể bị:
- Nhiễu điện từ (EMI) → bit bị lật
- Phản xạ sóng (thiếu termination) → bit bị méo
- Bus collision → 2 thiết bị nói cùng lúc → dữ liệu bị phá

CRC-16 phát hiện **99.998%** lỗi bit (so với checksum chỉ ~50%).

### Thuật toán (step by step)

```
Polynomial: 0xA001 (bit-reversed form of 0x8005)
Initial value: 0xFFFF

For each byte in [Slave ID, Function Code, Data...]:
  1. CRC = CRC XOR byte
  2. Repeat 8 times:
     if CRC bit 0 = 1:
       CRC = (CRC >> 1) XOR 0xA001
     else:
       CRC = CRC >> 1

Result: 16-bit CRC, đặt LSB trước trong frame
```

**Ví dụ cụ thể** — tính CRC cho request "Read Holding Register 0 từ slave 1":

```
Data:     01  03  00  00  00  01
          │   │   └──────┘  └──┘
       slave FC  start_addr  quantity

Step 1: CRC = 0xFFFF XOR 0x01 = 0xFFFE
  → 8 iterations of shift/XOR...
  → CRC after byte 0x01: 0xC0C1

Step 2: CRC = 0xC0C1 XOR 0x03 = 0xC0C2
  → 8 iterations...
  → CRC after byte 0x03: 0x0141

...continue for all bytes...

Final CRC: 0x0A84

In frame (LSB first): 84 0A

Full frame: [01] [03] [00 00] [00 01] [84 0A]
```

### Lookup Table (tối ưu)

Thay vì 8 shift/XOR per byte, tính sẵn bảng 256 entry:

```python
# Build table: mỗi entry = CRC contribution cho 1 byte value
TABLE = [0] * 256
for i in range(256):
    crc = i
    for _ in range(8):
        crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    TABLE[i] = crc

# Sử dụng: 1 lookup + 1 XOR per byte thay vì 8 iterations
def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc = (crc >> 8) ^ TABLE[(crc ^ b) & 0xFF]
    return crc
```

Performance: **~8× nhanh hơn** bit-by-bit. Quan trọng khi parse hàng nghìn frame/giây.

---

## 4. Kỹ thuật Sniffer: Bắt frame từ dòng byte thô

### Thách thức cốt lõi

Serial port cho ta **dòng byte liên tục**, không phân biệt ranh giới frame:

```
Raw bytes từ serial port:
  01 03 00 00 00 01 84 0A   01 03 02 00 64 B9 AF   01 04 00 ...
  ─────── frame 1 ──────   ─────── frame 2 ──────   ─── frame 3 ...

Nhưng ta nhận:
  buffer: [01 03 00 00 00 01 84 0A 01 03 02 00 64 B9 AF 01 04 00 ...]
           ^-- không biết frame 1 kết thúc ở đâu!
```

### State Machine: Gap-based detection

```
                    ┌──────────────────────────────┐
                    │                              │
                    ▼                              │
              ┌───────────┐    byte received       │
     ─────►   │  IDLE     ├──────────────────►┌────┴──────┐
   (power on) │ buf=empty │                   │COLLECTING │
              └───────────┘                   │ buf += b  │
                    ▲                         │ ts = now  │
                    │         gap detected    └────┬──────┘
                    │       (now - ts > 3.5T)      │
                    │              │                │
                    │         ┌────▼──────┐        │
                    │         │ TRY_PARSE │        │
                    │         │           │        │
                    │         └────┬──────┘        │
                    │              │                │
                    │    ┌─────────┴──────────┐     │
                    │    │                    │     │
                    │   len ≥ 4?             len < 4
                    │    │                    │     │
                    │   YES                  NO    │
                    │    │                    │     │
                    │  Parse frame          Junk   │
                    │  Check CRC           bytes   │
                    │    │                    │     │
                    └────┴────────────────────┘     │
                         buf = clear               │
                         ◄─────────────────────────┘
                              next byte
```

### Pseudo-code chi tiết

```python
class FrameParser:
    def __init__(self, baudrate):
        self.gap_threshold = 3.5 * 11 / baudrate  # seconds
        self.buf = bytearray()
        self.last_byte_ts = 0

    def feed(self, byte, timestamp):
        # 1. Kiểm tra gap
        if self.buf and (timestamp - self.last_byte_ts) > self.gap_threshold:
            # Gap detected → thử parse buffer hiện tại
            self.try_parse()

        # 2. Thêm byte vào buffer
        if not self.buf:
            self.frame_start_ts = timestamp
        self.buf.append(byte)
        self.last_byte_ts = timestamp

    def try_parse(self):
        data = bytes(self.buf)
        self.buf.clear()

        if len(data) < 4:        # Quá ngắn → junk
            self.junk_count += len(data)
            return

        if len(data) > 256:      # Quá dài → junk
            self.junk_count += len(data)
            return

        # Parse components
        slave_id = data[0]
        func_code = data[1]
        payload = data[2:-2]
        crc_received = data[-2] | (data[-1] << 8)  # LSB first
        crc_computed = crc16(data[:-2])

        frame = Frame(
            slave_id=slave_id,
            function_code=func_code,
            data=payload,
            crc_ok=(crc_received == crc_computed),
            timestamp=self.frame_start_ts,
        )

        self.on_frame(frame)
```

### Vấn đề thực tế với serial port

**Problem 1: OS buffering**
Hệ điều hành buffer bytes trước khi đưa lên userspace. Có thể nhận cục 50 bytes cùng lúc thay vì từng byte.

**Giải pháp:** Dùng `serial.timeout=0.001` (1ms) → read thường xuyên, giảm buffering. Đo gap dựa trên `time.perf_counter()` thay vì dựa vào timing của read().

**Problem 2: Baud rate mismatch**
Nếu sniffer đặt baud rate khác bus → **mọi byte đều garbage**.

**Giải pháp:** Sniffer mode detect: thử nhiều baud rate, đếm frame valid ở mỗi rate:

```
9600:   0 valid frames, 847 junk bytes
19200:  0 valid frames, 412 junk bytes
38400:  127 valid frames, 3 junk bytes  ← ĐÚNG!
```

**Problem 3: Partial frame ở đầu/cuối capture**
Bắt đầu capture giữa chừng 1 frame → bytes đầu là rác.

**Giải pháp:** Bỏ frame đầu tiên nếu CRC fail (đợi gap đầu tiên để sync).

---

## 5. Kỹ thuật Diagnostics: 8 quy tắc chẩn đoán

### Bản chất: Pattern Recognition trên stream of frames

Sniffer thu thập stream of frames, mỗi frame có attributes:
```
{timestamp, slave_id, function_code, data_length, crc_ok, is_exception}
```

Diagnostic engine chạy **8 quy tắc** trên aggregate data:

### Rule 1: HIGH_CRC_ERRORS

```
Metric:    bad_crc_count / total_frame_count × 100

Trigger:   > 5% → warning
           > 15% → critical

Nguyên nhân:
  • Dây A/B bị đứt hoặc tiếp xúc kém
  • Thiếu termination 120Ω
  • Baud rate mismatch giữa các thiết bị
  • Nhiễu EMI (VFD, motor, relay gần bus)
  • Bus quá dài (>1200m @9600 hoặc >700m @19200)

Cách sniffer phát hiện:
  Mỗi frame nhận được → tính CRC → so với 2 byte cuối
  Nếu không khớp → bad_crc_count++

Ví dụ:
  Nhận: [01 03 02 00 64 B9 FF]
  Tính CRC(01 03 02 00 64) = 0xAFB9
  Trong frame: 0xFFB9 ← KHÔNG KHỚP → CRC error
```

### Rule 2: JUNK_BYTES

```
Metric:    junk_bytes / total_bytes × 100

Trigger:   > 2% → warning
           > 10% → critical

Bản chất:
  Bytes nhận được mà KHÔNG thuộc frame nào:
    • Buffer < 4 bytes khi gap xảy ra
    • Buffer > 256 bytes (Modbus RTU max)
    • Bytes random do noise

Ví dụ:
  Nhận: [FF FF 01 03 00 00 00 01 84 0A]
         ^^^^^ junk (gap trước → flush → chỉ 2 bytes → junk)
```

### Rule 3: BUS_SILENCE

```
Metric:    max(gap between consecutive frames) in milliseconds

Trigger:   > 5000ms → warning
           > 15000ms → critical

Bản chất:
  Theo dõi khoảng cách giữa frame cuối và frame mới.
  Nếu bus im lặng quá lâu → Master có thể offline.

  Lưu ý: khác với inter-frame gap 3.5T (đó là BÌNH THƯỜNG).
  BUS_SILENCE nói về im lặng BẤT THƯỜNG (giây, không phải ms).
```

### Rule 4: HIGH_BUS_UTILIZATION

```
Metric:    (total_bytes × 11 bits/char) / (baudrate × elapsed_seconds) × 100

Trigger:   > 70% → warning
           > 90% → critical

Bản chất:
  RS-485 half-duplex → bandwidth bị chia đôi (vì phải đợi).
  Bus utilization > 70% → ít "chỗ trống" → slave có thể bị timeout.

Ví dụ @9600 baud, 60s capture:
  Total bytes: 48000
  Bits used: 48000 × 11 = 528000
  Bandwidth: 9600 × 60 = 576000
  Utilization: 528000/576000 = 91.7% → CRITICAL
```

### Rule 5: CHATTY_MASTER

```
Metric:    requests_to_slave_X / elapsed_seconds

Trigger:   > 50 requests/s → warning per slave

Bản chất:
  Nếu Master poll 1 slave quá nhanh, slave có thể không kịp xử lý.
  Cũng chiếm quá nhiều bandwidth → các slave khác bị chậm.

  Ví dụ: PLC poll energy meter 100 lần/giây → meter respond chậm dần
           → timeout → Master nghĩ meter offline
```

### Rule 6: EXCEPTION_RESPONSES

```
Metric:    exception_responses / total_responses × 100

Trigger:   > 10% → warning
           > 25% → critical

Bản chất:
  Exception response có bit 7 set trong function code:
    Normal: FC = 0x03 (Read Holding Registers)
    Exception: FC = 0x83 = 0x03 | 0x80

  Kèm theo exception code:
    01 = Illegal Function    → slave không hỗ trợ FC
    02 = Illegal Data Addr   → đọc register không tồn tại
    03 = Illegal Data Value  → ghi giá trị ngoài phạm vi
    04 = Device Failure      → lỗi phần cứng slave

  Sniffer đếm exception BY slave ID và BY exception code
  → biết slave nào bị lỗi gì nhiều nhất.
```

### Rule 7: SLOW_RESPONSE (Passive mode — heuristic)

```
Metric:    estimated response time per slave

Trigger:   > 500ms → warning

Bản chất (heuristic):
  Passive sniffer KHÔNG biết chắc frame nào là request, frame nào là response.
  Dùng heuristic:
    1. Frame match request pattern (8 bytes, FC=01-06) → ghi nhận pending request
    2. Frame tiếp theo cùng slave_id → coi là response
    3. Response time ≈ ts_response - ts_request

  ⚠️ Sai khi:
    • Bus có nhiều slave → frame chen giữa
    • Broadcast request → không có response
```

### Rule 8: SLAVE_NO_RESPONSE (Passive mode — heuristic)

```
Metric:    pending request > 5 seconds without matching response

Trigger:   > 5000ms → warning

Bản chất (heuristic):
  Ghi nhận request tới slave X.
  Nếu >5s không thấy response từ slave X → coi là no response.

  ⚠️ Sai khi:
    • Slave respond nhưng CRC fail → sniffer thấy bad frame, không match
    • Response bị parser lỗi (junk)
```

---

## 6. Inline Proxy: Kỹ thuật MITM

### Bản chất: tách bus thành 2 segment

```
TRƯỚC (bus bình thường):
  Master ════════════════════════════════════ Slaves
                   1 segment, tất cả trên 1 bus

SAU (inline proxy):
  Master ════ ComA ═══ [Pi] ═══ ComB ════ Slaves
              segment 1          segment 2
```

Pi đọc từ ComA, gửi sang ComB (và ngược lại). **Vì ComA chỉ kết nối Master, ComB chỉ kết nối Slaves**, ta biết chắc:

```
  Frame từ ComA RX = Master gửi = REQUEST
  Frame từ ComB RX = Slave gửi  = RESPONSE
```

### Forward strategy: Byte-level (recommended)

```python
async def forward(src, dst):
    """Forward mỗi byte ngay khi nhận được."""
    while True:
        data = src.read(256)     # đọc tối đa 256 bytes
        if data:
            dst.write(data)      # gửi ngay, không đợi
            parser.feed(data)    # phân tích song song
```

**Tại sao byte-level chứ không frame-level?**

| | Byte-level | Frame-level |
|---|---|---|
| Latency | ~0.1ms | +3.5 char times (đợi gap) |
| Transparency | 100% | 99% (thêm gap delay) |
| Complexity | Thấp | Cao (đợi frame hoàn chỉnh) |
| Can inject? | Không | Có |
| Can modify? | Không | Có |

Frame-level chỉ cần khi muốn **inject** hoặc **modify** frame trước khi forward. Cho mục đích diagnostics, byte-level là tối ưu.

### Request-Response Matching (chính xác 100%)

```
Timeline (inline proxy biết direction):

  ComA RX:   ├── [01 03 00 00 00 01 84 0A] ──────────────────────────────
  (Master)   │   ts_A = 0.000s
             │   → Forward to ComB TX
             │
  ComB RX:   ├────────────────────────── [01 03 02 00 64 B9 AF] ──────
  (Slave)    │                           ts_B = 0.012s
             │                           → Forward to ComA TX
             │
  Match:     Slave 1, FC 03
             Response time = ts_B - ts_A = 12ms ← CHÍNH XÁC
```

So với passive sniffer:
```
  Passive:   ├── [01 03 00 00 00 01 84 0A] ── [01 03 02 00 64 B9 AF] ──
  (1 port)   │   Cùng port, cùng direction → PHẢI ĐOÁN cái nào là request
             │   Có thể nhầm nếu bus bận
```

### 7 chẩn đoán mới chỉ có ở inline proxy

| # | Code | Bản chất | Passive có? |
|---|------|----------|:-:|
| 1 | `EXACT_RESPONSE_TIME` | `ts_ComB − ts_ComA` → ms chính xác | ❌ (heuristic) |
| 2 | `NO_RESPONSE_EXACT` | Request trên ComA, timeout trên ComB | ❌ (heuristic) |
| 3 | `DIRECTION_VIOLATION` | Frame từ ComB mà không có request từ ComA | ❌ |
| 4 | `LATE_RESPONSE` | Response đến sau khi Master đã gửi request mới | ❌ |
| 5 | `BROADCAST_RESPONSE` | Request broadcast (slave=0) từ ComA → có response từ ComB | ❌ |
| 6 | `DUPLICATE_SLAVE_ID` | 1 request → 2+ responses trên ComB | ❌ |
| 7 | `FRAME_CORRUPTION_DIR` | CRC OK trên ComA nhưng fail trên ComB | ❌ |

### Phát hiện DIRECTION_VIOLATION

```
Normal flow:
  ComA: REQ(slave=1, FC=03)  →  ComB: RSP(slave=1, FC=03)  ✓

Violation:
  ComA: (nothing)             →  ComB: RSP(slave=1, FC=03)  ✗
  Slave 1 tự gửi frame không được hỏi!
  → Firmware bug hoặc slave bị reset
```

### Phát hiện DUPLICATE_SLAVE_ID

```
  ComA: REQ(slave=5, FC=03)
  ComB: RSP(slave=5, FC=03, data=[00 64])     ← device A trả lời
  ComB: RSP(slave=5, FC=03, data=[01 F4])     ← device B cũng trả lời!

  → 2 thiết bị cùng address 5 → bus collision → CRC errors
```

### Phát hiện BROADCAST_RESPONSE

```
  ComA: REQ(slave=0, FC=06, reg=100, val=1)   ← broadcast write
  ComB: RSP(slave=0, FC=06, reg=100, val=1)   ← AI ĐÓ TRẢ LỜI!

  → Vi phạm Modbus spec: "Slaves shall not respond to broadcast"
  → Firmware bug trên slave
```

---

## 7. Dual-Pi: Cross-correlation giữa 2 điểm quan sát

### Bản chất: biến bus thành "oscilloscope 2 kênh"

Khi chỉ có 1 điểm quan sát (1 Pi), bạn thấy bus **tại 1 vị trí**. Thêm Pi thứ 2 tại vị trí khác
→ bạn có thể **so sánh** tín hiệu RS-485 ở **2 điểm khác nhau trên bus** → phát hiện lỗi phụ thuộc vào vị trí.

### Topology

```
  ┌────────────────────── RS-485 Bus ──────────────────────────────────┐
  │                                                                    │
  │   ┌────────┐    ┌─[Pi-1 Inline]──┐    ┌────┐ ┌────┐    ┌─[Pi-2]─┐│
  │   │ MASTER ├─A──┤ComA        ComB├─A──┤ S1 ├─┤ S2 ├─A──┤Passive ││
  │   │ PLC    ├─B──┤                ├─B──┤    ├─┤    ├─B──┤Sniffer ││
  │   └────────┘    └────────────────┘    └────┘ └────┘    └────────┘│
  │   [120Ω]        segment 1 │ segment 2                   [120Ω]  │
  │                           │                                      │
  └───────────────────────────┴──────────────────────────────────────┘
                              │
                      Pi-1 cắt bus ở đây
                      Forward A⇄B

  Pi-1: INLINE MODE — ở ĐẦU line (giữa Master và Slaves)
        • Biết direction (ComA=Master, ComB=Slave)
        • Forward traffic
        • 15 diagnostics (8 base + 7 inline)

  Pi-2: PASSIVE MODE — ở CUỐI line
        • Tap RX-only
        • Không biết direction
        • 8 diagnostics (base)
        • NHƯNG: vị trí cuối = chất lượng tín hiệu ở worst-case point
```

### Tại sao cuối line quan trọng?

RS-485 là **bus tuyến tính**. Tín hiệu yếu dần theo khoảng cách:

```
  Biên độ tín hiệu (V_A - V_B):

  3.0V ┤████████░░░░░░░░░░░░░░░░░░░░░░▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▓▓▓▓▓▓▓▓▓▓▓
       │                                                           │
  2.0V ┤   Pi-1 thấy frame rõ                                     │
       │   (gần Master)                                            │
  1.0V ┤                                               Pi-2 thấy  │
       │                                               frame yếu  │
  0.2V ┤ ··········································· threshold ···│···
       │                                               có thể lỗi │
       └───────────────────────────────────────────────────────────┘
       Master                                              Slave cuối
       (đầu bus)                                           (cuối bus)
```

→ Nếu tín hiệu "vừa đủ" ở cuối line → **CRC errors chỉ xuất hiện ở Pi-2, không ở Pi-1**.

### 8 chẩn đoán mới chỉ có khi dùng 2 Pi

| # | Code | Bản chất | Mode 1? | Mode 2? | Mode 3? |
|---|------|----------|:--:|:--:|:--:|
| 16 | `SIGNAL_DEGRADATION` | CRC % ở Pi-2 > Pi-1 → suy hao tín hiệu | ❌ | ❌ | ✅ |
| 17 | `SEGMENT_FAULT` | Frame mất hoàn toàn trên 1 đoạn | ❌ | ❌ | ✅ |
| 18 | `PROPAGATION_DELAY` | Đo delay truyền tải qua cáp | ❌ | ❌ | ✅ |
| 19 | `TERMINATION_FAULT` | Phương hướng CRC errors → thiếu termination | ❌ | ❌ | ✅ |
| 20 | `NOISE_LOCALIZATION` | Xác định vùng nhiễu trên bus | ❌ | ❌ | ✅ |
| 21 | `FRAME_LOSS` | Frame gửi từ Pi-1 nhưng Pi-2 không thấy | ❌ | ❌ | ✅ |
| 22 | `SLAVE_INTERFERENCE` | Slave gây nhiễu các slave khác cùng segment | ❌ | ❌ | ✅ |
| 23 | `TIMING_DRIFT` | Sai lệch timing giữa 2 điểm quan sát | ❌ | ❌ | ✅ |

---

### Rule 16: SIGNAL_DEGRADATION — Suy hao tín hiệu theo khoảng cách

```
Bản chất:
  So sánh tỷ lệ CRC errors tại 2 điểm:
    CRC_error_rate_Pi1 = bad_frames_Pi1 / total_frames_Pi1
    CRC_error_rate_Pi2 = bad_frames_Pi2 / total_frames_Pi2

  Nếu:  CRC_Pi2 >> CRC_Pi1  → tín hiệu suy yếu theo khoảng cách

Trigger:
  CRC_Pi2 > CRC_Pi1 + 5% → warning (SIGNAL_DEGRADATION)
  CRC_Pi2 > CRC_Pi1 + 15% → critical

Nguyên nhân:
  • Bus quá dài (>1200m @9600, >700m @19200)
  • Cáp kém chất lượng (không twisted pair, không shielded)
  • Quá nhiều tap/node trên bus (>32 unit loads)
  • Mối nối cáp bị lỏng ở giữa bus

Ví dụ:
  Pi-1 (đầu bus): 200 frames, 1 bad CRC (0.5%)
  Pi-2 (cuối bus): 200 frames, 24 bad CRC (12%)
  → SIGNAL_DEGRADATION: 12% - 0.5% = 11.5% gap → critical
  → Kết luận: cáp hoặc khoảng cách gây suy hao

  Passive sniffer KHÔNG THỂ phát hiện vì chỉ thấy CRC tại 1 điểm.
```

### Rule 17: SEGMENT_FAULT — Frame mất trên 1 đoạn

```
Bản chất:
  Pi-1 forward frame từ ComB ra bus segment 2.
  Pi-2 ở cuối segment 2 PHẢI thấy frame đó.
  Nếu Pi-2 KHÔNG thấy → đứt dây hoặc hở mạch giữa Pi-1 và Pi-2.

Detection (cross-correlation):
  1. Pi-1 ghi log mỗi frame forward sang ComB: {seq, ts, slave_id, fc, crc}
  2. Pi-2 ghi log mỗi frame nhận: {seq, ts, slave_id, fc, crc}
  3. So sánh 2 log (cần sync thời gian bằng NTP hoặc PTP):
     - Frame có trong Pi-1 log nhưng KHÔNG trong Pi-2 → SEGMENT_FAULT
     - Frame có trong cả 2 nhưng CRC khác → FRAME_CORRUPTION trên đường

Ví dụ:
  Pi-1 log:  ts=1.000 S=3 FC=03 CRC=✓
  Pi-2 log:  (không có frame tương ứng trong window ±50ms)
  → SEGMENT_FAULT: frame mất giữa Pi-1 và Pi-2
  → Kiểm tra cáp đoạn Pi-1:ComB → Pi-2

  Nếu lỗi chỉ xảy ra khi slave 3 respond (nhưng request từ master OK):
  → Slave 3 có driver yếu, không đủ công suất truyền xa
```

### Rule 18: PROPAGATION_DELAY — Đo delay truyền tải qua cáp

```
Bản chất:
  Cùng 1 frame, Pi-1 thấy trước, Pi-2 thấy sau.
  Hiệu: Δt = ts_Pi2 - ts_Pi1

  Δt bao gồm:
    1. Propagation delay qua cáp (~5ns/m)
    2. Pi-1 forward delay (~0.1ms byte-level)
    3. Jitter do OS scheduling (~0-2ms)

  Với bus 500m: propagation = 500 × 5ns = 2.5µs (nhỏ, bị jitter che lấp)
  → Metric hữu ích hơn: đo JITTER (biến động Δt)

Detection:
  Nếu Δt ổn định ~1ms cho mọi frame → bus OK
  Nếu Δt dao động mạnh (1ms ~ 50ms) → bus có vấn đề
  Nếu Δt tăng dần theo thời gian → firmware bug (accumulating delay)

Yêu cầu:
  • Cả 2 Pi phải sync time (NTP hoặc PTP)
  • Độ chính xác NTP: ±5-10ms → đủ phát hiện anomaly lớn
  • Nếu dùng PTP: ±1µs → phát hiện cả propagation delay qua cáp
```

### Rule 19: TERMINATION_FAULT — Thiếu điện trở đầu cuối

```
Bản chất:
  RS-485 cần termination 120Ω ở 2 đầu bus.
  Thiếu terminaton → phản xạ sóng → CRC errors.

  "Dấu hiệu đặc trưng" của thiếu termination:
    • CRC errors CÓ PATTERN: lỗi theo hướng truyền
    • Lỗi khi slave XA gửi về (tín hiệu yếu + phản xạ)
    • Lỗi KHÔNG ĐỀU giữa 2 Pi

Detection (Dual-Pi):
  Trường hợp 1: Thiếu termination ĐẦU bus (gần Master)
    Pi-1: thấy nhiều CRC errors từ slave responses (phản xạ từ đầu bus)
    Pi-2: thấy ít CRC errors hơn (xa vị trí phản xạ)
    → TERMINATION_FAULT: master end

  Trường hợp 2: Thiếu termination CUỐI bus (gần Pi-2)
    Pi-1: ít CRC errors
    Pi-2: nhiều CRC errors (gần vị trí phản xạ)
    → TERMINATION_FAULT: slave end

  Trường hợp 3: Thiếu CẢ 2 đầu
    Pi-1: nhiều CRC errors
    Pi-2: nhiều CRC errors
    → TERMINATION_FAULT: both ends

So sánh với 1 Pi:
  1 Pi chỉ thấy "nhiều CRC" → KHÔNG BIẾT thiếu ở đầu nào.
  2 Pi → biết HƯỚNG phản xạ → biết đầu nào thiếu.
```

### Rule 20: NOISE_LOCALIZATION — Xác định vùng nhiễu

```
Bản chất:
  Nhiễu EMI từ VFD, motor, relay thường tác động lên 1 đoạn cáp cụ thể.
  Với 2 Pi, xác định nguồn nhiễu nằm ĐẦU hay CUỐI bus.

Detection:
  1. Đếm junk bytes (bytes random do noise):
     junk_Pi1 = junk bytes/s tại Pi-1
     junk_Pi2 = junk bytes/s tại Pi-2

  2. Nếu junk_Pi1 >> junk_Pi2:
     → Nhiễu ở đoạn Master → Pi-1 (segment 1)

  3. Nếu junk_Pi2 >> junk_Pi1:
     → Nhiễu ở đoạn Pi-1 → Pi-2 (segment 2)

  4. Nếu junk_Pi1 ≈ junk_Pi2:
     → Nhiễu ở giữa bus HOẶC nhiễu phủ toàn bộ

Correlation với thời gian:
  Nếu junk bursts xảy ra CÓ PATTERN (mỗi 100ms, mỗi 1s...)
  → Nhiễu từ thiết bị đóng/cắt theo chu kỳ (relay, PWM, VFD)

  Ghi timestamp mỗi junk burst ở cả 2 Pi:
    Pi-1: junk burst @ t=5.000, t=5.100, t=5.200 (every 100ms)
    Pi-2: junk burst @ t=5.001, t=5.101, t=5.201 (same pattern, 1ms later)
    → Nhiễu nguồn gần segment 1, lan sang segment 2
```

### Rule 21: FRAME_LOSS — Frame mất trên đường truyền

```
Bản chất:
  Khác SEGMENT_FAULT (frame mất hoàn toàn), FRAME_LOSS đếm tỷ lệ mất.

Detection:
  frame_loss_rate = (frames_Pi1 - frames_Pi2) / frames_Pi1 × 100

  Trigger:
    > 1% → warning
    > 5% → critical

  frame_loss_rate NÊN = 0% (mọi frame Pi-1 forward phải đến Pi-2).
  Nếu > 0%:
    • Dây hở mạch gián đoạn (loose connection → mất frame random)
    • Slave driver bị yếu → frame fade out trước khi đến cuối bus
    • Adapter bị overload (buffer overflow → drop bytes)

Phân tích sâu:
  Nếu FRAME_LOSS chỉ xảy ra với frame TỪ 1 slave cụ thể:
    Pi-1: forwarded 100 frames from slave 5
    Pi-2: received 87 frames from slave 5 → loss 13%

    Các slave khác: loss 0%

    → Slave 5 có driver output yếu (không đủ công suất truyền xa)
    → Hoặc: slave 5 ở nhánh rẽ (stub) quá dài
```

### Rule 22: SLAVE_INTERFERENCE — Slave gây nhiễu slave khác

```
Bản chất:
  Một slave bị hỏng có thể "kéo bus xuống" khi nó truyền,
  gây nhiễu cho tất cả thiết bị khác trên cùng segment.

Detection (correlation analysis):
  1. Pi-1 (inline) biết CHÍNH XÁC slave nào đang nói (từ ComB)
  2. Pi-2 (passive) thấy CRC errors

  Cross-correlate:
    Thời điểm CRC errors ở Pi-2 → map với slave nào đang TX ở Pi-1

    slavery_interference_score[slave_id] =
      CRC_errors_during_slave_TX / total_slave_TX_time

  Nếu 1 slave có score cao hơn hẳn:
    slave 3: interference score = 42% (CRC errors khi slave 3 đang TX)
    slave 1: interference score = 1%
    slave 2: interference score = 0%
    → Slave 3 gây nhiễu! (driver quá mạnh, rise/fall time quá nhanh,
       hoặc driver bị hỏng → output impedance sai)

  1 Pi KHÔNG THỂ làm vì:
    - Passive: không biết ai đang TX
    - Inline: biết ai TX nhưng chỉ thấy CRC ở 1 vị trí
    - DUAL: biết ai TX (Pi-1) + CRC ở vị trí khác (Pi-2)
```

### Rule 23: TIMING_DRIFT — Sai lệch timing giữa 2 điểm

```
Bản chất:
  Frame duration (từ byte đầu đến byte cuối) phải GIỐNG NHAU
  tại mọi điểm trên bus (vì baud rate cố định).

  Nếu frame duration khác nhau ở 2 Pi:
    duration_Pi1 = ts_last_byte - ts_first_byte (tại Pi-1)
    duration_Pi2 = ts_last_byte - ts_first_byte (tại Pi-2)

    drift = |duration_Pi1 - duration_Pi2|

  drift > 0 nghĩa là:
    • Baud rate mismatch giữa thiết bị trên 2 segment
    • Clock drift trên 1 thiết bị (crystal oscillator sai)
    • Adapter USB-RS485 có buffering khác nhau

Detection:
  Theo dõi drift cho mỗi slave:
    slave 1: avg drift = 0.02ms → OK
    slave 2: avg drift = 0.03ms → OK
    slave 7: avg drift = 1.5ms  → BAUD RATE MISMATCH!
    → Slave 7 có thể đang chạy 9550 baud thay vì 9600
       (crystal oscillator tolerance ±0.5%)
```

---

### Kiến trúc phần mềm Dual-Pi

```
┌────────────────────────────────────────────────────────────────┐
│                           Network                              │
│         (WiFi / Ethernet / Tailscale VPN)                      │
│                                                                │
│    ┌──────────────────────┐       ┌──────────────────────┐     │
│    │      Pi-1 (Inline)   │       │    Pi-2 (Passive)    │     │
│    │                      │       │                      │     │
│    │  modbus_proxy.py     │       │  modbus_sniffer.py   │     │
│    │  ├── DirectionalFrame│       │  ├── FrameParser     │     │
│    │  ├── ProxyAnalyzer   │       │  ├── HealthAnalyzer  │     │
│    │  └── 15 diagnostics  │       │  └── 8 diagnostics   │     │
│    │                      │       │                      │     │
│    │  dashboard.py :8766  │       │  dashboard.py :8766  │     │
│    │  └── /api/sniffer/   │       │  └── /api/sniffer/   │     │
│    │      report          │       │      report          │     │
│    └──────────┬───────────┘       └──────────┬───────────┘     │
│               │                              │                 │
│               └──── Cross-correlator ────────┘                 │
│                     (compare reports)                          │
│                                                                │
│    Trung tâm so sánh có thể chạy trên:                         │
│      • Pi-1 (pull report từ Pi-2 qua API)                      │
│      • Server riêng                                            │
│      • Manual: download 2 JSON reports và diff                  │
└────────────────────────────────────────────────────────────────┘
```

### Cách sync dữ liệu giữa 2 Pi

**Phương pháp 1: API polling (đơn giản)**

```python
# Chạy trên Pi-1 (hoặc server thứ 3)
import requests

report_pi1 = get_local_report()  # từ proxy analyzer
report_pi2 = requests.get("http://<pi2-ip>:8766/api/sniffer/report").json()

# Cross-correlate
crc_diff = report_pi2["bad_frame_pct"] - report_pi1["bad_frame_pct"]
if crc_diff > 5:
    alert("SIGNAL_DEGRADATION", f"CRC gap: {crc_diff}%")

frame_loss = report_pi1["total_frames"] - report_pi2["total_frames"]
if frame_loss > 0:
    alert("FRAME_LOSS", f"{frame_loss} frames lost")
```

**Phương pháp 2: MQTT (realtime)**

```
Pi-1 publish → MQTT broker ← Pi-2 publish
                    │
              Correlator subscribe
              (so sánh events realtime)
```

**Phương pháp 3: Centralized log + offline analysis**

```
Pi-1: ghi frame log ra file → SCP/rsync lên server
Pi-2: ghi frame log ra file → SCP/rsync lên server
Server: diff 2 file → báo cáo cross-correlation
```

---

## 8. So sánh tổng hợp 3 mode

| Capability | Mode 1: Passive | Mode 2: Inline | Mode 3: Dual-Pi |
|---|:--:|:--:|:--:|
| **Phần cứng** | 1 Pi, 1 USB-485 | 1 Pi, 2 USB-485 | 2 Pi, 3 USB-485 |
| **Ảnh hưởng bus** | Không | Có (Pi là SPOF) | Có (Pi-1 là SPOF) |
| CRC error detection | ✅ | ✅ | ✅ |
| Junk byte detection | ✅ | ✅ | ✅ |
| Bus silence | ✅ | ✅ | ✅ |
| Bus utilization | ✅ | ✅ | ✅ |
| Chatty master | ✅ | ✅ | ✅ |
| Exception analysis | ✅ | ✅ | ✅ |
| **Response time** | ⚠️ Heuristic | ✅ Exact | ✅ Exact |
| **No response** | ⚠️ Heuristic | ✅ Confirmed | ✅ Confirmed |
| Direction violation | ❌ | ✅ | ✅ |
| Late response | ❌ | ✅ | ✅ |
| Broadcast response | ❌ | ✅ | ✅ |
| Duplicate slave ID | ❌ | ✅ | ✅ |
| Frame corruption (per-dir) | ❌ | ✅ | ✅ |
| **Signal degradation** | ❌ | ❌ | ✅ |
| **Segment fault** | ❌ | ❌ | ✅ |
| **Propagation delay** | ❌ | ❌ | ✅ |
| **Termination fault (which end)** | ❌ | ❌ | ✅ |
| **Noise localization** | ❌ | ❌ | ✅ |
| **Frame loss rate** | ❌ | ❌ | ✅ |
| **Slave interference** | ❌ | ❌ | ✅ |
| **Timing drift** | ❌ | ❌ | ✅ |
| **Tổng diagnostics** | **8** | **15** | **23** |

### Khi nào dùng mode nào?

| Tình huống | Mode khuyến nghị | Lý do |
|-----------|:-:|------|
| Monitor 24/7, không can thiệp | **1** | An toàn, không ảnh hưởng bus |
| Debug slave không trả lời | **2** | Cần exact response time + no-response confirm |
| Bus CRC lỗi random, không rõ nguyên nhân | **3** | So sánh 2 điểm để biết suy hao hay nhiễu |
| Nghi ngờ cáp hỏng giữa 2 điểm | **3** | Frame loss + segment fault detection |
| Tìm slave gây nhiễu bus | **3** | Cross-correlate TX timing + CRC errors |
| Kiểm tra termination | **3** | So sánh pattern CRC ở 2 đầu bus |
| Debug firmware slave | **2** | Direction violation + broadcast response |
| Commissioning mới, kiểm tra tổng thể | **2→3** | Inline trước, thêm passive nếu cần |

---

## Phụ lục A: Sơ đồ đấu nối phần cứng

### Mode 2: Inline Proxy (1 Pi, 2 COM)

```
┌──────────────────────────────────────────────────────────────────┐
│                    Raspberry Pi 5 [Pi-1]                          │
│                                                                  │
│   USB Port 1          USB Port 2                                 │
│   ┌─────────┐        ┌─────────┐                                │
│   │USB-RS485│        │USB-RS485│                                 │
│   │ CH340   │        │ CH340   │                                 │
│   │ ComA    │        │ ComB    │                                 │
│   │/ttyUSB0 │        │/ttyUSB1 │                                 │
│   └────┬────┘        └────┬────┘                                 │
└────────┼──────────────────┼──────────────────────────────────────┘
         │                  │
    [120Ω]                  │
    ┌────┴────┐        ┌────┴────────────────────────────┐
    │ MASTER  │        │  Segment 2                      │
    │ PLC/    │        │                                 │
    │ SCADA   │        │  S1 ─── S2 ─── S3 ─── [120Ω]  │
    └─────────┘        └─────────────────────────────────┘
```

### Mode 3: Dual-Pi (2 Pi, 3 COM)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│   ┌─────────┐   ┌──── Pi-1 (Inline) ────┐   ┌───┐ ┌───┐   ┌─ Pi-2 ──┐ │
│   │ MASTER  │   │                       │   │S1 │ │S2 │   │Passive │ │
│   │ PLC     ├─A─┤ ComA           ComB   ├─A─┤   ├─┤   ├─A─┤Sniffer │ │
│   │         ├─B─┤(ttyUSB0)   (ttyUSB1)  ├─B─┤   ├─┤   ├─B─┤(ttyUSB0│ │
│   └─────────┘   └───────────────────────┘   └───┘ └───┘   └────────┘ │
│   [120Ω]                                                    [120Ω]    │
│                                                                        │
│   segment 1          segment 2                                         │
│   (Master→Pi-1)      (Pi-1→Slaves→Pi-2)                               │
│                                                                        │
│   Pi-1:8766 ←──── WiFi/Ethernet ────→ Pi-2:8766                       │
│              (sync reports via API / MQTT)                              │
└──────────────────────────────────────────────────────────────────────────┘
```

## Phụ lục B: Cấu trúc source code

```
tools/modbus/
├── modbus_sniffer.py    ← CRC-16, FrameParser, HealthAnalyzer, ModbusSniffer
│                           Mode 1 (Passive): 8 diagnostics
│
├── modbus_proxy.py      ← Inline Proxy, DirectionalFrame, ProxyAnalyzer
│                           Mode 2 (Inline): 15 diagnostics (8 + 7)
│
├── dashboard.py         ← FastAPI server (port 8766)
│                           /api/sniffer/report → JSON health report
│                           /ws → WebSocket realtime events
│
├── config.yaml          ← Serial + sniffer + proxy config
├── requirements.txt
├── modbus-rtu-tools.service
└── static/
    └── index.html       ← Dark-themed dashboard (3 tabs)
```

