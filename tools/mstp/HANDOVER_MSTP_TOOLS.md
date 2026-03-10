# MS/TP Tools — Tài liệu Bàn giao

**Phiên bản:** 1.0  
**Ngày:** 2026-03-10  
**Nhánh Git:** `feature/mstp-tools`  
**Không ảnh hưởng gateway đang chạy trên nhánh `main`.**

---

## 1. Tổng quan

Bộ công cụ giám sát và vận hành mạng BACnet MS/TP trên Raspberry Pi 3 + USB-RS485:

| Công cụ | File | Mục đích |
|---------|------|---------|
| **Scanner** | `scanner.py` | Quét node, đọc properties, đo RTT — join token ring |
| **Health Monitor** | `health_monitor.py` | Giám sát liên tục, lưu sự kiện SQLite |
| **Passive Sniffer** | `mstp_sniffer.py` | Phân tích raw frame, chẩn đoán bệnh bus |
| **Protocol Bridge** | `bridge.py` | Đọc MS/TP → expose REST + MQTT |
| **File Transfer** | `bacnet_file_transfer.py` | Upload/download file lên device (AtomicWriteFile) |
| **Dashboard** | `dashboard.py` | Web UI + API server |

---

## 2. Phần cứng yêu cầu

```
Raspberry Pi 3 B/B+
  │
  └── USB → [USB-RS485 adapter] → A+/B− → RS485 bus
                                              │
                              [R=120Ω] ─── ══╪══ ─── [R=120Ω]
                                         Device 1 ... Device N
```

| Hạng mục | Yêu cầu |
|----------|---------|
| Pi OS | Raspberry Pi OS Lite / Ubuntu 22.04 |
| USB Adapter | CH340G / CP2102 / FTDI FT232 (nhận tự động) |
| Termination | Resistor 120Ω ở **2 đầu** bus (bắt buộc nếu chưa có) |
| Baud rate | Phải **khớp 100%** với thiết bị hiện có trên bus |
| GND chung | Nối GND Pi với GND bus (tránh common-mode noise) |

---

## 3. Cài đặt nhanh

```bash
# Clone/checkout branch
git clone https://github.com/hoangchieu91/Bacnet_to_MQTT.git
git checkout feature/mstp-tools
cd tools/mstp/

# Cài Python deps
pip3 install -r requirements.txt

# Sửa config
nano config.yaml
#   port: /dev/ttyUSB0      ← kiểm tra với: ls /dev/ttyUSB*
#   baudrate: 38400          ← phải khớp với bus
#   node_address: 127        ← để mặc định nếu không có conflict

# Cài service + set permission
sudo bash install.sh

# Khởi động dashboard
python3 dashboard.py
# → http://<ip-pi>:8765
```

---

## 4. Dashboard Web UI

Truy cập `http://<pi-ip>:8765` sau khi chạy `python3 dashboard.py`.

### 4 Tabs chính

#### 🔬 Monitor
- **Node Grid 128 ô** (0–127): 🟢 online / 🔴 offline / ⬛ unknown
- **Stats**: tổng online/offline, số lần scan
- **Event Feed**: realtime qua WebSocket
- **Node Detail**: click ô → name, vendor, model, RTT

#### 📡 Bus Health (Sniffer)
Passive sniffer không tham gia token ring — **không gây nhiễu bus**.

- **Bus Utilization ring**: % bandwidth đang dùng
- **Pathology list**: chẩn đoán tự động (xem §5)
- **Per-node stats table**: fps, kbps, bad CRC, token analysis
- **Frame log**: 20 frame gần nhất (type, src, dst, valid/invalid)

#### 📤 File Transfer
Upload application/config file lên BACnet device:

```
Device Address: 192.168.20.50    (BACnet/IP)
             hoặc 8700:5          (MS/TP qua router — network:node)
File Object #: 1                  (mặc định)
File: [chọn file .app]
☑ Send ReinitializeDevice         (reload sau khi upload xong)
```

#### ⚙️ Network Config
- Chọn serial port từ danh sách tự phát hiện
- Đổi baudrate, node address, scan interval
- Lưu → file `config.yaml` được cập nhật

---

## 5. Chẩn đoán sức khỏe bus (Bus Health)

Sniffer tự động phát hiện 8 loại bệnh:

| Code | Severity | Mô tả | Nguyên nhân thường gặp |
|------|----------|-------|------------------------|
| `CHATTY_NODE` | ⚠️/🔴 | Node gửi > 20 frames/s | Poll quá nhanh, firmware lỗi |
| `DUPLICATE_DEVICE_ID` | 🔴 | 2 node cùng khai ID | Cấu hình deviceInstance bị trùng |
| `HIGH_CRC_ERRORS` | ⚠️/🔴 | > 10% frame lỗi CRC | Thiếu terminator, dây kém, baud sai |
| `JUNK_BYTES` | ⚠️/🔴 | > 2% bytes rác | Baud rate không khớp |
| `TOKEN_IMBALANCE` | ⚠️ | 1 node giữ token quá lâu | Device tính toán nặng trước khi pass |
| `HIGH_BUS_UTILIZATION` | ⚠️/🔴 | > 70%/90% bandwidth | Quá nhiều thiết bị hoặc poll quá nhanh |
| `PFM_STORM` | ⚠️ | Poll-For-Master > 2/s | Tranh chấp địa chỉ MAC |
| `BUS_SILENCE` | ⚠️/🔴 | Im lặng > 500ms | Token bị mất, bus bị treo |

### Hướng xử lý

| Triệu chứng | Kiểm tra | Giải pháp |
|-------------|---------|----------|
| CRC cao + junk nhiều | Baud rate | Kiểm tra config mỗi thiết bị, đồng bộ lại |
| CRC cao, junk thấp | Điện | Thêm/kiểm tra resistor 120Ω; nối GND chung |
| Token imbalance | Device cụ thể | Giảm logic nội bộ hoặc tăng timeout |
| Duplicate ID | Cấu hình | Đổi deviceInstance trong ECY Configurator |
| Bus silence | Token ring | Tắt/bật lại device bị nghi ngờ giữ token |

---

## 6. File Transfer — CPO-RL4 & CPO-PC6A

### Scope hỗ trợ

| Loại file | Hỗ trợ | Cách làm |
|-----------|--------|---------|
| Application file (.app) | ✅ Đầy đủ | Upload via File Object + AtomicWriteFile |
| Config backup | ✅ Đầy đủ | Download via AtomicReadFile |
| Firmware core (low-level OTA) | ⚠️ Hạn chế | Cần **ECY Configurator** hoặc **Niagara N4** |

### Quy trình upload application

```bash
# CLI
python3 bacnet_file_transfer.py upload \
  --address 192.168.20.50 \
  --file-instance 1 \
  --path /path/to/application.app \
  --reload --device-instance 10121

# Hoặc qua Dashboard → Tab "File Transfer"
```

**Bước thực hiện:**
1. Kết nối BACnet/IP hoặc MS/TP (qua router)
2. Chọn file do Distech ECY Builder gen ra
3. Upload (chunked: 128B/chunk qua MS/TP, 512B qua IP)
4. Tick "ReinitializeDevice" → device reload application
5. Xác nhận: scan lại, kiểm tra `systemStatus` = `operational`

### Địa chỉ MS/TP

- **BACnet/IP trực tiếp**: `192.168.20.50`
- **MS/TP qua BBMD/router**: `8700:5` (network 8700, node 5)
- **Router trong cùng Pi**: Pi phải join token ring (dùng `scanner.py` trước)

---

## 7. CLI Commands

```bash
# Quét 1 lần, in bảng
python3 scanner.py

# Quét 1 lần, JSON output
python3 scanner.py --json

# Sniffer 60s, in báo cáo
python3 mstp_sniffer.py --port /dev/ttyUSB0 --baud 38400 --duration 60

# Xem từng frame realtime
python3 mstp_sniffer.py --frames --duration 120

# Sniffer JSON output
python3 mstp_sniffer.py --json --duration 60 > report.json

# Upload file
python3 bacnet_file_transfer.py upload --address 192.168.20.50 \
  --file-instance 1 --path app.file --reload --device-instance 10121

# Download file
python3 bacnet_file_transfer.py download --address 192.168.20.50 \
  --file-instance 1 --output backup.app
```

---

## 8. Service Management (systemd)

```bash
# Start
sudo systemctl start mstp-tools

# Auto-start khi boot (đã enable qua install.sh)
sudo systemctl enable mstp-tools

# Xem log
sudo journalctl -u mstp-tools -f

# Restart (sau khi đổi config)
sudo systemctl restart mstp-tools

# Stop
sudo systemctl stop mstp-tools
```

---

## 9. API Reference

### Base URL: `http://<pi-ip>:8765`

| Method | Endpoint | Mô tả |
|--------|---------|-------|
| GET | `/api/nodes` | Danh sách nodes |
| GET | `/api/nodes/{id}` | Node detail + event history |
| GET | `/api/events?limit=100` | Event log |
| GET | `/api/stats` | Bus summary stats |
| POST | `/api/scan` | Trigger scan ngay |
| GET | `/api/sniffer/report` | Full sniffer report + pathologies |
| GET | `/api/sniffer/frames?limit=100` | Frame log |
| GET | `/api/sniffer/pathologies` | Danh sách bệnh hiện tại |
| POST | `/api/file/upload` | Upload file (multipart/form-data) |
| GET | `/api/file/download` | Download file từ device |
| GET | `/api/serial-ports` | Danh sách serial ports trên hệ thống |
| PUT | `/api/config` | Cập nhật network config |
| GET | `/api/bridge/values` | Tất cả cached point values |
| WS | `/ws` | Realtime events (nodes, pathologies, xfer progress) |

---

## 10. Troubleshooting

| Vấn đề | Nguyên nhân | Giải pháp |
|--------|------------|----------|
| Không thấy `/dev/ttyUSB0` | Driver chưa load | `sudo dmesg \| grep USB`; thử cổng USB khác |
| Permission denied | User không trong group | `sudo usermod -aG dialout $USER`, logout/login |
| Scan không thấy device | Baud sai / terminator thiếu | Đo điện áp chênh A-B (idle = +2V), kiểm tra baud |
| Token ring join timeout | Bus bận hoặc 1 node monopoly | Chạy sniffer xem node nào chiếm token |
| Upload thất bại | Device không có File Object | Kiểm tra firmware device, tham khảo Distech docs |
| Dashboard 503 | BAC0 chưa khởi động xong | Đợi 5–10s sau khi start, xem log `journalctl` |

---

## 11. Lưu ý quan trọng

> **Bus termination**: RS485 **bắt buộc** có resistor 120Ω ở 2 đầu vật lý của đường dây.  
> Thiếu terminator → tín hiệu phản xạ → CRC errors → mất node.

> **Baud rate**: Tất cả thiết bị trên bus **phải dùng cùng baud rate**.  
> Nếu có thiết bị dùng baud khác, cả segment đó sẽ không thể giao tiếp.

> **Branch isolation**: Tất cả code trong nhánh `feature/mstp-tools`.  
> Nhánh `main` (gateway production) không bị ảnh hưởng.  
> Để merge vào main: `git checkout main && git merge feature/mstp-tools`

---

*Để được hỗ trợ kỹ thuật, xem thêm `README.md` trong cùng thư mục.*
