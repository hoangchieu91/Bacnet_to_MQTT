# BACnet Technical Guide — Gateway V2

> Tài liệu kỹ thuật về giao thức BACnet và cách module gateway áp dụng.  
> Dùng làm tham chiếu cho các dự án đọc BACnet khác.

---

## 1. Tổng quan BACnet

**BACnet** (Building Automation and Control Networks) là chuẩn ISO 16484-5 cho hệ thống tự động hóa tòa nhà.

### 1.1 Các lớp giao tiếp

```
┌─────────────────────────────────────────────────────────────┐
│  Application Layer   (Services: ReadProperty, Who-Is...)    │
├─────────────────────────────────────────────────────────────┤
│  Network Layer       (BACnet Routing, BBMD)                 │
├─────────────────────────────────────────────────────────────┤
│  Data Link / Physical                                       │
│   ├── BACnet/IP (Ethernet/WiFi — UDP port 47808)           │
│   ├── BACnet MS/TP (RS-485 — EIA-485, 9600–76800 baud)     │
│   ├── BACnet Ethernet (802.3)                               │
│   └── BACnet ARCnet, ZigBee, LonTalk...                    │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 BACnet/IP vs MS/TP

| Đặc điểm | BACnet/IP | MS/TP (RS-485) |
|-----------|-----------|----------------|
| Tốc độ | 10/100 Mbps | 9600–76800 bps |
| Topology | Star/Bus LAN | Bus (daisy chain) |
| Địa chỉ | IP:Port | MAC 0–127 |
| Khoảng cách | Qua switch/router | Tối đa ~1200m |
| Thiết bị | IP controllers | Legacy controllers |
| Ví dụ dự án | CPO-RL4, CPO-PC-6A | FCU, VAV controllers |

---

## 2. Object Model BACnet

Mỗi thiết bị BACnet chứa một tập **Objects**, mỗi object có nhiều **Properties**.

### 2.1 Object Types thông dụng

| Object Type | Viết tắt | Mô tả | Read/Write |
|-------------|----------|-------|-----------|
| `analogInput` | AI | Cảm biến analog (nhiệt độ, độ ẩm...) | R |
| `analogOutput` | AO | Ngõ ra analog (van, tốc độ quạt...) | R/W |
| `analogValue` | AV | Biến analog nội bộ (setpoint...) | R/W |
| `binaryInput` | BI | Cảm biến số (công tắc, trạng thái) | R |
| `binaryOutput` | BO | Ngõ ra số (relay, coil) | R/W |
| `binaryValue` | BV | Biến số nội bộ | R/W |
| `multiStateInput` | MSI | Ngõ vào đa trạng thái (mode selector) | R |
| `multiStateOutput` | MSO | Ngõ ra đa trạng thái | R/W |
| `multiStateValue` | MSV | Biến đa trạng thái (operating mode) | R/W |

### 2.2 Properties quan trọng

```
Object: analogInput, instance 1201
├── objectIdentifier   : (analogInput, 1201)
├── objectName         : "Zone-Temp-Room101"
├── presentValue       : 23.5   ← giá trị hiện tại
├── units              : degreesCelsius
├── description        : "Zone temperature sensor"
├── statusFlags        : [false, false, false, false]  ← [inAlarm, fault, overridden, outOfService]
├── outOfService       : false
└── eventState         : normal
```

```
Object: multiStateValue, instance 1301
├── presentValue       : 2      ← chỉ số trạng thái (1-based)
├── numberOfStates     : 4
├── stateText          : ["Auto", "Manual", "Off", "Emergency"]
└── ...
```

### 2.3 Priority Array (BACnet priority writing)

BACnet sử dụng **16 mức ưu tiên** cho write operations:

| Priority | Ý nghĩa |
|----------|---------|
| 1 | Manual-Life Safety |
| 2 | Automatic-Life Safety |
| 6 | Minimum On/Off |
| 8 | Manual Operator (khuyến nghị cho debug/vận hành tay) |
| 14 | Operator Override (mặc định gateway này) |
| 16 | Programmatic (thấp nhất — không override controller) |

> **Lưu ý:** Priority 1-7 có thể khóa controller không chạy auto. Luôn dùng priority ≥ 8 cho hệ thống SCADA/gateway.

---

## 3. BACnet Services

### 3.1 Discovery

```
WHO-IS (broadcast)  → BACnet network
I-AM   (unicast)    ← Mỗi device trả về: deviceId, address, maxAPDU, segmentation
```

```python
# Ví dụ BAC0
network = BAC0.lite(ip='192.168.1.100/24')
await asyncio.sleep(5)  # Đợi WHO-IS responses
devices = network.discoveredDevices
```

### 3.2 Read Property

```
ReadPropertyRequest:
  objectIdentifier = analogInput, 1
  propertyIdentifier = presentValue

ReadPropertyACK:
  presentValue = 23.5
```

### 3.3 ReadPropertyMultiple (RPM)

Đọc nhiều properties trong 1 gói tin — **hiệu quả hơn nhiều** so với đọc từng cái:

```python
# BAC0 RPM
result = await network.readMultiple(
    "192.168.1.x analogInput 1 presentValue units statusFlags"
)
```

### 3.4 WriteProperty

```python
await network.write(
    "192.168.1.x analogOutput 1 presentValue 50.0 - 14"
    # address    type     instance  property    value  priority
)
```

### 3.5 Subscribe COV (Change of Value)

```
SubscribeCOVRequest:
  subscriberProcessIdentifier = 101
  monitoredObjectIdentifier   = (binaryOutput, 3)
  issueConfirmedNotifications = false
  lifetime                    = 300  ← giây

← UnconfirmedCOVNotification (khi value thay đổi):
  subscriberProcessIdentifier = 101
  initiatingDeviceIdentifier  = 12345
  monitoredObjectIdentifier   = (binaryOutput, 3)
  listOfValues:
    presentValue = active
    statusFlags  = [false, false, false, false]
```

**Giới hạn thực tế:**
- Mỗi point COV = 1 subscription trong `active-cov-subscriptions` của device
- Device thường chấp nhận 20–200 subscriptions
- Lifetime hết hạn → phải re-subscribe (gateway này auto-renew ở 270s)

---

## 4. Cấu hình mạng cho BACnet/IP

### 4.1 BBMD (BACnet Broadcast Management Device)

Khi gateway và device ở **khác subnet**, cần BBMD để forward broadcast:

```
Subnet A (192.168.1.x)         Subnet B (192.168.20.x)
Gateway ──── BBMD-A ════════ BBMD-B ──── CPO device
             (forward WHO-IS)
```

Hoặc dùng **Foreign Device Registration**:
```python
BAC0.lite(ip='192.168.1.100/24', bbmdAddress='192.168.20.1', bbmdTTL=900)
```

### 4.2 Route-Aware Mode

Khi mạng có nhiều BACnet networks (MS/TP qua BACnet router):

```python
BAC0.lite(ip='192.168.1.100/24', route_aware=True)
```

Gateway này dùng `route_aware=True` khi phát hiện địa chỉ dạng `"5:20"` (network:mac).

### 4.3 Broadcast Response Misrouting (Bug đã fix)

**Vấn đề:** Một số device trả I-AM về **broadcast address** thay vì unicast → BAC0 bỏ qua.

**Fix:** Monkey-patch `BIPSimpleApplication._process_apdu` để accept cả packet từ broadcast address.

Xem chi tiết: `backend/bacnet_listener.py` → `_patch_broadcast_response`.

---

## 5. Thư viện: BAC0 + BACpypes3

Dự án dùng **BAC0** (wrapper) on top of **BACpypes3** (thư viện BACnet Python cầu thấp):

```
BAC0 (high-level API)
  └── BACpypes3 (protocol implementation)
        ├── Application layer
        ├── Network layer  
        └── UDP/IP transport
```

### 5.1 BAC0 Lite vs Full

| | BAC0.lite | BAC0.connect |
|--|-----------|-------------|
| Discovery | ✅ | ✅ |
| Read/Write | ✅ | ✅ |
| COV | ✅ | ✅ |
| Trending | ❌ | ✅ |
| Memory | Nhẹ | Nặng hơn |

Gateway dùng `BAC0.lite`.

### 5.2 Các API quan trọng

```python
# Khởi động
network = await BAC0.lite(ip='10.0.0.1/24', port=47808)

# Đọc
val = await network.read("address objectType instance property")

# Đọc multiple  
val = await network.readMultiple("address objectType instance prop1 prop2 prop3")

# Ghi (với priority)
await network.write("address objectType instance property value - priority")

# COV subscription
async with app.change_of_value(address, objectId, processId, confirmed, lifetime) as scm:
    prop_id, prop_val = await scm.get_value()

# Discovery
await network.discover(global_broadcast=True, timeout=10)
devices = network.discoveredDevices
```

---

## 6. Troubleshooting BACnet

### Không discover được device

1. Kiểm tra cùng subnet / BBMD config
2. Firewall: mở UDP port 47808 (inbound + outbound)
3. IP conflict: chạy 2 BAC0 instance cùng lúc → lỗi `[Errno 98] Address already in use`
4. Tailscale/VPN: có thể chặn broadcast → fix bằng `ip route` hoặc route specific

### ReadProperty timeout

```
asyncio.wait_for() sẽ corrupt BAC0 state → KHÔNG DÙNG
Thay bằng: BAC0 tự handle timeout nội bộ
```

Gateway này đã remove `asyncio.wait_for()` — xem `bacnet_service.py`.

### MS/TP device không trả lời

- Kiểm tra baud rate (thường 9600 hoặc 38400)
- MAC address collision (mỗi device phải unique 0–127)
- Địa chỉ format: `"5:20"` = network 5, MAC 20

---

## 7. CSV Format cho Mappings

File CSV export từ trang Mappings:

```csv
id,label,device_id,object_type,object_instance,mqtt_topic,read_mode,poll_interval,group,enabled,units,description,active_text,inactive_text
abc123,Zone-Temp,10121,analogValue,1201,bacnet/zone/temp,poll,10,,true,degreesCelsius,Zone temperature,,,
def456,Fan-Status,10121,binaryOutput,3,bacnet/fan/status,cov,10,FCU_Group,true,,,Running,Stopped
```

**Quan trọng khi chỉnh sửa CSV:**
- `read_mode`: `poll` hoặc `cov`
- `enabled`: `true` / `false`
- `poll_interval`: giây (integer)
- `object_instance`: integer
- `device_id`: integer (deviceInstance của BACnet device)
- Nếu trường `id` để trống khi import → backend tạo ID mới
