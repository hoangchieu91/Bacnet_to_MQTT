# Tài liệu Bàn giao — BACnet-MQTT Gateway V2

**Phiên bản:** 2.x  
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

**Quan trọng:** Frontend update → chỉ rsync `dist/` → **NGINX tự pick up, không restart service, không gây BACnet disconnection**.


---

## 2. Technology Stack

| Layer | Công nghệ | Phiên bản |
|-------|-----------|-----------|
| Backend | Python / FastAPI | 3.11+ / 0.110+ |
| BACnet | BAC0 + BACpypes3 | 22.x + 0.19+ |
| MQTT | paho-mqtt | 1.6+ |
| Database | SQLite | 3.x |
| Frontend | React + Vite | 18.x + 5.x |
| CSS | Tailwind CSS | 3.x |
| Grid | AG-Grid React | 31.x |
| Charts | Recharts | 2.x |
| HTTP Server | Uvicorn / NGINX | |

---

## 3. Cấu trúc thư mục

```
Bacnet_MQTT/
├── backend/
│   ├── main.py               ← FastAPI app, tất cả API endpoints
│   ├── gateway_engine.py     ← Polling loop, COV handler, MQTT publish
│   ├── bacnet_service.py     ← BAC0 wrapper: discover, read, write, COV
│   ├── bacnet_listener.py    ← Broadcast response fix, MSTP address parser
│   ├── config_manager.py     ← Đọc/ghi runtime_config.json
│   ├── history_store.py      ← SQLite history & event logging
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
│   └── history.db            ← SQLite: point history + events
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

## 6. COV (Change of Value)

Thay vì gateway poll 10s/lần, device tự **push** khi giá trị thay đổi.

```
Poll mode:  Gateway → Read every 10s → 6 packets/min/point
COV mode:   Subscribe → Device pushes ONLY on change → ≈ 0 packets khi stable
```

**Auto-fallback:** Nếu device từ chối COV 3 lần liên tiếp → tự đổi về Poll.

---

## 7. MQTT Topics

```
{prefix}/{device_id}/{object_type}/{object_instance}/value
```

Ví dụ:
```
bacnet/10121/analogValue/1201/value
→ {"value": 23.5, "timestamp": "2026-03-10T...", "source": "poll"}

bacnet/10121/binaryOutput/3/value  
→ {"value": "active", "source": "cov"}
```

**Command topic (ghi ngược lại BACnet):**
```
{prefix}/cmd/{device_id}/{object_type}/{object_instance}/write
→ {"value": 50.0, "priority": 14}
```

---

## 8. Service Management (systemd)

```bash
# Xem log realtime
journalctl -u bacnet-gateway -f

# Restart
sudo systemctl restart bacnet-gateway

# Status
systemctl status bacnet-gateway

# BACnet routing fix (cần khi có Tailscale)
sudo systemctl restart bacnet-routes
```

---

## 9. API Endpoints quan trọng

| Method | Path | Mô tả |
|--------|------|-------|
| GET | `/api/status` | Trạng thái gateway + MQTT |
| GET | `/api/health` | CPU/RAM/Disk/Temp |
| GET | `/api/system/services` | Systemd services + port health |
| GET | `/api/mappings` | List tất cả mappings |
| POST | `/api/mappings` | Tạo mapping mới |
| PUT | `/api/mappings/{id}` | Update mapping |
| DELETE | `/api/mappings/{id}` | Xóa mapping |
| POST | `/api/mappings/bulk-update` | Bulk update nhiều mappings |
| GET | `/api/mappings/export` | Export CSV |
| POST | `/api/mappings/import` | Import CSV/JSON |
| GET | `/api/history/{id}` | Lịch sử giá trị một point |
| GET | `/api/bacnet/devices` | List devices đã discover |
| GET | `/api/devices/health` | Online/offline status + fail count |
| POST | `/api/bacnet/discover` | Trigger discovery |
| POST | `/api/bacnet/read` | Đọc một property |
| POST | `/api/bacnet/write` | Ghi một property |
| GET | `/api/events` | Event log (filter: type, device, time, search) |
| GET | `/api/events/online-chart` | Biểu đồ online/offline events theo giờ |
| WS | `/ws` | WebSocket realtime updates |

---

## 10. Lưu ý vận hành

1. **Đừng chạy 2 instance backend cùng lúc** — xung đột UDP port 47808
2. **BACnet và gateway phải cùng subnet** (hoặc có BBMD)  
3. **Tailscale/VPN** can thiệp routing → chạy `bacnet-routes.service`
4. **COV limit per device** ~20–50 subscriptions; đừng COV hết tất cả points
5. **Priority 14** là default write — không override điều khiển tự động (priority 1–7)
6. **history.db** có thể lớn: check `data/history.db` định kỳ (hiện ~18MB / 160K records)
