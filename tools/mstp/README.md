# MS/TP Tools — Raspberry Pi 3 + USB-RS485

Công cụ quét sức khỏe mạng BACnet MS/TP và protocol bridge, chạy trên Raspberry Pi 3.

## Phần cứng cần thiết

| Thứ | Chi tiết |
|-----|---------|
| Pi 3 B/B+ | RAM ≥ 512MB, OS: Raspberry Pi OS / Ubuntu 22.04 |
| USB-RS485 adapter | CH340 / CP2102 / FTDI (Pi OS nhận tự động) |
| Resistor 120Ω | Ở 2 đầu RS485 bus (nếu bus chưa có) |
| Kết nối bus | A+/B- đúng cực, kết nối GND chung |

**Baud rate** phải khớp 100% với devices trên bus.  
Phổ biến: `38400` bps. Kiểm tra datasheet device.

---

## Cài đặt nhanh

```bash
# 1. Vào thư mục
cd Bacnet_MQTT/tools/mstp/

# 2. Sửa config
nano config.yaml
#  → port: /dev/ttyUSB0   (hoặc /dev/ttyAMA0)
#  → baudrate: 38400       (phải khớp với bus)

# 3. Cài đặt
sudo bash install.sh

# 4. Xác nhận port
ls /dev/ttyUSB* /dev/ttyAMA*
```

---

## Sử dụng

### Quét một lần (CLI)

```bash
cd tools/mstp/
python3 scanner.py
# Kết quả dạng bảng trên terminal

python3 scanner.py --json | python3 -m json.tool
# Kết quả JSON chi tiết
```

### Monitor liên tục + Dashboard

```bash
cd tools/mstp/
python3 dashboard.py
# Mở trình duyệt: http://<pi-ip>:8765
```

### Monitor một lần

```bash
python3 health_monitor.py --once
```

### Chạy như service (auto-start khi boot)

```bash
sudo systemctl start mstp-tools
sudo systemctl status mstp-tools
sudo journalctl -u mstp-tools -f
```

---

## Dashboard UI

```
┌─────────────────────────────────────────────────────┐
│ 🔌 MS/TP Network Health Dashboard      [Scan Now]   │
├─────────────────────────────────────────────────────┤
│  12 Online  │  3 Offline  │  15 Total  │  8 Scans  │
├──────────────────────────────────┬──────────────────┤
│  Node Grid 0-127                 │  Node Detail     │
│  🟢 = online   🔴 = offline      │  Name: ...       │
│  ⬛ = unknown                    │  Vendor: ...     │
│  [click to select]               │  RTT: 42ms ████  │
├──────────────────────────────────┼──────────────────┤
│                                  │  Event Feed      │
│                                  │  🟢 Node 5 online│
│                                  │  🔴 Node 12 off  │
└──────────────────────────────────┴──────────────────┘
```

---

## API Endpoints

| Method | Path | Mô tả |
|--------|------|-------|
| GET | `/api/nodes` | All nodes + status |
| GET | `/api/nodes/{id}` | Node detail + events |
| GET | `/api/events?limit=100` | Event log |
| GET | `/api/stats` | Bus health summary |
| POST | `/api/scan` | Trigger immediate re-scan |
| GET | `/api/bridge/values` | All cached point values |
| GET | `/api/bridge/{id}/{type}/{inst}` | Single point value |
| WS  | `/ws` | Realtime events |

---

## MQTT (nếu enable)

```
mstp/{node_id}/{object_type}/{instance}/value
→ {"node_id":5, "object_type":"analogValue", "instance":1,
   "value":23.5, "timestamp":..., "source":"mstp"}
```

Bật trong `config.yaml`:
```yaml
mqtt:
  enabled: true
  broker_host: 192.168.1.100
  broker_port: 1883
```

---

## Kiến trúc

```
USB-RS485 ──► /dev/ttyUSB0
                │
                ▼
           BAC0 (MS/TP master, node 127)
           Token ring participant
                │
          ┌─────┴───────┐
          │             │
       scanner      bridge
       WhoIs/IАm   poll objects
       read props   cache values
          │             │
          └─────┬───────┘
          health_monitor
          (track up/down, SQLite)
                │
           dashboard.py
           (FastAPI + WebSocket)
                │
          Browser / MQTT Broker
```

---

## Lưu ý kỹ thuật

1. **Token ring join** mất 1–3 giây sau start — bình thường
2. **Address conflict**: đảm bảo `node_address: 127` không trùng với device nào
3. **Baud rate**: một số controller dùng 9600 hoặc 76800, kiểm tra datasheet
4. **Bus termination**: thiếu resistor 120Ω → tín hiệu nhiễu, node miss
5. **Pi 3 timing**: đủ tốt ở baud ≤38400; dùng `Nice=-10` (đã set trong service)
6. **Không phải BACnet/IP Router chuẩn**: Pi đọc từ MS/TP và expose REST/MQTT.
   Nếu cần forward BACnet/IP packets, dùng thiết bị dedicated (Moxa, B&B Electronics).
