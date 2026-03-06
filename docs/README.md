# BACnet-MQTT Gateway

Hệ thống chuyển đổi giao thức BACnet/IP sang MQTT với giao diện web giám sát và điều khiển theo thời gian thực.

## Mục Lục

1. [Tổng Quan](#tổng-quan)
2. [Kiến Trúc Hệ Thống](#kiến-trúc-hệ-thống)
3. [Tính Năng Chi Tiết](#tính-năng-chi-tiết)
4. [Cấu Trúc MQTT Topics](#cấu-trúc-mqtt-topics)
5. [REST API Reference](#rest-api-reference)
6. [Giao Diện Web](#giao-diện-web)
7. [Cài Đặt & Triển Khai](#cài-đặt--triển-khai)
8. [Năng Lực Raspberry Pi](#năng-lực-raspberry-pi)
9. [Cấu Hình](#cấu-hình)

---

## Tổng Quan

BACnet-MQTT Gateway là cầu nối giữa thiết bị tự động hóa tòa nhà (BACnet/IP) và hệ thống IoT (MQTT). Gateway được thiết kế chạy trên Raspberry Pi, đặt tại hiện trường, kết nối trực tiếp với mạng BACnet qua Ethernet.

**Công nghệ sử dụng:**
- **Python 3.11+** — Ngôn ngữ chính
- **BAC0** — Thư viện BACnet/IP (dựa trên BACpypes3)
- **paho-mqtt 2.x** — MQTT client
- **FastAPI** — Web framework + REST API
- **WebSocket** — Real-time data streaming
- **Chart.js** — Biểu đồ trực quan

---

## Kiến Trúc Hệ Thống

```
┌──────────────┐     UDP/47808      ┌──────────────────────────────────────┐     TCP/1883     ┌──────────────┐
│  BACnet/IP   │◄──────────────────►│         Raspberry Pi Gateway         │◄───────────────►│  MQTT Broker │
│  Devices     │   BACnet Protocol  │                                      │   MQTT Protocol  │  (Mosquitto) │
│  (AHU, FCU,  │                    │  ┌──────────┐  ┌───────────────────┐ │                  │              │
│   VAV, DDC)  │                    │  │ BACnet   │  │  Gateway Engine   │ │                  └──────┬───────┘
└──────────────┘                    │  │ Service  │──│  (Polling+Cmd)    │ │                         │
                                    │  └──────────┘  └───────────────────┘ │                         │
                                    │  ┌──────────┐  ┌───────────────────┐ │                  ┌──────┴───────┐
                                    │  │ MQTT     │  │ FastAPI + WS      │ │◄────────────────►│   Clients    │
                                    │  │ Service  │  │ (Web UI + API)    │ │   HTTP/WS:8080   │  (Browser,   │
                                    │  └──────────┘  └───────────────────┘ │                  │   SCADA,     │
                                    └──────────────────────────────────────┘                  │   Node-RED)  │
                                                                                             └──────────────┘
```

---

## Tính Năng Chi Tiết

### 1. Khám Phá Thiết Bị BACnet (Device Discovery)

Gateway phát gói WHO-IS broadcast trên mạng BACnet/IP để tự động phát hiện tất cả thiết bị BACnet trong cùng subnet.

**Thông tin thu thập:**
- Device ID (số định danh duy nhất)
- Device Name (tên thiết bị)
- IP Address (địa chỉ mạng)
- Object List (danh sách các đối tượng trong thiết bị)

**Cách sử dụng:**
1. Mở trang **Devices** trên web UI
2. Nhấn **Discover Devices** — gateway sẽ quét mạng
3. Các thiết bị phát hiện được hiển thị dạng card
4. Nhấn vào card để xem danh sách Object của thiết bị
5. Nhấn **+ Add** để thêm object vào mapping (giám sát)

---

### 2. Đọc Giá Trị và Priority Array (Read Value + Priority Array)

Mỗi điểm (point) được cấu hình sẽ được đọc định kỳ. Gateway đọc:
- **presentValue** — Giá trị hiện tại đang có hiệu lực
- **priorityArray** — Mảng 16 mức ưu tiên BACnet

#### BACnet Priority Array (16 mức)

BACnet sử dụng hệ thống 16 mức ưu tiên để quyết định giá trị nào sẽ có hiệu lực trên thiết bị:

| Mức | Tên | Mô tả |
|-----|-----|-------|
| 1 | Manual Life Safety | An toàn sinh mạng — thủ công |
| 2 | Automatic Life Safety | An toàn sinh mạng — tự động |
| 3 | Available | Dự phòng |
| 4 | Available | Dự phòng |
| 5 | Critical Equipment | Thiết bị quan trọng |
| 6 | Minimum On/Off | Bật/Tắt tối thiểu |
| 7 | Available | Dự phòng |
| **8** | **Manual Operator** | **Vận hành thủ công (thường dùng nhất)** |
| 9–15 | Available | Dự phòng |
| **16** | **Default** | **Giá trị mặc định (schedule, BMS)** |

> **Quy tắc:** Mức ưu tiên có số **thấp hơn** sẽ **thắng**. Ví dụ: nếu mức 8 = 72°F và mức 16 = 70°F, giá trị hiệu lực sẽ là 72°F.

**Trên Web UI:**
- Cột **Priority Array** hiển thị 16 ô mini-grid, ô sáng = có giá trị, ô tối = null
- Click vào grid → mở modal chi tiết hiển thị giá trị từng mức

---

### 3. Ghi Giá Trị theo Mức Ưu Tiên (Write at Priority)

Gateway hỗ trợ ghi giá trị vào thiết bị BACnet tại mức ưu tiên 8–16.

**Qua Web UI:**
1. Mở Mappings → Click vào 🎚️ (Priority Array) của point
2. Trong modal, nhập **Value** và chọn **Priority (8–16)**
3. Nhấn **✍️ Write**

**Qua MQTT:**
```
Topic:   bacnet/cmd/write/<device_id>/<object_type>/<instance>
Payload: {"value": 72.5, "priority": 8}
```

**Qua REST API:**
```
POST /api/bacnet/write
Body:  {"device_id": 100, "object_type": "analogValue", "object_instance": 1, "value": 72.5, "priority": 8}
```

---

### 4. Nhả Mức Ưu Tiên (Release / Null)

Khi muốn bỏ giá trị đã ghi tại một mức ưu tiên, gateway ghi `null` vào mức đó. Thiết bị sẽ sử dụng giá trị của mức ưu tiên có số thấp nhất còn lại.

**Nhả một mức:**
```
Topic:   bacnet/cmd/release/<device_id>/<object_type>/<instance>
Payload: {"priority": 8}
```

**Nhả TẤT CẢ 16 mức:**
```
Topic:   bacnet/cmd/release/<device_id>/<object_type>/<instance>
Payload: {"priority": "all"}
```

**Trên Web UI:**
- Trong modal Priority Array, mỗi mức có nút 🔓 (Release)
- Nút **🔓 Release ALL (1–16)** ở cuối modal

---

### 5. Quản Lý Điểm Giám Sát qua MQTT (Dynamic Point Management)

Có thể thêm/xóa/liệt kê các điểm giám sát hoàn toàn qua MQTT mà không cần mở web UI.

#### Thêm điểm mới:
```
Topic:   bacnet/cmd/add_point
Payload: {
  "device_id": 100,
  "object_type": "analogValue",
  "object_instance": 1,
  "poll_interval": 10,
  "label": "Room Temperature"
}
```

#### Xóa điểm:
```
Topic:   bacnet/cmd/remove_point
Payload: {"device_id": 100, "object_type": "analogValue", "object_instance": 1}
```
Hoặc theo mapping ID:
```
Payload: {"mapping_id": "abc12345"}
```

#### Liệt kê tất cả điểm:
```
Topic:   bacnet/cmd/list_points
Payload: {}
```
→ Gateway trả lời trên `bacnet/response/list_points` với danh sách đầy đủ.

---

### 6. Dashboard Thời Gian Thực (Real-time Dashboard)

- **Biểu đồ Live Values** — Chart.js vẽ giá trị theo thời gian
- **Bảng Recent Updates** — Cập nhật mới nhất từ mỗi điểm
- **WebSocket** — Dữ liệu push real-time không cần refresh
- **Status Cards** — Trạng thái Gateway, BACnet, MQTT, số mapping hoạt động

---

### 7. Cấu Hình MQTT (MQTT Configuration)

- Cấu hình broker host, port, username/password, TLS
- Test connection trực tiếp từ giao diện
- Hiển thị trạng thái kết nối real-time
- Thay đổi topic prefix, QoS, retain

---

### 8. Quản Lý Hệ Thống (System Management)

- **Xuất cấu hình** — Export config dạng JSON
- **Nhập cấu hình** — Import config từ file JSON (backup/restore)
- **Xem logs** — Log viewer real-time trong trình duyệt

---

## Cấu Trúc MQTT Topics

Giả sử `topic_prefix` = `bacnet` (có thể cấu hình).

### Topics được Gateway publish (BACnet → MQTT)

| Topic | Mô tả | Payload mẫu |
|-------|--------|-------------|
| `bacnet/<dev>/<type>/<inst>/value` | Giá trị hiện tại | `{"value": 72.5, "device_id": 100, "timestamp": "..."}` |
| `bacnet/<dev>/<type>/<inst>/priority_array` | 16 mức ưu tiên | `{"present_value": 72.5, "priority_array": {"1": null, ..., "8": 72.5, ..., "16": 70.0}}` |
| `bacnet/response/<command>` | Phản hồi lệnh | `{"success": true, "message": "Write OK"}` |

### Topics lệnh (MQTT → Gateway)

| Topic | Payload | Chức năng |
|-------|---------|-----------|
| `bacnet/cmd/write/<dev>/<type>/<inst>` | `{"value": 72.5, "priority": 8}` | Ghi giá trị tại mức ưu tiên |
| `bacnet/cmd/release/<dev>/<type>/<inst>` | `{"priority": 8}` hoặc `{"priority": "all"}` | Nhả mức ưu tiên |
| `bacnet/cmd/add_point` | `{"device_id":100, "object_type":"analogValue", "object_instance":1, ...}` | Thêm điểm giám sát |
| `bacnet/cmd/remove_point` | `{"device_id":100, ...}` hoặc `{"mapping_id":"..."}` | Xóa điểm giám sát |
| `bacnet/cmd/list_points` | `{}` | Yêu cầu danh sách điểm |

---

## REST API Reference

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `GET` | `/api/status` | Trạng thái gateway, BACnet, MQTT |
| `POST` | `/api/gateway/start` | Khởi động gateway (bắt đầu poll) |
| `POST` | `/api/gateway/stop` | Dừng gateway |
| `POST` | `/api/bacnet/discover` | Quét thiết bị BACnet |
| `GET` | `/api/bacnet/devices` | Danh sách thiết bị đã phát hiện |
| `GET` | `/api/bacnet/devices/{id}/objects` | Danh sách object của thiết bị |
| `POST` | `/api/bacnet/write` | Ghi giá trị (body: `{device_id, object_type, object_instance, value, priority}`) |
| `POST` | `/api/bacnet/release` | Nhả ưu tiên (body: `{device_id, object_type, object_instance, priority}`) |
| `GET` | `/api/bacnet/priority_array/{dev}/{type}/{inst}` | Đọc priority array |
| `GET` | `/api/mqtt/config` | Lấy cấu hình MQTT |
| `PUT` | `/api/mqtt/config` | Cập nhật cấu hình MQTT |
| `POST` | `/api/mqtt/test` | Test kết nối broker |
| `GET` | `/api/mappings` | Danh sách mappings |
| `POST` | `/api/mappings` | Tạo mapping mới |
| `PUT` | `/api/mappings/{id}` | Cập nhật mapping |
| `DELETE` | `/api/mappings/{id}` | Xóa mapping |
| `GET` | `/api/config/export` | Xuất toàn bộ cấu hình |
| `POST` | `/api/config/import` | Nhập cấu hình |
| `GET` | `/api/logs` | Lấy log hệ thống |
| `WS` | `/ws` | WebSocket real-time data |

---

## Giao Diện Web

Gateway cung cấp giao diện web responsive, dark theme, truy cập tại `http://<pi_ip>:8080`.

### Các trang chính:

| Trang | Chức năng |
|-------|-----------|
| **Dashboard** | Biểu đồ real-time, bảng cập nhật mới, trạng thái hệ thống, Start/Stop gateway |
| **Devices** | Quét & hiển thị thiết bị BACnet, duyệt object list, thêm nhanh vào mapping |
| **Mappings** | Quản lý điểm giám sát, xem Priority Array 16 mức, ghi/nhả ưu tiên |
| **MQTT Config** | Cấu hình broker, test kết nối, xem trạng thái |
| **System** | Xem log hệ thống, xuất/nhập cấu hình |

---

## Cài Đặt & Triển Khai

### Yêu cầu phần cứng
- Raspberry Pi 3B+/4/5 (khuyến nghị Pi 3B+ trở lên)
- Ethernet kết nối trực tiếp vào mạng BACnet/IP
- VPN (WireGuard/OpenVPN) để truy cập từ xa

### Cài đặt nhanh

```bash
# 1. Clone/copy dự án vào Pi
scp -r Bacnet_MQTT/ pi@<pi_ip>:~/bacnet_mqtt_gateway/

# 2. Chạy script cài đặt
ssh pi@<pi_ip>
cd ~/bacnet_mqtt_gateway
chmod +x scripts/install.sh
./scripts/install.sh
```

### Quản lý service

```bash
# Xem trạng thái
sudo systemctl status bacnet-gateway

# Khởi động / dừng
sudo systemctl start bacnet-gateway
sudo systemctl stop bacnet-gateway

# Xem log real-time
journalctl -u bacnet-gateway -f

# Khởi động cùng hệ thống
sudo systemctl enable bacnet-gateway
```

### Cập nhật phần mềm

```bash
# Từ máy dev, sync code lên Pi
rsync -avz --exclude='venv' --exclude='__pycache__' \
  Bacnet_MQTT/ pi@<pi_ip>:~/bacnet_mqtt_gateway/

# Restart service
ssh pi@<pi_ip> 'sudo systemctl restart bacnet-gateway'
```

---

## Năng Lực Raspberry Pi

### Pi 3B+ (1GB RAM)

| Chỉ số | Giá trị khuyến nghị | Tối đa |
|--------|---------------------|--------|
| Số thiết bị BACnet | 5–10 | ~20 |
| Số điểm giám sát (points) | 50–100 | ~200 |
| Poll interval tối thiểu | 5 giây | 2 giây |
| RAM sử dụng (gateway) | ~120MB | ~250MB |
| Web UI clients đồng thời | 2–3 | 5 |

### Pi 5 (4GB RAM)

| Chỉ số | Giá trị khuyến nghị | Tối đa |
|--------|---------------------|--------|
| Số thiết bị BACnet | 20–50 | ~100 |
| Số điểm giám sát (points) | 200–500 | ~1000 |
| Poll interval tối thiểu | 2 giây | 1 giây |
| RAM sử dụng (gateway) | ~120MB | ~500MB |
| Web UI clients đồng thời | 5–10 | 20 |

### Lưu ý hiệu năng

- **Mỗi point read** mất ~50-200ms tùy mạng và thiết bị BACnet
- **priorityArray read** thêm ~100-300ms mỗi point
- Poll 100 points mỗi 10 giây → ~10 requests/giây → Pi 3 xử lý tốt
- **Web UI nhẹ** — HTML/JS static, CPU chỉ dùng cho API + WebSocket
- **MQTT publish** rất nhẹ (~0.5ms/message)
- **Khuyến nghị cho Pi 3**: 50–100 points, poll mỗi 10 giây, priority array mỗi 30 giây

---

## Cấu Hình

File cấu hình: `config/default_config.json` (mặc định) → `config/runtime_config.json` (runtime)

```json
{
  "mqtt": {
    "broker_host": "localhost",
    "broker_port": 1883,
    "username": "",
    "password": "",
    "use_tls": false,
    "client_id": "bacnet_mqtt_gateway",
    "topic_prefix": "bacnet",
    "qos": 1,
    "retain": false
  },
  "bacnet": {
    "ip": "0.0.0.0",
    "port": 47808,
    "device_id": 599,
    "default_poll_interval": 10
  },
  "web": {
    "host": "0.0.0.0",
    "port": 8080
  },
  "gateway": {
    "mappings": []
  }
}
```

### Cấu hình cho hiện trường

Khi deploy tại hiện trường, cần thay đổi:
- `bacnet.ip` → IP của NIC kết nối mạng BACnet (hoặc giữ `0.0.0.0` nếu chỉ có 1 NIC)
- `mqtt.broker_host` → IP/domain của MQTT broker server
- `mqtt.username` / `mqtt.password` → Thông tin xác thực broker
