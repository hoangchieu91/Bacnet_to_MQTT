# BACnet MS/TP — Hướng Dẫn Clone Tính Năng Sang Dự Án Khác

> **Ngày tạo:** 2026-03-17  
> **Nguồn dự án:** `Bacnet_MQTT` Gateway V2  
> **Phần cứng yêu cầu:** USB-RS485 adapter + Raspberry Pi (hoặc Linux bất kỳ)

---

## 1. Tổng Quan Kiến Trúc

```
┌──────────────┐    RS-485 Bus    ┌──────────────────────┐
│ FCU / AHU    │──────────────────│  Raspberry Pi        │
│ (BACnet MSTP │    2-wire A/B    │  + USB-RS485 adapter │
│  Slave Node) │                  │  + Python MS/TP code │
└──────────────┘                  └──────────┬───────────┘
                                             │ Ethernet / WiFi
                                  ┌──────────▼───────────┐
                                  │  MQTT Broker / REST   │
                                  │  (Upstream server)    │
                                  └──────────────────────┘
```

### 2 phương pháp giao tiếp MS/TP:

| Phương pháp | File | Mô tả | Khi nào dùng |
|:---|:---|:---|:---|
| **BAC0 Wrapper** | `scanner.py`, `bridge.py` | Dùng thư viện BAC0 (wrapper của BACpypes3) để join token ring | Khi cần tương thích đầy đủ BACnet stack, tự động route qua BBMD |
| **Pure Python** | `mstp_master.py` | Tự xây dựng MS/TP frame từ raw serial, parse CRC, quản lý token ring | Khi cần kiểm soát tối đa, debug protocol level, hoặc chạy trên thiết bị nhúng |

---

## 2. Danh Sách Files Cần Clone

### Core (BẮT BUỘC)

| File | Size | Vai trò |
|:---|:---|:---|
| `mstp_master.py` | 31KB | ⭐ **Pure Python MS/TP stack** — Token ring, CRC, frame parser, WhoIs/IAm/ReadProperty |
| `scanner.py` | 10KB | Scanner dùng BAC0 — WhoIs, đọc properties, đo RTT |
| `bridge.py` | 8KB | Protocol Bridge — đọc value từ MSTP → REST API + MQTT |
| `config.yaml` | 1.3KB | Cấu hình serial port, baud, scan, MQTT, API |
| `requirements.txt` | 239B | Python dependencies |

### Nâng cao (TÙY CHỌN)

| File | Vai trò |
|:---|:---|
| `mstp_sniffer.py` | Passive sniffer — Phân tích raw frame, **KHÔNG tham gia** token ring |
| `mstp_scan_raw.py` | Scanner raw (không dùng BAC0), parse WhoIs response trực tiếp |
| `mstp_mqtt_bridge.py` | Bridge dùng `MstpMaster` thay vì BAC0 |
| `health_monitor.py` | Giám sát sức khỏe bus liên tục, lưu SQLite |
| `bacnet_file_transfer.py` | Upload/download file qua BACnet AtomicWriteFile |
| `dashboard.py` | Web dashboard (FastAPI + WebSocket) cho sức khỏe mạng |
| `install.sh` | Script cài đặt tự động trên Pi |
| `mstp-tools.service` | Systemd service template |

---

## 3. Cấu Hình (config.yaml)

```yaml
serial:
  port: /dev/ttyUSB0        # Cổng USB-RS485 (kiểm tra: ls /dev/ttyUSB*)
  baudrate: 38400            # PHẢI TRÙNG với tất cả thiết bị trên bus!
  node_address: 127          # Địa chỉ node của Pi (0-127, chọn số chưa ai dùng)
  timeout: 10                # Thời gian chờ response (giây)

scan:
  interval_seconds: 60       # Tần suất quét lại (cho health monitor)
  node_range: [0, 127]       # Phạm vi quét
  timeout_per_node: 1.5      # Thời gian chờ mỗi node (giây)

bridge:
  enabled: true
  poll_interval: 30          # Chu kỳ poll value (giây)

mqtt:
  enabled: true
  broker_host: nxchieu.duckdns.org
  broker_port: 54883
  topic_prefix: mstp         # → mstp/{node_id}/{obj_type}/{instance}/value
```

---

## 4. ⚠️ Chú Ý Quan Trọng Khi Sử Dụng BACnet MS/TP

### 4.1 Phần Cứng

| Hạng mục | Chi tiết |
|:---|:---|
| **Adapter** | USB-RS485 (chip **FTDI FT232R** hoặc **CH340**) |
| **Bus cáp** | Twisted-pair, **2 dây** (A+ / B-), có GND chung |
| **Termination** | Điện trở **120Ω** ở **2 đầu** bus (đầu và cuối) |
| **Chiều dài** | Tối đa **1200m** (38400 baud), giảm theo baud rate |
| **Pull-up/down** | A kéo lên +5V (560Ω), B kéo xuống GND (560Ω) — tùy bus |

> [!CAUTION]
> Nếu thiếu **resistor termination 120Ω** ở 2 đầu bus → CRC error liên tục, mất frame, token ring collapse!

### 4.2 Baud Rate

```
⚠️ TẤT CẢ thiết bị trên CÙNG bus PHẢI dùng CÙNG baud rate!
```

| Baud | Khoảng cách max | Ghi chú |
|:---|:---|:---|
| 9600 | ~1200m | Cổ điển, chậm nhất |
| 19200 | ~1200m | Phổ biến trên hệ cũ |
| **38400** | ~1200m | ⭐ **Phổ biến nhất** trên BMS hiện đại |
| 76800 | ~600m | Nhanh nhưng giảm khoảng cách |

**Cách xác định baud rate hiện tại:**
```bash
# Dùng sniffer thử từng baud cho đến khi thấy frame hợp lệ
python3 mstp_sniffer.py --port /dev/ttyUSB0 --baud 38400 --duration 10
python3 mstp_sniffer.py --port /dev/ttyUSB0 --baud 19200 --duration 10
```

### 4.3 MS/TP Address (MAC)

- Phạm vi: **0–127** (total 128 nodes trên 1 bus)
- **Node address PHẢI DUY NHẤT** trên bus — nếu trùng → **token ring crash**
- Convention: **127** thường dùng cho diagnostic tool (Pi)
- Kiểm tra MAC đã dùng: `python3 mstp_scan_raw.py --port /dev/ttyUSB0`

### 4.4 Token Ring — Điều Cần Biết

MS/TP dùng **token-passing** protocol (không phải request-response đơn giản):

```
Node 1 ──Token──► Node 5 ──Token──► Node 12 ──Token──► Node 127 (Pi)
   ▲                                                        │
   └────────────────────────────────────────────────────────┘
```

- **Chỉ node CÓ token** mới được phát dữ liệu
- Pi phải **JOIN vào token ring** trước khi giao tiếp
- Sau join, Pi nhận token → gửi WhoIs/ReadProperty → pass token
- Token circulate time phụ thuộc số node: `~5ms × N_nodes`

> [!IMPORTANT]
> **Chỉ 1 request tại 1 thời điểm** trên bus MS/TP! Không thể gửi song song. Code polling phải tuần tự (serial), không song song (parallel).

### 4.5 Timing & Performance

| Thông số | Giá trị | Ghi chú |
|:---|:---|:---|
| Token pass | ~5ms/hop | N nodes → N×5ms per cycle |
| ReadProperty RTT | 50–300ms | Tùy device phản hồi |
| Max frame data | 501 bytes | ASHRAE 135 §9.3 |
| Max APDU (route qua IP→MSTP) | 128 bytes | Giảm do routing overhead |
| Poll 1 device (5 objects) | ~1–2s | 5 ReadProperty tuần tự |
| Poll 20 devices | ~30–60s | Nên đặt poll_interval ≥ 30s |

> [!WARNING]
> **Không poll quá nhanh!** Nếu poll_interval quá ngắn, queue request sẽ chồng chéo và gây timeout cascade. Khuyến nghị: ≥30s cho 10-20 device.

### 4.6 CRC — Lỗi Thường Gặp

MS/TP dùng 2 loại CRC theo ASHRAE 135 Annex G:
- **CRC-8** (header): Bảo vệ 5 byte header (frame type, dst, src, length)
- **CRC-16** (data): Bảo vệ payload data

```python
# CRC-8 — 1 byte, check header integrity
def calc_crc8(data: bytes) -> int:
    crc = 0xFF
    for b in data:
        crc = CRC8_TABLE[crc ^ b]
    return (~crc) & 0xFF

# CRC-16 — 2 bytes (little-endian), check data integrity
def calc_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc = (crc >> 8) ^ CRC16_TABLE[(crc ^ b) & 0xFF]
    return (~crc) & 0xFFFF
```

> [!CAUTION]
> Bảng CRC phải đúng **chính xác** theo ASHRAE 135 Annex G. Nếu sai 1 giá trị → **tất cả frame bị reject**. Luôn copy bảng từ code đã kiểm chứng (`mstp_master.py` lines 30-98).

### 4.7 Frame Structure

```
Preamble: 0x55 0xFF
Header:   [FrameType] [Destination] [Source] [LengthHi] [LengthLo] [HeaderCRC]
Data:     [... payload ...] [DataCRC_Lo] [DataCRC_Hi]
```

**Frame Types:**

| Hex | Tên | Mô tả |
|:---|:---|:---|
| 0x00 | TOKEN | Chuyển quyền phát cho node khác |
| 0x01 | POLL_FOR_MASTER | Tìm node mới trên bus |
| 0x02 | REPLY_TO_POLL | Trả lời PFM → "tôi ở đây!" |
| 0x05 | BACNET_DATA_XR | Dữ liệu BACnet, mong chờ reply |
| 0x06 | BACNET_DATA_NXR | Dữ liệu BACnet, không cần reply |

### 4.8 Địa Chỉ MS/TP vs BACnet/IP

| Loại | Format | Ví dụ | Giải thích |
|:---|:---|:---|:---|
| BACnet/IP | `192.168.20.28` | IP trực tiếp | Thiết bị có Ethernet |
| MS/TP trực tiếp | `5` | MAC address | Pi cắm trực tiếp vào bus |
| MS/TP qua router | `8700:5` | `network:node` | BACnet/IP router forward tới MS/TP |

Code detect MS/TP trong gateway chính:
```python
is_mstp = ":" in address and not address.startswith("[") and "." not in address
# "8700:5" → True (MS/TP qua router)
# "192.168.20.28" → False (IP)
```

### 4.9 Serial Port Permission (Linux)

```bash
# Thêm user vào group dialout
sudo usermod -aG dialout $USER

# Kiểm tra
ls -la /dev/ttyUSB0
# → crw-rw---- 1 root dialout ...

# Nếu vẫn bị permission denied → reboot hoặc logout/login
```

### 4.10 Đa Bus (Multiple RS-485)

Nếu cần kết nối **nhiều bus MS/TP** cùng lúc:
- Mỗi bus cần **1 USB-RS485 adapter riêng** (`/dev/ttyUSB0`, `/dev/ttyUSB1`...)
- Mỗi adapter chạy **1 instance MstpMaster riêng** với MAC address riêng
- Baud rate có thể khác nhau giữa các bus

---

## 5. Quick Start — Clone Vào Dự Án Mới

### Bước 1: Copy files

```bash
# Tạo thư mục trong dự án mới
mkdir -p your_project/mstp/

# Copy core files
cp mstp_master.py scanner.py bridge.py config.yaml requirements.txt your_project/mstp/

# (Tùy chọn) Copy tools nâng cao
cp mstp_sniffer.py mstp_scan_raw.py health_monitor.py your_project/mstp/
```

### Bước 2: Cài dependencies

```bash
cd your_project/mstp/
pip install -r requirements.txt
```

### Bước 3: Sửa config

```bash
nano config.yaml
# Sửa: port, baudrate, node_address, mqtt broker
```

### Bước 4: Test kết nối

```bash
# Test 1: Quét bus (dùng BAC0)
python3 scanner.py --config config.yaml

# Test 2: Quét raw (không cần BAC0)
python3 mstp_scan_raw.py --port /dev/ttyUSB0 --baud 38400

# Test 3: Sniffer passive (chỉ nghe, không join bus)
python3 mstp_sniffer.py --port /dev/ttyUSB0 --baud 38400 --duration 30
```

### Bước 5: Tích hợp vào code

```python
# === Cách 1: Dùng BAC0 wrapper (đơn giản) ===
from scanner import MstpScanner

async def scan_mstp():
    scanner = MstpScanner(
        port="/dev/ttyUSB0",
        baudrate=38400,
        node_address=127,
    )
    async with scanner:
        results = await scanner.scan()
        for addr, node in results.items():
            print(f"Node {addr}: {node.name} — {node.vendor} {node.model}")
            print(f"  RTT: {node.rtt_ms}ms, Objects: {node.object_count}")


# === Cách 2: Dùng Pure Python stack (kiểm soát cao) ===
from mstp_master import MstpMaster

def scan_raw():
    master = MstpMaster("/dev/ttyUSB0", baudrate=38400, mac=127)
    master.queue_whois()  # Queue WhoIs broadcast

    devices = {}
    def on_event(event, data):
        if event == 'iam':
            devices[data['device_instance']] = data
            print(f"Found: Device {data['device_instance']} at MAC {data['mac']}")
        elif event == 'joined':
            print(f"Joined token ring as MAC {data['mac']}")
            master.queue_whois()

    master.run(duration=15, callback=on_event)
    return devices


# === Cách 3: Bridge liên tục (poll + MQTT) ===
from bridge import MstpBridge
from scanner import MstpScanner

async def run_bridge():
    scanner = MstpScanner.from_config("config.yaml")
    await scanner.start()

    bridge = MstpBridge.from_config("config.yaml", bacnet=scanner._bacnet)
    await bridge.start()
    await bridge.run_poll_loop()  # Blocks — polls forever
```

---

## 6. Xử Lý Sự Cố

| Triệu chứng | Nguyên nhân | Giải pháp |
|:---|:---|:---|
| `Permission denied: /dev/ttyUSB0` | User không có quyền serial | `sudo usermod -aG dialout $USER` + relogin |
| Token ring join timeout | Baud rate sai | Thử 9600 / 19200 / 38400 / 76800 |
| CRC errors liên tục | Thiếu termination resistor | Hàn 120Ω ở 2 đầu bus |
| Chỉ thấy TOKEN + PFM frame | WhoIs không được gửi | Kiểm tra `queue_whois()` đã gọi sau join |
| IAm nhận nhưng ReadProperty timeout | APDU quá lớn | Giảm chunk size, dùng ReadPropertyMultiple |
| Scan chỉ thấy 1 node | Pi là sole master | Check cáp A/B có bị đảo không |
| `BAC0 start failed` | Port 47808 đã bị chiếm | Dùng port khác: `ip="0.0.0.0/24:47809"` |

---

## 7. Tham Khảo

- **ASHRAE 135-2016** — §9.3 (MS/TP Data Link Layer)
- **ASHRAE 135 Annex G** — CRC Lookup Tables
- **BAC0 docs**: https://bac0.readthedocs.io
- **BACpypes3**: https://github.com/JoelBender/BACpypes3

---

*File nén code mẫu: `mstp_clone_package.tar.gz` (cùng thư mục docs/)*
