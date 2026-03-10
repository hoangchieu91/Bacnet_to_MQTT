# Tài liệu Bàn giao — BACnet-MQTT Gateway V2

**Phiên bản:** 2.1  
**Ngày cập nhật:** 2026-03-10  
**Môi trường deploy:** Ubuntu Server (Raspberry Pi / x86)  
**Web UI:** `http://<server-ip>:8080` (NGINX)


---

## 1. Kiến trúc tổng thể

```
┌─────────────────────────────────────────────────────────────────┐
│                        Gateway Server                           │
│                                                                 │
│  ┌─────────────┐    ┌──────────────────┐    ┌──────────────┐  │
│  │   NGINX     │    │  FastAPI Backend  │    │  BAC0/       │  │
│  │  Port 8080  │───►│  Uvicorn:8000    │◄──►│  BACpypes3   │  │
│  │ (static+    │ /api│ (localhost only) │    │  Port 47808  │  │
│  │  proxy)     │ /ws │                  │    │  UDP BACnet  │  │
│  └──────┬──────┘    └──────┬───────────┘    └──────────────┘  │
│         │                  │                                    │
│  Serves │          ┌───────▼───────┐                           │
│  dist/  │          │  SQLite DB    │                           │
│  (SPA)  │          │  history.db   │                           │
│         │          └───────────────┘                           │
│         │                                                       │
│  ┌──────▼─────────────────────────────────────────────────┐   │
│  │            WebSocket /ws (proxied via NGINX)             │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
         │                                          │
     Port 8080                              BACnet/IP UDP
     (NGINX)                               BACnet Devices (LAN)
         │
         ▼ MQTT Publish
    MQTT Broker (cloud / local)
         │
         ▼ Subscribe
    SCADA / Home Assistant / Node-RED
```

> **Frontend update** → chỉ rsync `dist/` → NGINX pick up ngay, **không restart service, không gây BACnet disconnect**.


---

## 2. Technology Stack

| Layer | Công nghệ | Phiên bản |
|-------|-----------|-----------|
| Backend | Python / FastAPI | 3.11+ / 0.110+ |
| BACnet | BAC0 + BACpypes3 | 22.x + 0.19+ |
| MQTT | paho-mqtt | 1.6+ |
| Database | SQLite (WAL mode) | 3.x |
| Frontend | React + Vite | 18.x + 5.x |
| CSS | Tailwind CSS | 3.x |
| Grid | AG-Grid React | 31.x |
| Charts | Recharts | 2.x |
| HTTP Server | Uvicorn / NGINX | — |

---

## 3. Cấu trúc thư mục

```
Bacnet_MQTT/
├── backend/
│   ├── main.py               ← FastAPI app, tất cả API endpoints
│   ├── gateway_engine.py     ← Polling loop (RPM batch + single), COV handler
│   ├── bacnet_service.py     ← BAC0 wrapper: discover, read, write, RPM batch
│   ├── bacnet_listener.py    ← Broadcast response fix, MSTP address parser
│   ├── config_manager.py     ← Đọc/ghi runtime_config.json
│   ├── history_store.py      ← SQLite history, event log, data retention
│   ├── mqtt_service.py       ← paho-mqtt wrapper
│   ├── models.py             ← Pydantic data models
│   ├── device_registry.py    ← Device metadata cache
│   └── anomaly_engine.py     ← Rule-based anomaly detection
├── frontend_v2/
│   ├── src/
│   │   ├── App.jsx           ← Root, auth context, routing, mobile nav
│   │   ├── stores/           ← Zustand stores (mapping, device...)
│   │   └── components/       ← Pages: MappingsPage, DeviceHealthPage...
│   └── dist/                 ← Build output (served by NGINX)
├── config/
│   ├── runtime_config.json   ← BACnet IP, MQTT URL, poll settings
│   └── discovered_devices.json ← Cache thiết bị đã discover
├── data/
│   └── history.db            ← SQLite: point history + event log
├── docs/                     ← Tài liệu kỹ thuật
└── scripts/
    ├── bacnet-gateway.service ← systemd unit for backend
    ├── bacnet-routes.sh      ← Fix routing khi có Tailscale/VPN
    ├── deploy-frontend.sh    ← Deploy UI only (no backend restart)
    ├── deploy-backend.sh     ← Deploy Python code + restart service
    └── install.sh            ← Fresh install (Ubuntu/Pi)
```


---

## 4. Cấu hình (runtime_config.json)

```json
{
  "bacnet": {
    "ip": "192.168.20.113",
    "mask": "24",
    "port": 47808,
    "device_id": 3056882,
    "route_aware": true,
    "auto_discover": true
  },
  "mqtt": {
    "url": "mqtt://broker:1883",
    "username": "",
    "password": "",
    "topic_prefix": "bacnet",
    "qos": 0
  }
}
```

---

## 5. Mapping — Point Configuration

Mỗi mapping = 1 BACnet object muốn publish lên MQTT.

| Trường | Kiểu | Mô tả |
|--------|------|-------|
| `label` | string | Tên hiển thị |
| `device_id` | int | BACnet deviceInstance |
| `object_type` | string | `analogInput`, `binaryOutput`, `multiStateValue`... |
| `object_instance` | int | BACnet object instance number |
| `mqtt_topic` | string | MQTT topic publish (auto nếu để trống) |
| `read_mode` | `poll`/`cov` | Poll: gateway hỏi định kỳ. COV: device tự push |
| `poll_interval` | int (s) | Tần suất poll (giây). Không dùng nếu COV |
| `group` | string | Nhóm để lọc/quản lý |
| `enabled` | bool | Bật/tắt point |

### Import/Export CSV

```
Download (↓): Xuất tất cả mappings ra file CSV
Upload (↑):   Nhập từ CSV hoặc JSON (backward compat)
```

Định dạng CSV: xem `docs/BACNET_TECHNICAL_GUIDE.md` mục 7.

---

## 6. Polling — Chế độ đọc dữ liệu

### COV (Change of Value)

Thay vì gateway poll 10s/lần, device tự **push** khi giá trị thay đổi.

```
Poll mode:  Gateway → Read every 10s → 6 packets/min/point
COV mode:   Subscribe → Device pushes ONLY on change → ≈ 0 packets khi stable
```

**Auto-fallback:** Nếu device từ chối COV 3 lần liên tiếp → tự đổi về Poll.

### ReadPropertyMultiple — Batch Poll (v2.1)

Polling loop tự động **group mappings theo device** và tối ưu:

| Device type | Phương thức | Packets/cycle |
|-------------|-------------|---------------|
| BACnet/IP (có `.` trong địa chỉ, ví dụ `192.168.x.x`) | RPM batch (tối đa 20 objects/request) | **1 packet** |
| MS/TP qua router (ví dụ `8700:20`) | Single-read tuần tự | 1 packet/point |

```
Ví dụ: Device có 20 points (BACnet/IP)
  Trước RPM: 20 requests × 3 properties = 60 packets/cycle
  Sau  RPM:  1 RPM request               =  1 packet/cycle  (↓ ~98%)
```

Monitor RPM:
```bash
journalctl -u bacnet-gateway -f | grep "\[RPM\]"
# → [RPM] 192.168.20.10:47808: batch-read 12 objects OK
```

**Auto-fallback:** RPM thất bại → tự switch về single-read từng object.

---

## 7. MQTT Topics

```
{prefix}/{device_id}/{object_type}/{object_instance}/value
```

Ví dụ:
```
bacnet/10121/analogValue/1201/value
→ {"value": 23.5, "timestamp": "2026-03-10T...", "source": "poll"}

bacnet/10121/binaryOutput/3/value  (COV)
→ {"value": "active", "source": "cov"}

bacnet/10121/analogInput/5/value   (RPM batch)
→ {"value": 18.2, "source": "rpm"}
```

**Command topic (ghi ngược lại BACnet):**
```
{prefix}/cmd/{device_id}/{object_type}/{object_instance}/write
→ {"value": 50.0, "priority": 14}
```

---

## 8. Data Retention (v2.1)

Hệ thống tự xóa dữ liệu cũ theo cấu hình:

| Bảng | Mặc định | Cấu hình tại |
|------|----------|--------------|
| `point_history` | **90 ngày** | Settings → System → Data Retention |
| `event_log` | **180 ngày** | Settings → System → Data Retention |

Cleanup:
- **Tự động**: mỗi 60 phút (background task)
- **Thủ công**: Settings → System → "Run Cleanup Now" (hiển thị số records đã xóa + MB freed)

API:
```
GET  /api/history/stats    → DB size, total records, retention config
POST /api/history/cleanup  → trigger manual cleanup, trả về kết quả
PUT  /api/history/config   → cập nhật retention_days / event_retention_days
```

---

## 9. Service Management (systemd)

```bash
# Xem log realtime
journalctl -u bacnet-gateway -f

# Filter logs theo tính năng
journalctl -u bacnet-gateway -f | grep "\[RPM\]"       # Batch poll
journalctl -u bacnet-gateway -f | grep "Retention"     # Data cleanup
journalctl -u bacnet-gateway -f | grep "device_online" # Device status

# Restart
sudo systemctl restart bacnet-gateway

# Status
systemctl status bacnet-gateway

# BACnet routing fix (cần khi có Tailscale)
sudo systemctl restart bacnet-routes
```

---

## 10. API Endpoints quan trọng

### Gateway & System
| Method | Path | Mô tả |
|--------|------|-------|
| GET | `/api/status` | Trạng thái gateway + MQTT |
| GET | `/api/health` | CPU/RAM/Disk/Temp |
| GET | `/api/system/services` | Systemd services + port health |

### Mappings
| Method | Path | Mô tả |
|--------|------|-------|
| GET | `/api/mappings` | List tất cả mappings |
| POST | `/api/mappings` | Tạo mapping mới |
| PUT | `/api/mappings/{id}` | Update mapping |
| DELETE | `/api/mappings/{id}` | Xóa mapping |
| POST | `/api/mappings/bulk-update` | Bulk update nhiều mappings |
| GET | `/api/mappings/export` | Export CSV |
| POST | `/api/mappings/import` | Import CSV/JSON |

### BACnet
| Method | Path | Mô tả |
|--------|------|-------|
| GET | `/api/bacnet/devices` | List devices đã discover |
| POST | `/api/bacnet/discover` | Trigger discovery |
| POST | `/api/bacnet/read` | Đọc một property |
| POST | `/api/bacnet/write` | Ghi một property |

### Devices & Health
| Method | Path | Mô tả |
|--------|------|-------|
| GET | `/api/devices/health` | Online/offline status + fail count |
| GET | `/api/devices/{id}/offline-history` | Lịch sử offline + trạng thái hiện tại |

### History & Events
| Method | Path | Mô tả |
|--------|------|-------|
| GET | `/api/history/{id}` | Lịch sử giá trị một point |
| GET | `/api/history/multi` | Lịch sử nhiều points cùng lúc |
| GET | `/api/history/stats` | DB size, records, retention config |
| POST | `/api/history/cleanup` | Manual data cleanup |
| PUT | `/api/history/config` | Update retention settings |
| GET | `/api/events` | Event log (filter: type, device, severity, search) |
| GET | `/api/events/online-chart` | Biểu đồ online/offline events theo giờ |

### Realtime
| Method | Path | Mô tả |
|--------|------|-------|
| WS | `/ws` | WebSocket realtime updates (point_update, alarm, device_online...) |

---

## 11. Lưu ý vận hành

1. **Đừng chạy 2 instance backend cùng lúc** — xung đột UDP port 47808
2. **BACnet và gateway phải cùng subnet** (hoặc có BBMD)  
3. **Tailscale/VPN** can thiệp routing → chạy `bacnet-routes.service`
4. **COV limit per device** ~20–50 subscriptions; đừng COV hết tất cả points
5. **Priority 14** là default write — không override điều khiển tự động (priority 1–7)
6. **RPM chỉ kích hoạt** khi device có >1 point và địa chỉ IP (có dấu `.`); MS/TP an toàn với single-read
7. **Data Retention**: mặc định history 90 ngày, events 180 ngày — có thể thay đổi tại Settings → System

---

## 12. Sự cố thường gặp

| Triệu chứng | Nguyên nhân | Xử lý |
|-------------|-------------|-------|
| Device luôn offline dù đang hoạt động | Ping loop dùng name-comparison sai | Đã fix v2.1 (exception-based) |
| Tab "Offline History" hiện "Still offline" dù online | API không trả current_online | Đã fix v2.1 |
| BACnet traffic cao | Poll mode, chưa dùng COV/RPM | Bật COV cho points thay đổi thường xuyên; RPM tự kích hoạt với IP |
| history.db lớn | Chưa cấu hình retention | Settings → System → Data Retention → Save + Run Cleanup |
| Disconnect sau khi update UI | Deploy sai cách | Dùng `deploy-frontend.sh` (chỉ rsync dist/, không restart service) |
| BACnet không discover được | Tailscale intercept routing | `sudo systemctl restart bacnet-routes` |
