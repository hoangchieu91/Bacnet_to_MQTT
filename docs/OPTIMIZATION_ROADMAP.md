# Optimization & Upgrade Roadmap — BACnet-MQTT Gateway

> Nghiên cứu các hướng tối ưu và nâng cấp cho hệ thống.  
> Phân loại theo độ ưu tiên và độ phức tạp.

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

### 2.3 COV Dashboard

Thêm tab trong Device Health để xem:
- Số COV subscriptions active per device
- Số notifications nhận được/giờ
- Last notification timestamp
- Manual re-subscribe button

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

| Task | Impact | Effort | Priority |
|------|--------|--------|----------|
| ReadPropertyMultiple | ⭐⭐⭐ Cao | ⭐⭐ Med | **Ngay** |
| COV Dashboard | ⭐⭐ Med | ⭐ Low | **Ngay** |
| Adaptive poll interval | ⭐⭐ Med | ⭐⭐ Med | Ngắn hạn |
| Device batching | ⭐⭐⭐ Cao | ⭐⭐⭐ High | Ngắn hạn |
| InfluxDB migration | ⭐⭐⭐ Cao | ⭐⭐⭐ High | Trung hạn |
| MQTT retain/QoS1 | ⭐⭐ Med | ⭐ Low | Ngắn hạn |
| BACnet TrendLog reader | ⭐⭐⭐ Cao | ⭐⭐ Med | Trung hạn |
| BACnet Alarms | ⭐⭐⭐ Cao | ⭐⭐⭐ High | Dài hạn |
| HTTPS/TLS | ⭐⭐⭐ Cao | ⭐⭐ Med | Ngắn hạn |
| Grafana integration | ⭐⭐ Med | ⭐⭐ Med | Dài hạn |
