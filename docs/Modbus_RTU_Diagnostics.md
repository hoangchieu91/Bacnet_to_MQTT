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
7. [So sánh Passive vs Inline](#7-so-sánh)

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

## 7. So sánh Passive vs Inline

```
                    ┌──────────────────┬──────────────────────┐
                    │  Passive (1 COM) │  Inline Proxy (2 COM)│
┌───────────────────┼──────────────────┼──────────────────────┤
│ Ảnh hưởng bus     │ Không            │ Có (nếu Pi crash →  │
│                   │                  │ bus mất kết nối)     │
├───────────────────┼──────────────────┼──────────────────────┤
│ Phần cứng         │ 1× USB-RS485     │ 2× USB-RS485         │
├───────────────────┼──────────────────┼──────────────────────┤
│ Biết chiều frame  │ ❌ (đoán)        │ ✅ (chắc chắn)       │
├───────────────────┼──────────────────┼──────────────────────┤
│ Response time     │ ⚠️ Heuristic     │ ✅ Chính xác (µs)    │
├───────────────────┼──────────────────┼──────────────────────┤
│ No response       │ ⚠️ False positive│ ✅ 100% confirmed    │
├───────────────────┼──────────────────┼──────────────────────┤
│ Diagnose per-     │ ❌               │ ✅ (CRC fail 1 chiều)│
│ segment wiring    │                  │                      │
├───────────────────┼──────────────────┼──────────────────────┤
│ Safe for prod     │ ✅ (không ảnh    │ ⚠️ (Pi là SPOF)      │
│                   │  hưởng bus)      │                      │
├───────────────────┼──────────────────┼──────────────────────┤
│ Usecase           │ Monitor liên tục │ Debug deep problem   │
│                   │ 24/7             │ phải tạm dừng bus    │
└───────────────────┴──────────────────┴──────────────────────┘
```

### Khi nào dùng mode nào?

**Passive Sniffer** — dùng khi:
- Monitor bus 24/7 không muốn ảnh hưởng
- Kiểm tra sức khỏe tổng quan (CRC, utilization, exception rate)
- Tìm slave nào gây nhiều lỗi nhất

**Inline Proxy** — dùng khi:
- Cần đo response time chính xác
- Nghi ngờ slave không trả lời nhưng passive không confirm được
- Debug firmware issue (direction violation, broadcast response)
- Tìm 2 slave cùng address
- Tìm đoạn dây nào bị lỗi (so sánh CRC 2 chiều)

---

## Phụ lục A: Sơ đồ đấu nối phần cứng Inline Proxy

```
┌──────────────────────────────────────────────────────────────────┐
│                    Raspberry Pi 5                                 │
│                                                                  │
│   USB Port 1          USB Port 2                                 │
│   ┌─────────┐        ┌─────────┐                                │
│   │USB-RS485│        │USB-RS485│                                 │
│   │ CH340   │        │ CH340   │                                 │
│   │ ComA    │        │ ComB    │                                 │
│   │/ttyUSB0 │        │/ttyUSB1 │                                 │
│   └────┬────┘        └────┬────┘                                 │
│        │                  │                                      │
└────────┼──────────────────┼──────────────────────────────────────┘
         │                  │
    ┌────┴────┐        ┌────┴────┐
    │ A(+)    │        │ A(+)    │
    │ B(-)    │        │ B(-)    │
    │ GND     │        │ GND     │
    └────┬────┘        └────┬────┘
         │                  │
    ═════╪══════════   ═════╪══════════════════
    Segment 1 (Master)  Segment 2 (Slaves)
    ═════╪══════════   ═════╪════╪════╪═══════
         │                  │    │    │
    ┌────┴────┐        ┌────┴┐ ┌─┴─┐ ┌┴────┐
    │ MASTER  │        │ S1  │ │S2 │ │ S3  │
    │ PLC/    │        │     │ │   │ │     │
    │ SCADA   │        │     │ │   │ │     │
    └─────────┘        └─────┘ └───┘ └─────┘

    ⚠️ Mỗi segment cần termination 120Ω ở 2 đầu
    ⚠️ GND phải chung giữa tất cả thiết bị
```

## Phụ lục B: Cấu trúc source code

```
tools/modbus/
├── modbus_sniffer.py    ← [521 lines] CRC-16, FrameParser, HealthAnalyzer, ModbusSniffer
│                           • _crc16()          — CRC-16 Modbus lookup table
│                           • ModbusFrameParser  — gap-based state machine
│                           • ModbusHealthAnalyzer — 8 diagnostic rules
│                           • ModbusSniffer      — async serial reader + diagnostics
│
├── modbus_proxy.py      ← [450 lines] Inline Proxy, DirectionalFrame, ProxyAnalyzer
│                           • ProxyHealthAnalyzer — extends base with 7 more rules
│                           • ModbusProxy         — dual COM forward + analyze
│                           • DirectionalFrame    — frame with direction context
│
├── dashboard.py         ← [160 lines] FastAPI server
│                           • /api/sniffer/report → JSON health report
│                           • /ws → WebSocket realtime events
│
├── config.yaml
├── requirements.txt
├── modbus-rtu-tools.service
└── static/
    └── index.html       ← [310 lines] Dark-themed dashboard
```
