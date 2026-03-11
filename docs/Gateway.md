# BACnet Gateway — Ubuntu Server

## SSH Access
- **Tailscale IP:** 10.212.154.2
- **Network Interface Configuration:**
  - `eth0` (WAN/Main network): `10.25.7.155` / `24`
  - `eth1` (BMS network): `192.168.20.250` / `24`
  - `tun0` (OpenVPN): `10.8.0.x`
  - `tailscale0` (Tailscale VPN)

---

## 2. Dịch Vụ Gateway (Website Control)
- **URL:** http://10.212.154.2:8080
- **Chức năng:** Quét, giám sát và cấu hình điều khiển BACnet Devices, MQTT Status.

---

## 3. Deployment Paths

| Thành Phần | Đường Dẫn trên Server (`10.212.154.2`) | Chú Thích |
| :--- | :--- | :--- |
| **Backend** | `/home/user/bacnet_mqtt_gateway/backend` | Mã nguồn FastAPI |
| **Frontend** | `/home/user/bacnet_mqtt_gateway/frontend_v2/dist` | File web tĩnh sau khi build (HTML/JS/CSS) |
| **Service File** | `/etc/systemd/system/bacnet-gateway.service` | Quản lý auto-start |
| **NGINX Config** | `/etc/nginx/sites-available/bacnet-gateway` | Cấu hình web server & reverse proxy |

---

## 4. Quản Lý Network Interfaces (Netplan)
File cấu hình: `/etc/netplan/01-network-manager-all.yaml`

| Interface | IP Address | Mô Tả |
| :--- | :--- | :--- |
| eth0 | 10.25.7.155 | Mạng chính của văn phòng |
| eth1 | 192.168.20.250 | Mạng chuyên biệt kết nối tủ BMS/BACnet (có route) |
| tun0 | DHCP OpenVPN | Mạng của VPN server |
| tailscale0 | 10.212.154.2 | Tailscale VPN |
| tun0 | 10.212.154.2 | OpenVPN (→ VPN clients truy cập BACnet) |

## OpenVPN Server
- **Server:** 10.25.7.155 (nxchieu.duckdns.org:54194)
- **Tạo client mới:**
```bash
ssh user@10.25.7.155
sudo /etc/openvpn/gen-client.sh TenClient
# File .ovpn tại: /etc/openvpn/client/TenClient.ovpn
```
- VPN clients truy cập 192.168.20.0/24 qua NAT trên máy này
## Troubleshooting: BAC0 Unicast Read no-response Timeout (Tailscale)
If BACnet broadcasts (like Who-Is / Discovery) work perfectly but unicas t commands (like reading a specific device property) always time out with a `no-response` error, check your **Linux Routing Table**. 
If you are running **Tailscale** on the gateway, it may inject a broader subnet route (e.g., `192.168.20.0/22`) into Tailscale's routing table (table 52) which takes priority over your local LAN table. 
As a result, responses to UDP Unicast packets get incorrectly routed through the VPN interface instead of the local interface, causing a timeout.

**Fix:** Add a more specific static route to your local subnet in Tailscale's routing table so local traffic stays local:
```bash
sudo ip route add 192.168.20.0/24 dev ens38 table 52
```
*(Replace `192.168.20.0/24` with your BACnet subnet and `ens38` with your local network interface).*

## Troubleshooting: Discovery Fails and Logs "dictionary changed size during iteration"
If the `/api/bacnet/discover` API returns an empty list and the backend logs show `RuntimeError: dictionary changed size during iteration`, this happens because BAC0 constantly populates `_network.discoveredDevices` in a background task while the API is trying to iterate over it.

**Fix:** Safely wrap the iteration over the keys/items with `list()` to create a detached copy:
```python
# Fixed in bacnet_service.py
for key, info in list(self._network.discoveredDevices.items()):
```
