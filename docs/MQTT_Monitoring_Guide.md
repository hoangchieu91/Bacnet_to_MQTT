# Hướng Dẫn Giám Sát Hệ Thống BACnet Qua MQTT Từ Xa

> **Cập nhật:** 2026-03-17  
> **Broker:** `nxchieu.duckdns.org:54883`  
> **Wildcard toàn hệ thống:** `bms/#`

---

## 1. Tổng Quan Kiến Trúc

```
┌─────────────────────────────────────────────────────────────┐
│                  MQTT Broker Trung Tâm                      │
│              nxchieu.duckdns.org:54883                       │
│                                                              │
│   bms/ubuntu_gw/...    ← Ubuntu Server (758 thiết bị)       │
│   bms/pi5_gw/...       ← Raspberry Pi 5 (MS/TP + Modbus)   │
│   bms/pi3_gw/...       ← Raspberry Pi 3 (Dự phòng)         │
│   bms/local_dev/...    ← Máy trạm phát triển               │
└──────────────────────────┬──────────────────────────────────┘
                           │
              Subscribe bms/# → Nhận TẤT CẢ dữ liệu
```

Mỗi Gateway Node kết nối vào Broker chung bằng **client_id riêng** (`gw_ubuntu`, `gw_pi5`, `gw_pi3`) để tránh xung đột. Hệ thống sử dụng **LWT** (Last Will and Testament) để phát hiện node mất kết nối đột ngột.

---

## 2. Cấu Trúc Topic Chi Tiết

### 2.1 Gateway Status (LWT — Sống/Chết của Gateway)

| Topic | Ý nghĩa |
| :--- | :--- |
| `bms/{node}/status` | Trạng thái gateway: online/offline |

**Payload:**
```json
// Khi gateway khởi động thành công (Retain = true)
{"online": true}

// Khi gateway mất kết nối đột ngột — Broker TỰ ĐỘNG gửi (LWT)
{"online": false}
```

**Ví dụ thực tế:**
```
bms/ubuntu_gw/status   → {"online": true}
bms/pi5_gw/status      → {"online": true}
bms/pi3_gw/status      → {"online": false}   ← Pi 3 đang offline!
```

> 💡 **Mẹo:** Subscribe `bms/+/status` để chỉ nhận trạng thái sống/chết của tất cả gateway mà không bị lẫn với device status.

---

### 2.2 Device Status (Thiết Bị BACnet Online/Offline)

| Topic | Ý nghĩa |
| :--- | :--- |
| `bms/{node}/device/{device_id}/status` | Trạng thái thiết bị BACnet con |

**Payload:**
```json
// Thiết bị phản hồi Ping thành công
{"online": true, "address": "192.168.20.28"}

// Thiết bị không phản hồi sau 3 lần ping
{"online": false, "error": "timeout after 3 failures"}
```

**Ví dụ thực tế:**
```
bms/ubuntu_gw/device/8000/status  → {"online": true, "address": "192.168.20.28"}
bms/ubuntu_gw/device/8500/status  → {"online": true, "address": "192.168.20.73"}
bms/pi5_gw/device/703/status      → {"online": true, "address": "10.25.7.50"}
```

> 💡 Subscribe `bms/+/device/+/status` để theo dõi tất cả thiết bị trên mọi node.

---

### 2.3 Telemetry (Dữ Liệu Thực — Nhiệt Độ, Trạng Thái Quạt...)

| Topic | Ý nghĩa |
| :--- | :--- |
| `bms/{node}/device/{device_id}/{object_type}/{instance}/value` | Giá trị đọc từ thiết bị BACnet |

**Payload:**
```json
{
  "value": 23.5,
  "object_type": "analogValue",
  "object_instance": 1,
  "device_id": 8000,
  "timestamp": "2026-03-16T16:30:00.000Z",
  "source": "poll",
  "alarm_state": "normal"
}
```

- `source`: `"poll"` (đọc định kỳ), `"cov"` (Change of Value push), `"rpm"` (ReadPropertyMultiple batch)
- `alarm_state`: `"normal"`, `"high-limit"`, `"low-limit"`, `"fault"`

**Ví dụ:**
```
bms/ubuntu_gw/device/8000/analogValue/1/value
bms/pi5_gw/device/703/binaryOutput/3/value
```

---

### 2.4 Priority Array (Bảng Ưu Tiên BACnet)

| Topic | Ý nghĩa |
| :--- | :--- |
| `bms/{node}/device/{device_id}/{object_type}/{instance}/priority_array` | 16 mức ưu tiên BACnet |

**Payload:**
```json
{
  "present_value": 22.0,
  "priority_array": {
    "1": null, "2": null, "..": null,
    "8": 22.0,
    "14": 24.0,
    "16": 25.0
  },
  "device_id": 8000,
  "timestamp": "2026-03-16T16:30:00.000Z"
}
```

---

### 2.5 Command (Điều Khiển Thiết Bị Từ Xa)

| Topic | Ý nghĩa |
| :--- | :--- |
| `bms/{node}/cmd/device/{device_id}/{object_type}/{instance}/write` | Ghi giá trị xuống thiết bị |
| `bms/{node}/cmd/device/{device_id}/{object_type}/{instance}/release` | Giải phóng ưu tiên |
| `bms/{node}/cmd/add_point` | Thêm điểm đo mới |
| `bms/{node}/cmd/remove_point` | Xóa điểm đo |
| `bms/{node}/cmd/list_points` | Liệt kê tất cả điểm đo |

**Ghi giá trị:**
```json
// Publish tới: bms/pi5_gw/cmd/device/703/analogValue/1/write
{"value": 22.0, "priority": 14}
```

**Giải phóng 1 mức ưu tiên:**
```json
// Publish tới: bms/pi5_gw/cmd/device/703/analogValue/1/release
{"priority": 14}
```

**Giải phóng tất cả ưu tiên:**
```json
{"priority": "all"}
```

**Phản hồi lệnh** (Gateway tự gửi):
```
bms/pi5_gw/response/write → {"success": true, "message": "Write OK: analogValue:1 = 22.0 @priority 14"}
```

---

## 3. Bảng Tổng Hợp Topic Pattern

| Mục đích | Subscribe Pattern | Ví dụ |
| :--- | :--- | :--- |
| **Mọi thứ** | `bms/#` | Tất cả dữ liệu toàn hệ thống |
| **Gateway sống/chết** | `bms/+/status` | `bms/ubuntu_gw/status` |
| **Thiết bị sống/chết** | `bms/+/device/+/status` | `bms/ubuntu_gw/device/8000/status` |
| **Dữ liệu 1 node** | `bms/ubuntu_gw/#` | Mọi thứ từ Ubuntu Server |
| **Dữ liệu 1 thiết bị** | `bms/ubuntu_gw/device/8000/#` | Tất cả data của device 8000 |
| **Tất cả telemetry** | `bms/+/device/+/+/+/value` | Chỉ giá trị, không status |
| **Phản hồi lệnh** | `bms/+/response/#` | Kết quả write/release |

---

## 4. Hướng Dẫn Giám Sát Thực Tế

### 4.1 Dùng MQTT Explorer (GUI — Đề xuất cho người mới)

1. Tải **MQTT Explorer** tại: https://mqtt-explorer.com
2. Nhập thông tin kết nối:
   - **Host:** `nxchieu.duckdns.org`
   - **Port:** `54883`
   - **Username / Password:** *(để trống)*
3. Nhấn **Connect** → Toàn bộ cây topic `bms/` sẽ hiện ra dạng tree.

---

### 4.2 Dùng `mosquitto_sub` (CLI — Nhanh gọn)

```bash
# Cài đặt
sudo apt install -y mosquitto-clients

# Xem TẤT CẢ bản tin
mosquitto_sub -h nxchieu.duckdns.org -p 54883 -t "bms/#" -v

# Chỉ xem trạng thái gateway
mosquitto_sub -h nxchieu.duckdns.org -p 54883 -t "bms/+/status" -v

# Chỉ xem trạng thái thiết bị
mosquitto_sub -h nxchieu.duckdns.org -p 54883 -t "bms/+/device/+/status" -v

# Xem dữ liệu 1 thiết bị cụ thể
mosquitto_sub -h nxchieu.duckdns.org -p 54883 -t "bms/ubuntu_gw/device/8000/#" -v
```

---

### 4.3 Python Monitoring Script

Script dưới đây kết nối vào broker và in ra mọi bản tin real-time, phân loại theo từng nhánh topic:

```python
#!/usr/bin/env python3
"""mqtt_monitor.py — Giám sát toàn hệ thống BACnet Gateway qua MQTT."""

import json
import paho.mqtt.client as mqtt

BROKER = "nxchieu.duckdns.org"
PORT = 54883

def on_connect(client, userdata, flags, rc, properties=None):
    if hasattr(rc, "value"):
        rc = rc.value
    if rc == 0:
        print(f"✅ Kết nối broker {BROKER}:{PORT} thành công!")
        client.subscribe("bms/#", qos=0)
        print("📡 Đang lắng nghe bms/# ...")
    else:
        print(f"❌ Kết nối thất bại (rc={rc})")

def on_message(client, userdata, msg):
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = msg.payload.decode(errors="replace")

    parts = topic.split("/")

    # Phân loại bản tin
    if topic.endswith("/status") and "device" not in topic:
        # Gateway Status (LWT)
        node = parts[1]
        state = "🟢 ONLINE" if payload.get("online") else "🔴 OFFLINE"
        print(f"[GATEWAY] {node}: {state}")

    elif "/device/" in topic and topic.endswith("/status"):
        # Device Status
        node = parts[1]
        device_id = parts[3]
        state = "🟢" if payload.get("online") else "🔴"
        addr = payload.get("address", "?")
        print(f"[DEVICE]  {node}/{device_id}: {state} (addr={addr})")

    elif topic.endswith("/value"):
        # Telemetry
        node = parts[1]
        device_id = parts[3]
        obj_type = parts[4]
        instance = parts[5]
        value = payload.get("value", payload)
        alarm = payload.get("alarm_state", "")
        alarm_flag = " ⚠️" if alarm and alarm != "normal" else ""
        print(f"[DATA]    {node}/{device_id}/{obj_type}:{instance} = {value}{alarm_flag}")

    elif "/response/" in topic:
        # Command Response
        ok = "✅" if payload.get("success") else "❌"
        print(f"[CMD]     {ok} {payload.get('message', '')}")

    else:
        print(f"[OTHER]   {topic}: {payload}")

def main():
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="central_monitor",
    )
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(BROKER, PORT, keepalive=60)

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n🛑 Dừng giám sát.")
        client.disconnect()

if __name__ == "__main__":
    main()
```

**Chạy:**
```bash
# Từ máy bất kỳ có Python + paho-mqtt
pip install paho-mqtt
python3 mqtt_monitor.py
```

**Output mẫu:**
```
✅ Kết nối broker nxchieu.duckdns.org:54883 thành công!
📡 Đang lắng nghe bms/# ...
[GATEWAY] ubuntu_gw: 🟢 ONLINE
[GATEWAY] pi5_gw: 🟢 ONLINE
[GATEWAY] pi3_gw: 🔴 OFFLINE
[DEVICE]  ubuntu_gw/8000: 🟢 (addr=192.168.20.28)
[DEVICE]  ubuntu_gw/8500: 🟢 (addr=192.168.20.73)
[DATA]    ubuntu_gw/8000/analogValue:1 = 23.5
[DATA]    pi5_gw/703/binaryOutput:3 = active
[CMD]     ✅ Write OK: analogValue:1 = 22.0 @priority 14
```

---

### 4.4 Gửi Lệnh Điều Khiển Từ Xa

```bash
# Ghi nhiệt độ 22°C xuống thiết bị 703 trên Pi 5 (priority 14)
mosquitto_pub -h nxchieu.duckdns.org -p 54883 \
  -t "bms/pi5_gw/cmd/device/703/analogValue/1/write" \
  -m '{"value": 22.0, "priority": 14}'

# Giải phóng tất cả ưu tiên
mosquitto_pub -h nxchieu.duckdns.org -p 54883 \
  -t "bms/pi5_gw/cmd/device/703/analogValue/1/release" \
  -m '{"priority": "all"}'

# Xem kết quả
mosquitto_sub -h nxchieu.duckdns.org -p 54883 -t "bms/pi5_gw/response/#" -v
```

---

## 5. Cấu Hình Các Node

| Node | Client ID | Topic Prefix | Config File |
| :--- | :--- | :--- | :--- |
| Ubuntu Server | `gw_ubuntu` | `bms/ubuntu_gw` | `/home/user/bacnet_mqtt_gateway/config/runtime_config.json` |
| Raspberry Pi 5 | `gw_pi5` | `bms/pi5_gw` | `/home/pi/bacnet_mqtt_gateway/config/runtime_config.json` |
| Raspberry Pi 3 | `gw_pi3` | `bms/pi3_gw` | `/home/admin/bacnet_mqtt_gateway/config/runtime_config.json` |
| Local Machine | `gw_local` | `bms/local_dev` | `./config/runtime_config.json` |

**Trường cấu hình quan trọng** (trong `runtime_config.json` → `mqtt`):
```json
{
  "mqtt": {
    "broker_host": "nxchieu.duckdns.org",
    "broker_port": 54883,
    "client_id": "gw_ubuntu",
    "topic_prefix": "bms/ubuntu_gw",
    "qos": 1,
    "retain": false
  }
}
```

> ⚠️ **Quan trọng:** Mỗi node PHẢI có `client_id` khác nhau! Nếu 2 node trùng `client_id`, broker sẽ đá node cũ ra liên tục.

---

## 6. Xử Lý Sự Cố MQTT

| Triệu chứng | Nguyên nhân | Giải pháp |
| :--- | :--- | :--- |
| Không thấy bản tin nào | `broker_host` vẫn là `localhost` | Sửa thành `nxchieu.duckdns.org` |
| Connect rồi disconnect liên tục | Trùng `client_id` giữa các node | Đổi `client_id` riêng cho mỗi node |
| Bản tin không đúng prefix `bms/` | Code cũ dùng `mqtt_topic` override | Đảm bảo code mới (v2.0) đã deploy |
| `Connection refused` | Broker chưa chạy hoặc sai port | Kiểm tra `54883` và firewall |
| Bản tin cũ vẫn hiện trên broker | Retained messages từ cấu hình cũ | Publish payload rỗng với retain=true |

---

*Tài liệu này áp dụng cho BACnet-MQTT Gateway v2.0+ (2026-03-17).*
