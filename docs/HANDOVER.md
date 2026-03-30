# Tài Liệu Bàn Giao — BACnet-MQTT Gateway V2

**Phiên bản:** 2.2
**Ngày cập nhật:** 2026-03-16
**Người cập nhật:** nxchieu

---

## Tóm Tắt Hệ Thống

Hệ thống gồm **1 Ubuntu Server** làm gateway chính và **2 Raspberry Pi** làm bridge MS/TP RS485. Tất cả kết nối từ xa qua **Tailscale VPN**.

```
Internet
    │
    ├── Tailscale VPN (100.x.x.x)
    │       ├── Ubuntu Server  100.74.25.27  (bacnet-monitor) ← Web UI + BACnet/IP
    │       ├── Pi 5           100.x.x.x    (cập nhật)        ← MS/TP RS485
    │       └── Pi 3           100.x.x.x    (cập nhật)        ← MS/TP RS485
    │
    └── OpenVPN (10.212.154.x)
            └── Ubuntu Server  10.212.154.3
```

---

## 1. Ubuntu Server — bacnet-monitor

### Thông Tin Máy

| Thông số | Giá trị |
| :--- | :--- |
| Hostname | `bacnet-monitor` |
| OS | Ubuntu 24.04.4 LTS |
| CPU | Intel Core i7-6700 @ 3.40GHz |
| RAM | 1.9 GB |
| Disk | 29 GB (24% đã dùng — còn ~22 GB) |
| Python | 3.12.3 |
| Docker | ❌ Đã gỡ (2026-03-16) |

### Truy Cập SSH

```bash
# Qua Tailscale (khuyến nghị từ xa)
ssh user@100.74.25.27

# Qua OpenVPN
ssh user@10.212.154.3

# Qua LAN nội bộ
ssh user@172.20.24.175

# Password
Admin@12345
```

### Địa Chỉ Mạng

| Interface | IP | Mục đích |
| :--- | :--- | :--- |
| `ens33` | `172.20.24.175` | LAN văn phòng |
| `ens38` | `192.168.20.113` | Mạng BACnet/BMS |
| `tun0` | `10.212.154.3` | OpenVPN |
| `tailscale0` | `100.74.25.27` | Tailscale |

### Web UI Truy Cập

> **URL:** `http://100.74.25.27:8000`

⚠️ Port là **8000** (uvicorn trực tiếp, không qua NGINX reverse proxy).

### Dịch Vụ Systemd

| Dịch vụ | Port | Lệnh quản lý |
| :--- | :--- | :--- |
| `bacnet-gateway` | 8000 | `sudo systemctl restart bacnet-gateway` |
| `mosquitto` | 1883 | `sudo systemctl restart mosquitto` |
| `nginx` | 80 | `sudo systemctl restart nginx` |

```bash
# Xem log realtime
journalctl -u bacnet-gateway -f

# Xem 50 dòng log gần nhất
journalctl -u bacnet-gateway -n 50

# Kiểm tra tất cả dịch vụ
systemctl status bacnet-gateway mosquitto nginx
```

### Cấu Trúc Thư Mục

```
/home/user/bacnet_mqtt_gateway/
├── backend/                  ← FastAPI source code
│   ├── main.py               ← Entry point, tất cả API endpoints
│   ├── bacnet_service.py     ← BAC0 wrapper
│   ├── gateway_engine.py     ← Polling loop
│   ├── history_store.py      ← SQLite history
│   ├── mqtt_service.py       ← MQTT paho wrapper
│   └── config_manager.py
├── frontend_v2/
│   └── dist/                 ← Build output web (được serve qua uvicorn)
├── config/
│   └── runtime_config.json   ← Cấu hình BACnet IP, MQTT URL
├── data/
│   └── history.db            ← SQLite: lịch sử điểm đo + event log
├── venv/                     ← Python virtual environment
└── /etc/systemd/system/bacnet-gateway.service
```

### Deploy Cập Nhật Code

```bash
# SSH vào máy
ssh user@100.74.25.27

# Vào thư mục dự án
cd ~/bacnet_mqtt_gateway

# Pull code mới
git pull

# Nếu có thay đổi Python (backend)
sudo systemctl restart bacnet-gateway

# Nếu chỉ update frontend (không cần restart backend)
cd frontend_v2 && npm run build
# dist/ được serve tự động — không gây BACnet disconnect
```

---

## 2. Raspberry Pi 5

### Thông Tin Máy

| Thông số | Giá trị |
| :--- | :--- |
| Hostname | `Raspberry-Pi5` |
| OS | Debian GNU/Linux 13 (Trixie) |
| CPU | ARM Cortex-A76 quad-core @ 2.4GHz |
| RAM | 4 GB |
| Vai trò | **Sub-master Gateway**, Modbus RTU Master, MS/TP Bridge |
| Tailscale IP | (Chưa cấu hình) |
| LAN IP | `10.25.7.21` |
| SSH Username | `pi` |
| SSH Password | `Raspberry` |

### Khả năng & Vai Trò (Capabilities)
- **Hiệu năng cao**: Vi xử lý mạnh và RAM 4GB cho phép Pi 5 xử lý lượng dữ liệu lớn, đóng vai trò như một Sub-master Gateway hoặc thậm chí thay thế Ubuntu Server cho các site nhỏ/vừa.
- **Modbus RTU Master**: Đang gánh vác việc đọc dữ liệu Modbus RTU qua cổng Serial ổn định (`modbus-rtu-tools.service`).
- **Quản lý đa mạng MS/TP**: Khả năng đọc đồng thời nhiều chuỗi RS485 (qua USB và GPIO) độ trễ thấp, phục vụ số lượng thiết bị > 50 nút.
- **Đa nhiệm**: Dư sức chạy thêm Mosquitto broker (MQTT) hoặc lưu trữ DB (với thẻ nhớ tốc độ cao/SSD) mà không sợ OOM.

### Kết Nối RS485

| Thông số | Giá trị |
| :--- | :--- |
| Serial port | `/dev/ttyAMA0` (UART onboard) hoặc `/dev/ttyUSB0` |
| Baudrate | 9600 / 38400 (theo thiết bị) |
| Protocol | BACnet MS/TP |

### Dịch Vụ

```bash
# Xem log
journalctl -u mstp-bridge -f

# Restart service
sudo systemctl restart mstp-bridge
sudo systemctl status mstp-bridge
```

### Lưu Ý Pi 5

- Cần **enable UART** trong `/boot/config.txt`: `enable_uart=1`
- Nếu dùng RS485 hat, kiểm tra driver: `ls /dev/ttyAMA*`
- GPIO header 40-pin tương thích ngược Pi 3/4

---

## 3. Raspberry Pi 3

### Thông Tin Máy

| Thông số | Giá trị |
| :--- | :--- |
| Hostname | `BMS-BACKBACKDOOR` |
| OS | Debian GNU/Linux 12 (Bookworm) |
| CPU | ARM Cortex-A53 quad-core @ 1.2GHz |
| RAM | 1 GB |
| Vai trò | **Slave/Bridge RS485** từ xa (dự phòng) |
| Tailscale IP | (Chưa cấu hình) |
| LAN IP | `10.25.7.22` |
| SSH Username | `admin` |
| SSH Password | `Admin@12345` |

### Kết Nối RS485

| Thông số | Giá trị |
| :--- | :--- |
| Serial port | `/dev/ttyAMA0` |
| Baudrate | 9600 / 38400 |
| Protocol | BACnet MS/TP |

### Dịch Vụ

```bash
journalctl -u mstp-bridge -f
sudo systemctl restart mstp-bridge
```

### Khả năng & Giới hạn Pi 3

- **Khả năng (Phù hợp làm gì)**: Rất tốt cho vai trò **Slave/Bridge RS485 từ xa** (chuyển đổi BACnet MS/TP riêng lẻ hoặc Modbus RTU nhỏ gọn rồi đẩy nhanh lên MQTT Ubuntu/Pi 5).
- **Giới hạn phần cứng**: Do RAM chỉ 1GB và CPU ARM Cortex-A53 thế hệ cũ, máy sẽ bị quá tải (OOM) nếu load database nội dung lớn, xử lý giao diện React UI liên tục cho nhiều user, hoặc map > 20 thiết bị MS/TP.
- **Đề xuất**: Chỉ nên dùng để chạy `mstp-bridge` hoặc daemon Python nhẹ (`bacnet-gateway` trỏ MQTT ra xa). Hạn chế lưu DB trực tiếp trên Pi 3 để bảo vệ thẻ nhớ và RAM. Không có USB 3.0, tốc độ đọc thẻ nhớ chậm hơn Pi 5.
- Nếu lỡ đầy RAM (OOM): `dmesg | grep -i "killed process"`

---

## 4. Kiến Trúc Phần Mềm

```
┌──────────────────────────────────────────────────┐
│              Ubuntu Server (bacnet-monitor)       │
│                                                   │
│  Browser → :8000 → FastAPI (uvicorn)              │
│                        │                          │
│              ┌─────────┴─────────┐                │
│              │   BAC0 / BACpypes3│                │
│              │   Port 47808 UDP  │                │
│              └─────────┬─────────┘                │
│                        │                          │
│              ┌─────────┴─────────┐                │
│              │  SQLite history.db│                │
│              └───────────────────┘                │
│                        │                          │
│              Mosquitto MQTT :1883                  │
└──────────────────────────────────────────────────┘
        │ BACnet/IP UDP              │ BACnet/IP
        ▼ (192.168.20.x)            ▼ (qua LAN)
  BACnet/IP Devices           Raspberry Pi 5 / Pi 3
  (Chillers, Meters...)       → RS485 → MS/TP Devices
                                (Thermostats, FCUs...)
```

---

## 5. Cấu Hình BACnet & MQTT (runtime_config.json)

```json
{
  "bacnet": {
    "ip": "192.168.20.113",
    "mask": "24",
    "port": 47808,
    "device_id": 3056000,
    "route_aware": true,
    "auto_discover": true
  },
  "mqtt": {
    "url": "mqtt://nxchieu.duckdns.org:54883",
    "username": "",
    "password": "",
    "topic_prefix": "bms/ubuntu_gw",
    "qos": 0
  }
}
```
*Lưu ý:* Mỗi Node sẽ có một `topic_prefix` riêng biệt (ví dụ: `bms/pi5_gw`, `bms/pi3_gw`, `bms/local_dev`) để không xung đột dữ liệu trên cùng 1 broker.

---

## 6. API Endpoints Quan Trọng

Base URL: `http://100.74.25.27:8000`

| Method | Path | Mô tả |
| :--- | :--- | :--- |
| GET | `/api/status` | Trạng thái gateway + MQTT |
| GET | `/api/health` | CPU/RAM/Disk/Temp |
| POST | `/api/bacnet/discover` | Quét thiết bị BACnet |
| GET | `/api/bacnet/devices` | Danh sách thiết bị đã tìm thấy |
| GET | `/api/mappings` | Danh sách point mappings |
| POST | `/api/mappings/import` | Import CSV/JSON |
| GET | `/api/mappings/export` | Export CSV |
| GET | `/api/events` | Event log |
| WS | `/ws` | WebSocket realtime |

---

## 7. MQTT Topics (Kiến Trúc Mới)

Theo quy hoạch, hệ thống đẩy dữ liệu lên qua một Broker chung tại `nxchieu.duckdns.org:54883`.

### Các Topic Prefix
- `bms/ubuntu_gw`: Dữ liệu từ Ubuntu Server.
- `bms/pi5_gw`: Dữ liệu từ Pi 5 (MS/TP + Modbus).
- `bms/pi3_gw`: Dữ liệu từ Pi 3 (Dự phòng).
- `bms/local_dev`: Máy trạm phát triển nội bộ.

### Cấu trúc Bản tin
```text
# Gateway Status (Khởi động hệ thống & LWT)
{prefix}/status
→ Ví dụ: bms/pi5_gw/status
→ Payload (LWT): {"online": false}

# Device Status (Trạng thái thiết bị MS/TP, BACnet)
{prefix}/device/{device_id}/status
→ Ví dụ: bms/pi5_gw/device/703/status
→ Payload: {"online": true, "address": "192.168.1.5"}

# Telemetry (Dữ liệu từ thiết bị)
{prefix}/device/{device_id}/{object_type}/{object_instance}/value
→ Ví dụ: bms/pi5_gw/device/703/analogValue/1/value
→ Payload: {"value": 23.5, "alarm_state": "normal", "timestamp": "2026-03-16T...", "source": "poll"}

# Command (Ra lệnh điều khiển từ xa)
{prefix}/cmd/device/{device_id}/{object_type}/{object_instance}/write
→ Ví dụ: bms/pi5_gw/cmd/device/703/analogValue/1/write
→ Payload: {"value": 50.0, "priority": 14}
```
*Mẹo: Để xem dữ liệu toàn hệ thống trên máy tính của bạn, chỉ cần Subscribe vào wildcard `bms/#`.*

> 📖 **Xem thêm:** Hướng dẫn chi tiết cách giám sát, subscribe pattern, script Python monitor, và cách gửi lệnh từ xa: **[MQTT_Monitoring_Guide.md](MQTT_Monitoring_Guide.md)**

---

## 8. OpenVPN Server

| Thông số | Giá trị |
| :--- | :--- |
| Server | `nxchieu.duckdns.org:54194` |
| IP nội bộ | `10.25.7.155` |

```bash
# Tạo client mới
ssh user@10.25.7.155
sudo /etc/openvpn/gen-client.sh TenClient
# File: /etc/openvpn/client/TenClient.ovpn
```

---

## 9. Lưu Ý Vận Hành

1. **Port Web UI là 8000**, không phải 8080 — đây là nguồn lỗi phổ biến nhất
2. **Docker đã gỡ** khỏi Ubuntu server (2026-03-16) — dự án chạy trực tiếp qua systemd
3. **Không chạy 2 instance backend** cùng lúc — xung đột UDP port 47808
4. **Tailscale/VPN** có thể can thiệp routing BACnet → nếu bị timeout: `sudo ip route add 192.168.20.0/24 dev ens38 table 52`
5. **Priority 14** cho write BACnet — không override điều khiển tự động (priority 1–7)
6. **COV limit** ~20–50 subscriptions/device — không COV toàn bộ points

---

## 10. Sự Cố Thường Gặp

| Triệu chứng | Nguyên nhân | Xử lý |
| :--- | :--- | :--- |
| Không vào được `http://...:8080` | Sai port | Dùng port **8000** |
| BACnet unicast timeout | Tailscale chiếm route | `sudo ip route add 192.168.20.0/24 dev ens38 table 52` |
| Discovery trả về rỗng | Race condition BAC0 | Đã fix: dùng `list()` khi iterate discoveredDevices |
| Service không start | Port bị chiếm hoặc venv lỗi | `ss -tlnp \| grep 8000` → kill process cũ |
| Pi 3 OOM crash | Quá nhiều thiết bị | Giảm số lượng MS/TP device, tối đa ~20 |
| `history.db` to | Chưa cấu hình retention | Settings → System → Data Retention → Run Cleanup |

---

## 11. Checklist Bàn Giao

- [ ] SSH vào Ubuntu server thành công (`user@100.74.25.27`)
- [ ] Web UI mở được tại `http://100.74.25.27:8000`
- [ ] BACnet discover thấy thiết bị
- [ ] MQTT broker kết nối được
- [ ] SSH vào Pi 5 thành công
- [ ] SSH vào Pi 3 thành công
- [ ] MS/TP bridge trên Pi 5/Pi 3 đang active
- [ ] Tailscale kết nối cả 3 thiết bị

---

*Tài liệu này tổng hợp thông tin thực tế từ hệ thống tính đến 2026-03-16.*
*Các mục "(cập nhật)" cần bổ sung thông tin Pi 5 và Pi 3 khi có.*
