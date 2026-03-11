# HIẾN CHƯƠNG DỰ ÁN
## BACnet-MQTT Industrial Gateway — Phiên Bản 3.0

> **Phiên bản:** 1.0 | **Ngày:** 2026-03-07 | **Trạng thái:** Phê duyệt kỹ thuật

---

## 1. Tuyên Ngôn Dự Án

Hệ thống BACnet-MQTT Gateway là **cầu nối thời gian thực** giữa mạng BACnet công nghiệp và hạ tầng IoT/MQTT, phục vụ giám sát, điều khiển và lưu lịch sử toàn bộ thiết bị tòa nhà thông minh. Dự án xây dựng một gateway đủ tin cậy để vận hành **liên tục 24/7** trên môi trường sản xuất mà **không làm gián đoạn hệ thống đang chạy**.

---

## 2. Phạm Vi & Mục Tiêu

### 2.1 Mục Tiêu Cốt Lõi

| Mục tiêu | Đo lường thành công |
|---|---|
| Giám sát 700+ BACnet devices | Tất cả devices được poll, không bỏ sót |
| Phát hiện device online/offline trong ≤ 3 phút | Alert MQTT khi device drop |
| Phát hiện point hoạt động sai kịch bản | Hệ thống alarm rule theo ngưỡng + pattern |
| Không làm chậm/gián đoạn hệ thống thực | Poll rate throttle, exponential backoff |
| Lưu lịch sử 30 ngày | SQLite ring buffer, max 500MB |

### 2.2 Phạm Vi V3.0 (Sprint hiện tại)

```
✅ Đã hoàn thành:
   ├── Backend ổn định (asyncio lock, MQTT non-blocking, SQLite thread-safe)
   ├── React V2 cơ bản (8 pages, AG-Grid, Detail Panel, Scheduler)
   └── Deploy: Raspberry Pi + Ubuntu Server

🎯 Mục tiêu V3.0:
   ├── Frontend: Hoàn chỉnh tính năng còn thiếu so với V1
   ├── Frontend: Thêm Device Health Dashboard (700+ devices)
   ├── Frontend: Point Anomaly Monitor
   └── Backend: Smart Polling Engine cho scale lớn
```

---

## 3. Bối Cảnh Kỹ Thuật

### 3.1 Hạ Tầng Hiện Tại

| Thành phần | Thông tin |
|---|---|
| **Gateway Server** | Ubuntu 24.04, i7-6700, RAM 1.9GB, Disk 29GB |
| **BACnet Interface** | `ens38` — 192.168.20.113 (BACnet/IP subnet) |
| **Remote Access** | Tailscale (100.74.25.27) + OpenVPN (tun0) |
| **MQTT Broker** | Mosquitto local (Docker) |
| **Web UI** | http://100.74.25.27:8080 |
| **Database** | SQLite WAL — lịch sử 30 ngày |

### 3.2 Thách Thức Lớn: 700+ Devices

**Vấn đề:**
- BACnet polling tuần tự: 700 devices × 3 points × 10s interval = **cần xử lý 210 reads/s**
- MSTP bus chỉ xử lý được ~2-5 req/s → **không thể poll tất cả với cùng frequency**
- RAM 1.9GB → không thể cache toàn bộ state
- Discovery toàn bộ 700 devices một lúc → **network storm**, làm chết thiết bị nhạy cảm

**Giải pháp kỹ thuật đề xuất:**

```
Smart Polling Architecture (Tier-based):

Tier 1 — Critical Points  (poll 5s)   : FCU setpoint, alarm states (~100 points)
Tier 2 — Normal Points    (poll 30s)  : Temperature, humidity sensors (~500 points)  
Tier 3 — Status Points    (poll 120s) : Enable/disable status, counts (~2000 points)
Tier 4 — Archival         (poll 300s) : Energy kWh, diagnostic counters

+ Adaptive Backoff: Device không response → tăng interval x2, max 600s
+ Heartbeat Check: Mỗi 60s chỉ ping 1 property để kiểm tra online/offline
+ Batch Discovery: Quét WHO-IS theo batch 50 devices, sleep 5s giữa batches
```

---

## 4. Kiến Trúc Phần Mềm

```
┌────────────────────────────────────────────────────────────────┐
│                    FRONTEND (React V2)                         │
│  Dashboard  │  Device Health  │  Monitor  │  Anomaly Alerts   │
│  Mappings   │  Groups         │  Charts   │  Scheduler        │
└──────────────────────────────┬─────────────────────────────────┘
                               │ REST + WebSocket
┌──────────────────────────────▼─────────────────────────────────┐
│                    BACKEND (FastAPI)                            │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ BACnetService│  │GatewayEngine │  │   Smart Poll Engine  │  │
│  │ (BAC0 wrap) │  │ (Orchestrate)│  │   (Tier + Backoff)   │  │
│  └──────┬──────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         │               │                      │               │
│  ┌──────▼──────┐  ┌──────▼───────┐  ┌──────────▼───────────┐  │
│  │ MqttService │  │ HistoryStore │  │  AnomalyDetector      │  │
│  │ (Paho wrap) │  │ (SQLite WAL) │  │  (Rule Engine)        │  │
│  └─────────────┘  └──────────────┘  └──────────────────────┘  │
└──────────────────────────────┬─────────────────────────────────┘
                               │
      BACnet/IP Network ───────┘─── 700+ Devices
      MQTT Broker ────────────────── Subscribers (SCADA, dashboards)
```

---

## 5. Kế Hoạch Phát Triển

### Sprint 1 — Hoàn Chỉnh V2 Frontend (Tuần này)

| Task | Mô tả | Ưu tiên |
|---|---|---|
| Add Point modal | Form thêm point thủ công, chọn device từ dropdown | P1 |
| Bulk Edit modal | Chỉnh nhiều points cùng lúc | P1 |
| Dashboard Live Points | Bảng thời gian thực tất cả points | P1 |
| Dashboard Live Chart | Line chart Recharts real-time | P1 |
| Clone Points | Copy points sang device khác | P2 |

### Sprint 2 — Device Health Dashboard (700+ Devices)

| Task | Mô tả | Ưu tiên |
|---|---|---|
| Device grid view | 700 devices, dạng tile/card với status màu | P1 |
| Online/offline heatmap | Visual instant nhìn thấy device nào down | P1 |
| Device status API | Backend endpoint bulk status | P1 |
| Heartbeat polling | Background pinging để detect offline | P1 |
| Device filter/search | Search by name, group, network, status | P2 |

### Sprint 3 — Point Anomaly Monitor

| Task | Mô tả | Ưu tiên |
|---|---|---|
| Scenario/Rule Engine | Định nghĩa kịch bản mong đợi (if X then Y) | P1 |
| Anomaly Alert list | Danh sách points đang lệch kịch bản | P1 |
| Alarm history timeline | Graph hiển thị lịch sử alarm | P2 |
| MQTT alert publish | Đẩy anomaly alert qua MQTT | P2 |

### Sprint 4 — Smart Polling Engine (Backend)

| Task | Mô tả | Ưu tiên |
|---|---|---|
| Tier-based polling config | Phân loại points theo tier 1-4 | P1 |
| Adaptive backoff | Tự động giảm tần suất device offline | P1 |
| Batch WHO-IS discovery | Quét theo lô, throttled | P1 |
| Polling metrics | Thống kê poll success/fail/latency | P2 |

---

## 6. Nguyên Tắc Vận Hành (NON-NEGOTIABLE)

> Đây là các ràng buộc cứng không được vi phạm khi phát triển tính năng mới.

### 6.1 Không Làm Gián Đoạn Hệ Thống

```
❌ KHÔNG:
  - Gửi WHO-IS broadcast liên tục trong 10 giây
  - Poll tất cả 700+ devices cùng lúc khi start
  - Chặn event loop > 1 giây

✅ NÊN:
  - Batch discovery: mỗi batch 50 device, sleep 5s giữa batches
  - Ramp up: start poll từ 10 points, tăng dần
  - Circuit breaker: ngừng poll device sau 5 lần fail liên tiếp
```

### 6.2 Hierarchical Polling Strategy

```python
TIER_CONFIG = {
    1: {"interval": 5,   "desc": "Critical control points"},
    2: {"interval": 30,  "desc": "Sensor measurements"},
    3: {"interval": 120, "desc": "Status/mode points"},
    4: {"interval": 300, "desc": "Archival/energy data"},
}

# Adaptive backoff per device
def get_adaptive_interval(device_id, base_interval):
    fail_count = device_fail_counts.get(device_id, 0)
    multiplier = min(2 ** fail_count, 64)  # Max 64× slowdown
    return min(base_interval * multiplier, 3600)  # Max 1h
```

### 6.3 Giám Sát Tài Nguyên (Vì RAM Chỉ 1.9GB)

```
RAM thresholds (GatewayEngine):
  ≥ 95% → PAUSE polling hoàn toàn
  ≥ 90% → THROTTLE: x2 tất cả intervals
  ≥ 80% → WARNING: log cảnh báo

Target operating RAM:
  Baseline FastAPI: ~80MB
  Per-device cache: ~1KB × 700 = ~700KB
  History buffer:   ~50MB max
  BAC0 network:     ~100MB
  → Operating budget: ~1.5GB → còn 400MB headroom
```

---

## 7. Giám Sát Kịch Bản (Scenario Monitoring)

Đây là tính năng cốt lõi mà **không có trong bất kỳ gateway thương mại nào** ở phân khúc này.

### 7.1 Định Nghĩa Kịch Bản

```json
{
  "scenario_id": "FCU_COOLING_MODE",
  "name": "FCU phải bật cooling khi nhiệt độ > 26°C",
  "trigger": {
    "point_id": "temp_room_205",
    "condition": "value > 26.0"
  },
  "expected": {
    "point_id": "fcu_205_mode",
    "expected_value": "cooling",
    "tolerance_seconds": 120
  },
  "severity": "warning",
  "notify_mqtt": "alerts/fcu/scenario_fail"
}
```

### 7.2 Rule Engine Logic

```
Event: temp_room_205 = 27.5°C (> 26°C trigger)
  → Start timer 120s
  → At T+120s: read fcu_205_mode
    IF fcu_205_mode == "cooling" → ✅ OK, clear alert
    IF fcu_205_mode != "cooling" → 🔴 Anomaly detected!
       → Log to event_log (severity=warning)
       → Publish to MQTT: alerts/fcu/scenario_fail
       → Show in Anomaly page
```

---

## 8. Quy Trình Deploy An Toàn (Live System)

```bash
# 1. Test local trước khi deploy
source venv/bin/activate
python -m pytest backend/tests/ -v

# 2. Build frontend (chỉ copy files, không restart service)
cd frontend_v2 && npm run build

# 3. Deploy — chỉ copy files thay đổi
rsync -avz --exclude=node_modules \
  frontend_v2/dist/ user@100.74.25.27:/home/user/bacnet_mqtt_gateway/frontend_v2/dist/

# 4. Restart service (< 5 giây downtime)
ssh user@100.74.25.27 "sudo systemctl restart bacnet-gateway"

# 5. Verify
curl -s http://100.74.25.27:8080/api/status | python -m json.tool
```

---

## 9. Metrics Theo Dõi (KPIs)

| Metric | Mục tiêu | Cảnh báo |
|---|---|---|
| Device online rate | ≥ 98% | < 95% |
| Poll success rate | ≥ 99% | < 97% |
| Avg poll latency (BACnet) | < 500ms | > 2000ms |
| API response time | < 200ms | > 1000ms |
| DB size | < 400MB | > 450MB |
| RAM usage | < 80% | > 85% |
| Anomaly resolution time | < 30 phút | > 2 giờ |

---

## 10. Danh Sách Rủi Ro

| Rủi ro | Xác suất | Ảnh hưởng | Biện pháp |
|---|---|---|---|
| RAM cạn khi poll 700 devices | Cao | Cao | RAM guardian + tier polling |
| MSTP bus overload | Cao | Cao | Serial lock + adaptive backoff |
| DB grow vô hạn | Trung bình | Trung bình | Ring buffer + retention cleanup |
| BACnet network storm khi start | Cao | Cao | Batch WHO-IS + ramp up |
| Ubuntu server disk đầy | Thấp | Cao | Monitor disk, log rotation |
| Deploy gây restart giữa giờ cao điểm | Trung bình | Thấp | Deploy script + health check |

---

*Tài liệu này được duy trì bởi kỹ sư dự án. Mọi thay đổi kiến trúc quan trọng cần cập nhật document này.*
