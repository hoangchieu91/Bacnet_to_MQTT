# BACnet Gateway — Ubuntu Server

## SSH Access
- **Tailscale IP:** 100.74.25.27
- **LAN IP:** 172.20.24.223
- **VPN IP:** 10.212.154.2 (OpenVPN client `SDN_2026`)
- **BACnet IP:** 192.168.20.113 (ens38 — mạng BACnet/IP)
- **User:** user
- **Pass:** Admin@12345

## Web UI
- **URL:** http://100.74.25.27:8080
- **VPN URL:** http://10.212.154.2:8080

## Services
- **BACnet-MQTT Gateway:** uvicorn on port 8080
- **Gateway source:** /home/user/bacnet_mqtt_gateway/
- **OpenVPN client:** openvpn-client@SDN_2026 (auto-start)
- **Tailscale:** tailscale0

## Hardware
- **CPU:** Intel i7-6700 @ 3.40GHz (1 vCPU)
- **RAM:** 1.9 GB
- **Swap:** 2.0 GB
- **Disk:** 29 GB (/dev/mapper/ubuntu--vg-ubuntu--lv)
- **OS:** Ubuntu 24.04 LTS

## Network Interfaces
| Interface | IP | Mục đích |
|-----------|-----|---------|
| ens33 | 172.20.24.223 | Internet (LAN) |
| ens38 | 192.168.20.113 | BACnet/IP LAN |
| tailscale0 | 100.74.25.27 | Tailscale VPN |
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
