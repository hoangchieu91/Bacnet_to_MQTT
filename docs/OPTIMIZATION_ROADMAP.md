# Optimization & Upgrade Roadmap — BACnet-MQTT Gateway

> Nghiên cứu các hướng tối ưu và nâng cấp cho hệ thống.  
> Phân loại theo độ ưu tiên và độ phức tạp.
>
> **Cập nhật:** 2026-03-10 | Legend: ✅ Đã xong · ⚡ Đang làm · 📋 Planned

---

## ✅ Đã Implement (Tổng kết)

| Tính năng | Mô tả |
|-----------|-------|
| ✅ COV Support | Subscribe device push; auto-fallback Poll 3 lần thất bại |
| ✅ COV Bulk Mode Switch | Đổi read_mode nhiều points cùng lúc từ UI |
| ✅ CSV Import/Export | Mappings xuất/nhập CSV (backward compat JSON) |
| ✅ MSV/MSI/MSO state text | Hiển thị `1 — Auto`, `2 — Manual` từ state_text |
| ✅ Scheduler | Ghi BACnet theo lịch, persist restart, manual trigger |
| ✅ Device Registry | Ghi nhớ device qua restart, merge live + persisted |
| ✅ NGINX Architecture | Frontend tách khỏi backend; update UI không restart BACnet |
| ✅ Service Health Panel | Dashboard hiển thị systemd services, ports, BACnet UDP |
| ✅ Dashboard Stability | Device Online/Offline chart, Recent Events với filter đầy đủ |
| ✅ Event Log | SQLite event_log: device_online/offline, anomaly, system |
| ✅ Anomaly Detection | Rule-based engine, custom thresholds |

---

## 1. Tối ưu BACnet Network (Ngay bây giờ)

### 1.1 ⚡ ReadPropertyMultiple (RPM) — Ưu tiên cao

**Vấn đề hiện tại:** Gateway đọc từng property riêng lẻ:
```
READ analogInput 1 presentValue     → 1 packet
READ analogInput 1 units            → 1 packet  
READ analogInput 1 statusFlags      → 1 packet
→ N properties = N packets
```

**Giải pháp:** Gộp vào 1 RPM request:
```
READ analogInput 1 [presentValue, units, statusFlags] → 1 packet
→ Giảm 60–80% BACnet traffic
```

**Cách implement:**
```python
result = await network.readMultiple(
    f"{address} {obj_type} {instance} presentValue units statusFlags"
)
```

**Tác động:** Quan trọng nhất khi polling 100+ points qua MS/TP (chậm).

---

### 1.2 🔄 Adaptive Poll Interval

**Vấn đề:** Tất cả points poll cùng 1 interval (10s), bất kể tần suất thay đổi.

**Giải pháp:** Tự động điều chỉnh interval dựa trên lịch sử thay đổi:
```
Point thay đổi 50 lần/giờ  → interval 5s (cần realtime)
Point thay đổi 1 lần/ngày  → interval 300s (tiết kiệm)
Point không thay đổi 1 tuần → interval 3600s
```

**Schema DB thêm:**
```sql
ALTER TABLE mappings ADD COLUMN change_frequency REAL DEFAULT 0;
ALTER TABLE mappings ADD COLUMN auto_interval INTEGER DEFAULT 0;
```

---

### 1.3 📦 Device-level Batching

**Vấn đề:** 100 points trên 1 device = 100 read requests riêng.

**Giải pháp nhóm theo device:**
```python
# Thay vì:
for mapping in mappings:
    read(mapping)

# Dùng:
for device_id, device_mappings in group_by_device(mappings):
    read_multiple_objects(device_id, device_mappings)  # 1 RPM cho toàn bộ
```

---

## 2. COV Improvements (Ngắn hạn)

### 2.1 COV với Confirmed Notification

Hiện tại dùng **Unconfirmed** COV (không biết device nhận được không).

```python
# Unconfirmed (hiện tại) — nhanh, nhẹ, nhưng có thể mất gói
confirmed=False

# Confirmed — device ACK, đảm bảo delivered, dùng khi critical points
confirmed=True
```

**Khuyến nghị:** Binary outputs liên quan an toàn → dùng Confirmed.

### 2.2 COV Increment (cov-increment property)

Cho analog points, BACnet hỗ trợ **COV Increment** — chỉ push khi thay đổi > ngưỡng:

```
Nhiệt độ thay đổi 0.01°C → KHÔNG push (noise)
Nhiệt độ thay đổi 0.5°C  → PUSH (significant)
```

```python
# Đọc cov-increment của device
cov_inc = await network.read(f"{address} analogInput 1 covIncrement")
# Đặt ngưỡng khi subscribe
# (BACnet 2016+ hỗ trợ covIncrement trong SubscribeCOV request)
```

### 2.2 COV Increment (cov-increment property)

Cho analog points, BACnet hỗ trợ **COV Increment** — chỉ push khi thay đổi > ngưỡng:

```
Nhiệt độ thay đổi 0.01°C → KHÔNG push (noise)
Nhiệt độ thay đổi 0.5°C  → PUSH (significant)
```

```python
# Đọc cov-increment của device
cov_inc = await network.read(f"{address} analogInput 1 covIncrement")
# Đặt ngưỡng khi subscribe
# (BACnet 2016+ hỗ trợ covIncrement trong SubscribeCOV request)
```

### ✅ 2.3 COV Dashboard

> **Đã implement:** Service Health Panel + Dashboard Online/Offline chart + Recent Events panel

Đã có:
- Biểu đồ số lượng device online/offline theo giờ (6h/24h/3d/7d)
- Events panel filter theo: time, event type, severity, device, search
- Service & Ports health (systemd status, TCP port check, BACnet UDP bind)

Còn thiếu: Số COV subscriptions active per device, notifications nhận/giờ

---

## 3. Data & Storage (Trung hạn)

### 3.1 Time-Series Database — InfluxDB / TimescaleDB

**Vấn đề hiện tại:** SQLite không tối ưu cho time-series data.

```
history.db: 160K records → 18MB (SQLite)
             1M records → có thể query chậm
```

**Giải pháp:** Migrate sang InfluxDB 2.x:

```python
from influxdb_client import InfluxDBClient
client.write_api().write(
    bucket="bacnet",
    record=Point("sensor").tag("device", "10121").field("value", 23.5)
)
```

**Lợi ích:**
- Query theo time range nhanh hơn 10–100x
- Tự động downsampling (1 tuần → avg/5min)
- Tích hợp Grafana dashboard

### 3.2 Data Retention Policy

```sql
-- Giữ raw data 7 ngày
-- Giữ 5-min average 3 tháng  
-- Giữ daily average 2 năm
```

### 3.3 MQTT QoS và Persistent Session

Hiện tại QoS=0 (fire and forget). Cân nhắc:
- **QoS 1** cho points quan trọng (đảm bảo broker nhận)
- **Retained messages** để client mới subscribe biết ngay last value

```python
mqtt.publish(topic, payload, qos=1, retain=True)
```

---

## 4. Protocol Extensions (Dài hạn)

### 4.1 BACnet Alarms và Events

BAC0 hỗ trợ `ConfirmedEventNotification`. Push alarm trực tiếp từ controller:

```
Device phát hiện fault → EventNotification → Gateway → MQTT alarm/topic
```

Không cần poll `statusFlags` liên tục — thiết bị tự báo fault.

### 4.2 BACnet Trending (TrendLog)

Các controller hiện đại (CPO-RL4) có built-in trend log. Thay vì gateway tự lưu, **đọc trực tiếp TrendLog object** từ device:

```python
trend = await network.read(f"{address} trendLog 1 logBuffer")
# → Danh sách records có timestamp từ device
```

Ưu điểm: Không mất dữ liệu khi gateway offline.

### 4.3 Multi-Gateway Architecture

Khi hệ thống lớn (>1000 points):

```
┌──────────────┐    ┌──────────────┐
│  Gateway-A   │    │  Gateway-B   │
│  Site 1      │    │  Site 2      │
│  BACnet/IP   │    │  MS/TP       │
└──────┬───────┘    └──────┬───────┘
       └──────────┬─────────┘
                  ▼ MQTT
           ┌──────────────┐
           │  Central     │
           │  Aggregator  │
           │  + InfluxDB  │
           └──────────────┘
```

Mỗi gateway nhỏ, lightweight, chỉ poll 100–200 points.

### 4.4 BACnet Secure Connect (BACnet/SC)

Chuẩn mới (ASHRAE 135-2020) — BACnet over WebSocket/TLS:

```
BACnet/IP (UDP, plain) → BACnet/SC (WSS, mã hóa)
```

Phù hợp khi device và gateway ở network khác nhau qua internet.

---

## 5. Frontend & UX

### 5.1 Grafana Integration

Expose `/api/grafana` compatible endpoint → kết nối Grafana SimpleJSON datasource.

### 5.2 Floor Plan / SVG Overlay

Cho phép drag-and-drop đặt points lên bản vẽ mặt bằng:
```
SVG floor plan + point overlay → realtime values update theo màu/icon
```

### 5.3 Notifications & Alerting

- **Alert rule engine** đã có (AnomalyEngine) nhưng chưa có notification output
- Thêm: Email / Telegram / Webhook khi anomaly triggered

### 5.4 Mobile App (PWA)

Frontend hiện có mobile responsive. Upgrade lên PWA:
- Offline cache dashboard
- Push notifications (Web Push API)
- Add to home screen

---

## 6. Bảo mật

| Điểm cần cải thiện | Độ ưu tiên |
|--------------------|-----------|
| HTTPS (TLS cho API và Frontend) | Cao |
| JWT token expiry ngắn + refresh token | Trung bình |
| Rate limiting cho API | Trung bình |
| BACnet network segregation (VLAN) | Cao (hạ tầng) |
| Audit log cho BACnet write operations | Trung bình |
| Mã hóa config (MQTT password) | Thấp |

---

## 7. Ma trận Ưu tiên

| Task | Impact | Effort | Status |
|------|--------|--------|--------|
| ✅ COV Support | ⭐⭐⭐ Cao | ⭐⭐ Med | **Done** |
| ✅ CSV Export | ⭐⭐ Med | ⭐ Low | **Done** |
| ✅ Service Health Panel | ⭐⭐ Med | ⭐⭐ Med | **Done** |
| ✅ NGINX tách frontend | ⭐⭐⭐ Cao | ⭐⭐ Med | **Done** |
| ✅ Event Log + Dashboard | ⭐⭐⭐ Cao | ⭐⭐ Med | **Done** |
| ReadPropertyMultiple | ⭐⭐⭐ Cao | ⭐⭐ Med | **Ngay** |
| MQTT retain/QoS1 | ⭐⭐ Med | ⭐ Low | **Ngay** |
| COV subscriptions view | ⭐⭐ Med | ⭐ Low | **Ngay** |
| HTTPS/TLS | ⭐⭐⭐ Cao | ⭐⭐ Med | **Ngắn hạn** |
| Alert Notification (Email/Telegram) | ⭐⭐⭐ Cao | ⭐⭐ Med | **Ngắn hạn** |
| Adaptive poll interval | ⭐⭐ Med | ⭐⭐ Med | Ngắn hạn |
| Device batching (RPM) | ⭐⭐⭐ Cao | ⭐⭐⭐ High | Trung hạn |
| InfluxDB migration | ⭐⭐⭐ Cao | ⭐⭐⭐ High | Trung hạn |
| BACnet TrendLog reader | ⭐⭐⭐ Cao | ⭐⭐ Med | Trung hạn |
| BACnet Alarms (EventNotification) | ⭐⭐⭐ Cao | ⭐⭐⭐ High | Dài hạn |
| Grafana integration | ⭐⭐ Med | ⭐⭐ Med | Dài hạn |
| PWA / Mobile App | ⭐ Low | ⭐⭐ Med | Dài hạn |

---

## 8. Đề xuất Nâng cấp Ưu tiên Tiếp theo

### 🔴 Ngay bây giờ (1–2 sprint)

1. **MQTT QoS 1 + Retain** — chỉ cần 1 dòng code, impact lớn: đảm bảo broker nhận và client mới biết last value ngay
2. **HTTPS cho NGINX** — Let's Encrypt (nếu có domain) hoặc self-signed cert; ẩn MQTT password trên đường truyền
3. **Alert Notification** — Gửi Telegram / Webhook khi anomaly triggered; AnomalyEngine đã có, chỉ cần thêm output
4. **ReadPropertyMultiple** — Gộp reads trong 1 device thành 1 RPM packet; giảm 60–80% BACnet traffic khi poll nhiều points

### 🟡 Ngắn hạn (1–2 tháng)

5. **Data Retention** — Tự động xóa/archive history.db records cũ hơn 90 ngày
6. **Grafana SimpleJSON** — Expose `/api/grafana` endpoint → kết nối Grafana dashboard enterprise
7. **COV subscriptions panel** — Hiển thị số COV active/device, last notification, re-subscribe button
