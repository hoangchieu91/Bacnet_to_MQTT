# BACnet Gateway — Thông Tin Hệ Thống

> **Cập nhật lần cuối:** 2026-03-16

---

## 1. Ubuntu Server (bacnet-monitor)

| Thông số | Giá trị |
| :--- | :--- |
| **Hostname** | `bacnet-monitor` |
| **OS** | Ubuntu 24.04.4 LTS |
| **CPU** | Intel Core i7-6700 @ 3.40GHz |
| **RAM** | 1.9 GB |
| **Disk** | 29 GB (24% đã dùng) |
| **Python** | 3.12.3 |
| **Docker** | ❌ Đã gỡ (2026-03-16) |
| **Thư mục dự án** | `/home/user/bacnet_mqtt_gateway` |

### SSH Access
```bash
ssh user@100.74.25.27   # Qua Tailscale VPN (khuyến nghị)
ssh user@10.212.154.3   # Qua OpenVPN
ssh user@172.20.24.175  # Qua LAN nội bộ
# Password: Admin@12345
```

---

## 2. Raspberry Pi 5

| Thông số | Giá trị |
| :--- | :--- |
| **Hostname** | `Raspberry-Pi5` |
| **IP LAN (wlan0)**| `10.25.7.21` |
| **Gateway** | `10.25.7.91` |
| **DNS** | `10.25.7.200` |
| **OS** | Debian 13 (Trixie) |
| **RAM** | 4 GB |
| **Disk** | 117 GB (7% đã dùng) |
| **Python** | 3.13.5 |

### SSH Access
```bash
ssh pi@10.25.7.21
# Password: Raspberry
```

### Dịch Vụ Đang Chạy
- **`bacnet-gateway.service`**: Gateway chính.
- **`mstp-tools.service`**: Công cụ MS/TP.
- **`modbus-rtu-tools.service`**: 🟢 **Dịch vụ Modbus RTU của bạn đang chạy ở đây.**

### Khả năng & Vai Trò (Capabilities)
- **Hiệu năng cao**: Vi xử lý mạnh và RAM 4GB cho phép Pi 5 xử lý lượng dữ liệu lớn, đóng vai trò như một Sub-master Gateway hoặc thậm chí thay thế Ubuntu Server cho các site nhỏ/vừa.
- **Modbus RTU Master**: Đang gánh vác việc đọc dữ liệu Modbus RTU qua cổng Serial ổn định.
- **Quản lý đa mạng MS/TP**: Khả năng đọc đồng thời nhiều chuỗi RS485 (qua USB và GPIO) độ trễ thấp, phục vụ số lượng thiết bị > 50 nút.
- **Đa nhiệm**: Dư sức chạy thêm Mosquitto broker (MQTT) hoặc lưu trữ DB (với thẻ nhớ tốc độ cao/SSD) mà không sợ OOM (Out Of Memory).

---

## 3. Raspberry Pi 3

| Thông số | Giá trị |
| :--- | :--- |
| **Hostname** | `BMS-BACKBACKDOOR` |
| **IP LAN** | `10.25.7.22` |
| **IP OpenVPN** | `10.212.154.36` |
| **OS** | Debian 12 (Bookworm) |
| **Cấu hình** | Raspberry Pi 3 Model B, 1GB RAM |
| **Python** | 3.11.2 (venv) |

### SSH Access
```bash
ssh admin@10.25.7.22
# Password: Admin@12345
```

### Trạng thái & Dịch vụ
- **`bacnet-gateway.service`**: ✅ **Đã cài đặt và đang chạy (active).**
- **Web UI**: `http://10.25.7.22:8000`
- **MQTT**: Đã trỏ về broker trung tâm (`nxchieu.duckdns.org:54883`) với prefix `bms/pi3_gw`.
- ⚠️ **Cổng Serial**: Hiện tại chưa cấu hình RS485 (cần thiết bị phần cứng).

### Khả năng & Giới hạn (Capabilities & Limitations)
- **Khả năng (Phù hợp làm gì)**: Rất tốt cho vai trò **Slave/Bridge RS485 từ xa** (chuyển đổi BACnet MS/TP riêng lẻ hoặc Modbus RTU nhỏ gọn rồi đẩy nhanh lên MQTT Ubuntu/Pi 5).
- **Giới hạn phần cứng**: Do RAM chỉ 1GB và CPU ARM Cortex-A53 thế hệ cũ, máy sẽ bị quá tải (OOM) nếu load database nội dung lớn, xử lý giao diện React UI liên tục cho nhiều user, hoặc map > 20 thiết bị MS/TP.
- **Đề xuất**: Chỉ nên dùng để chạy `mstp-bridge` hoặc daemon Python nhẹ (`bacnet-gateway` trỏ MQTT ra xa). Hạn chế lưu DB trực tiếp trên Pi 3 để bảo vệ thẻ nhớ và RAM.

---

## 4. Kiến Trúc MQTT Trung Tâm (Mới)
Tất cả các node đẩy dữ liệu về broker dùng chung tại **`mqtt://nxchieu.duckdns.org:54883`** để tránh xung đột. Mỗi Node có một Prefix riêng.

| Node | Topic Prefix | Mục Đích |
| --- | --- | --- |
| Ubuntu Server | `bms/ubuntu_gw` | Gateway tổng hợp dữ liệu |
| Raspberry Pi 5 | `bms/pi5_gw` | MS/TP & Modbus RTU Data |
| Raspberry Pi 3 | `bms/pi3_gw` | Tủ điều khiển/Slave từ xa |
| Local Machine | `bms/local_dev` | Dev / Testing Control |

### Cấu trúc Bản tin (Ví dụ Node Pi 5)
```text
# LWT / Gateway Status
bms/pi5_gw/status => {"online": true}

# Device Address Status (Khi BAC0 Ping thấy thiết bị online)
bms/pi5_gw/device/{device_id}/status => {"online": true, "address": "192.168.1.5"}

# Trạng thái điểm phân tán (Telemetry)
bms/pi5_gw/device/{device_id}/{object_type}/{object_instance}/value => {"value": 23.5, "alarm_state": "normal"}

# Gửi lệnh điều khiển xuống tủ (Command)
bms/pi5_gw/cmd/device/{device_id}/{object_type}/{object_instance}/write => {"value": 50.0, "priority": 14}
```

> **Cách dùng trên máy Local**: Nếu muốn xem toàn bộ hệ thống, máy local chỉ cần Subscribe vào wildcard `bms/#`. Nếu muốn gửi lệnh ghi giá trị xuống Pi 5, Publish lệnh Command tới `bms/pi5_gw/cmd/device/...`.

---

## 4. Troubleshooting
- **Web UI**: Luôn sử dụng port **8000** (`http://100.74.25.27:8000`).
- **Modbus RTU**: Dịch vụ đang nằm ở Pi 5. Nếu cần dùng Pi 3, hãy cấu hình serial port và di chuyển script.
