# BACnet-MQTT Gateway V2

Hệ thống chuyển đổi giao thức BACnet/IP sang MQTT với giao diện web giám sát và điều khiển theo thời gian thực.

**Ngày cập nhật:** 2026-03-10

---

## Mục Lục

1. [Tổng Quan](#tổng-quan)
2. [Kiến Trúc Hệ Thống](#kiến-trúc-hệ-thống)
3. [Tính Năng](#tính-năng)
4. [Cài Đặt](#cài-đặt)
5. [Cấu Hình](#cấu-hình)
6. [MQTT Topics](#mqtt-topics)
7. [REST API](#rest-api)
8. [Giao Diện Web](#giao-diện-web)
9. [Vận hành & Deploy](#vận-hành--deploy)

---

## Tổng Quan

BACnet-MQTT Gateway là cầu nối giữa thiết bị tự động hóa tòa nhà (BACnet/IP, BACnet MS/TP) và hệ thống IoT (MQTT). Chạy trên Ubuntu Server hoặc Raspberry Pi, triển khai tại hiện trường, kết nối trực tiếp với mạng BACnet qua Ethernet.

**Công nghệ sử dụng:**
- **Python 3.11+** + **FastAPI** — Backend REST API
- **BAC0** (BACpypes3) — BACnet/IP + BACnet MS/TP stack
- **paho-mqtt 2.x** — MQTT client
- **SQLite** — Lưu lịch sử và event log
- **React + Vite** — Giao diện web (dark mode, mobile-friendly)
- **Recharts** — Biểu đồ lịch sử và dashboard
- **NGINX** — Serve static frontend + reverse proxy API

---

## Kiến Trúc Hệ Thống

```
┌─────────────────────────────────────────────────────────────┐
│                       Gateway Server                         │
│                                                              │
│  ┌─────────────┐         ┌──────────────────────────────┐   │
│  │   NGINX     │         │   FastAPI Backend             │   │
│  │  Port 8080  │─/api/──►│   Uvicorn : 8000 (internal)  │   │
│  │  (public)   │─/ws/───►│   gateway_engine.py          │   │
│  │             │         │   bacnet_service.py           │   │
│  │  Serves     │         │   history_store.py (SQLite)   │   │
│  │  dist/ SPA  │         └────────────┬─────────────────┘   │
│  └─────────────┘                      │ BAC0 / BACpypes3     │
│                                       │ UDP Port 47808       │
└───────────────────────────────────────┼─────────────────────┘
                                        │
              ┌─────────────────────────┼──────────────────┐
              │           BACnet Network│                   │
              ▼                         ▼                   ▼
       BACnet/IP Devices          MS/TP Devices         BBMD Router
       (CPO-RL4, CPO-PC-6A...)   (qua RS-485 router)
```

**Lưu ý kiến trúc:**
- NGINX (port 8080) serve React SPA trực tiếp từ `frontend_v2/dist/` — **không cần restart khi update UI**
- Backend chỉ listen `127.0.0.1:8000` — không exposé ra ngoài trực tiếp
- Frontend update → chỉ cần `rsync dist/` → zero BACnet downtime

---

## Tính Năng

### BACnet
- **Device Discovery** — WHO-IS broadcast, phát hiện tự động tất cả device BACnet/IP
- **MS/TP Support** — Đọc thiết bị qua BACnet router (địa chỉ dạng `10.0.1.2:3`)
- **COV (Change of Value)** — Subscribe device push thay vì poll; fallback tự động về Poll nếu COV thất bại 3 lần
- **Read/Write** — Ghi giá trị theo Priority Array 16 mức; nhả ưu tiên (null write)
- **MSV/MSI/MSO** — Hiển thị state text (`1 — Auto`, `2 — Manual`...)
- **COV mode selection** — Có thể đổi read_mode cho nhiều points cùng lúc

### Quản lý
- **Mappings (Point Config)** — CRUD, bulk update, import/export CSV
- **Groups** — Gom nhóm points, filter nhanh
- **Scheduler** — Ghi BACnet value theo lịch (cron-like), persist qua restart
- **Device Registry** — Ghi nhớ device đã discover qua các lần restart

### Monitoring
- **Dashboard** — Stat cards, Service Health (services + ports), biểu đồ Online↔Offline events, Recent Events với filter
- **Device Health** — Grid trạng thái online/offline từng device, fail count, last_seen
- **Charts** — Lịch sử giá trị từng point (zoom, multi-series)
- **Anomaly Detection** — Rule-based, custom thresholds, event log

### MQTT
- Publish BACnet values lên broker (topic có thể tùy chỉnh)
- Subscribe command topics để write ngược lại BACnet
- Dynamic point management qua MQTT

---

## Cài Đặt

```bash
# Clone
git clone <repo> /home/user/bacnet_mqtt_gateway
cd /home/user/bacnet_mqtt_gateway

# Cài đặt (cần sudo)
sudo bash scripts/install.sh
```

Script `install.sh` tự động:
1. Cài `nginx`, `python3-venv`
2. Tạo Python venv + install requirements
3. Cài `bacnet-gateway.service` (uvicorn port 8000)
4. Config NGINX site (port 8080 → static + proxy)
5. Fix file permissions cho NGINX
6. Enable + start cả 2 services

**Web UI sau khi cài:** `http://<server-ip>:8080`

---

## Cấu Hình

File: `config/runtime_config.json` (tạo lần đầu từ `config/default_config.json`)

```json
{
  "bacnet": {
    "ip": "192.168.1.100",
    "mask": "24",
    "port": 47808,
    "device_id": 3056882,
    "route_aware": true,
    "auto_discover": true
  },
  "mqtt": {
    "url": "mqtt://broker.example.com:1883",
    "username": "",
    "password": "",
    "topic_prefix": "bacnet",
    "qos": 0
  }
}
```

| Tham số | Ý nghĩa |
|---------|---------|
| `bacnet.ip` | IP của NIC kết nối mạng BACnet |
| `bacnet.mask` | Subnet mask (CIDR: 24 = /24 = 255.255.255.0) |
| `bacnet.route_aware` | `true` khi có MS/TP hoặc BBMD router |
| `mqtt.url` | `mqtt://host:port` hoặc `mqtts://host:port` (TLS) |

---

## MQTT Topics

### Publish (BACnet → MQTT)

```
{prefix}/{device_id}/{object_type}/{object_instance}/value
```
Payload: `{"value": 23.5, "timestamp": "2026-...", "source": "poll"|"cov"}`

### Commands (MQTT → BACnet)

| Topic | Payload | Chức năng |
|-------|---------|-----------|
| `{prefix}/cmd/write/{dev}/{type}/{inst}` | `{"value": 72.5, "priority": 8}` | Ghi giá trị |
| `{prefix}/cmd/release/{dev}/{type}/{inst}` | `{"priority": 8}` hoặc `{"priority": "all"}` | Nhả ưu tiên |
| `{prefix}/cmd/add_point` | `{"device_id":..., "object_type":..., ...}` | Thêm point |
| `{prefix}/cmd/remove_point` | `{"mapping_id": "..."}` | Xóa point |

---

## REST API

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| GET | `/api/status` | Trạng thái gateway + MQTT |
| GET | `/api/health` | CPU/RAM/Disk/Temp |
| GET | `/api/system/services` | Systemd services + port health |
| GET | `/api/bacnet/devices` | Devices đã discover (live + registry) |
| POST | `/api/bacnet/discover` | Trigger device discovery |
| GET | `/api/devices/health` | Online/offline status từng device |
| GET | `/api/mappings` | List tất cả mappings |
| POST | `/api/mappings` | Tạo mapping |
| PUT | `/api/mappings/{id}` | Cập nhật mapping |
| DELETE | `/api/mappings/{id}` | Xóa mapping |
| POST | `/api/mappings/bulk-update` | Update nhiều mappings |
| GET | `/api/mappings/export` | Export CSV |
| POST | `/api/mappings/import` | Import CSV/JSON |
| POST | `/api/bacnet/read` | Đọc property BACnet |
| POST | `/api/bacnet/write` | Ghi giá trị BACnet |
| GET | `/api/history/{id}` | Lịch sử giá trị 1 point |
| GET | `/api/events` | Event log (filter: type, device, time, search) |
| GET | `/api/events/online-chart` | Biểu đồ online/offline theo giờ |
| WS | `/ws` | WebSocket real-time updates |

---

## Giao Diện Web

| Trang | Chức năng |
|-------|-----------|
| **Dashboard** | Stat cards, Service Health, Online↔Offline chart, Recent Events (filter) |
| **Device Health** | Grid online/offline, fail count, last_seen, point count |
| **Devices** | Discover, duyệt object list, thêm vào mapping |
| **Mappings** | CRUD points, COV/Poll mode, Priority Array, import/export CSV |
| **Groups** | Quản lý nhóm points |
| **Charts** | Lịch sử giá trị, multi-series, zoom |
| **Anomaly** | Cảnh báo rule-based, event log |
| **Scheduler** | Ghi BACnet tự động theo lịch |
| **Settings** | BACnet config, MQTT config, Auth |
| **Logs** | System logs real-time |

---

## Vận hành & Deploy

### Deploy nhanh

```bash
# Chỉ update UI (KHÔNG restart service, zero downtime)
./scripts/deploy-frontend.sh

# Update backend Python code (có restart, BACnet reconnect ~30s)
./scripts/deploy-backend.sh
```

### Quản lý service

```bash
sudo systemctl status bacnet-gateway nginx
sudo systemctl restart bacnet-gateway   # Restart backend
sudo systemctl restart nginx            # Reload nginx (không ảnh hưởng BACnet)
journalctl -u bacnet-gateway -f         # Log real-time
```

### Lưu ý vận hành

1. **Đừng chạy 2 instance backend cùng lúc** — xung đột UDP port 47808
2. **BACnet và gateway phải cùng subnet** (hoặc có BBMD router)
3. **Tailscale/VPN** can thiệp routing → chạy `bash scripts/bacnet-routes.sh`
4. **COV limit per device** ~20–50 subscriptions; đừng COV hết tất cả points
5. **Priority 14** là default write — không override BMS/schedule (priority 1–7)
6. **history.db** tăng dần: định kỳ check `data/history.db`
