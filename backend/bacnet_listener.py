"""
Passive BACnet traffic listener.

Sniffs who-is / who-has packets from a configured BMS server IP.
Uses AF_PACKET raw socket (Linux) so it does NOT conflict with BAC0's
own UDP socket already bound to port 47808.

Extracted device IDs are registered with GatewayEngine so they appear
in Device Health even if we never mapped them ourselves.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import socket
from typing import Callable

logger = logging.getLogger(__name__)

BACNET_UDP_PORT = 47808   # 0xBAC0


def _parse_bacnet_packet(data: bytes) -> set[int]:
    """
    Parse a raw BACnet/IP UDP payload.
    Returns a set of device IDs found in WHO-IS APDUs.
    Returns empty set for WHO-HAS or unknown packets.
    """
    ids: set[int] = set()
    try:
        if len(data) < 6:
            return ids
        # BVLC header must start with 0x81
        if data[0] != 0x81:
            return ids

        # BVLC length
        bvlc_len = struct.unpack_from(">H", data, 2)[0]
        if bvlc_len != len(data):
            return ids  # malformed or fragmented

        # Skip BVLC (4 bytes) → NPDU
        idx = 4

        # ----------- NPDU ----------
        if idx >= len(data):
            return ids
        npdu_ctrl = data[idx]; idx += 1

        # Destination network present
        if npdu_ctrl & 0x20:
            idx += 2  # DNET
            dlen = data[idx]; idx += 1
            idx += dlen   # DADR
            idx += 1      # hop count

        # Source network present
        if npdu_ctrl & 0x08:
            idx += 2  # SNET
            slen = data[idx]; idx += 1
            idx += slen   # SADR

        # ----------- APDU ----------
        if idx >= len(data):
            return ids

        apdu_type = data[idx]; idx += 1

        # Only care about Unconfirmed-REQ (0x10)
        if apdu_type != 0x10:
            return ids

        if idx >= len(data):
            return ids
        service = data[idx]; idx += 1

        # WHO-IS = service 0x08
        if service == 0x08:
            ids = _parse_who_is_apdu(data[idx:])

        # WHO-HAS = service 0x07  (best effort, we may not get all)
        elif service == 0x07:
            ids = _parse_who_has_apdu(data[idx:])

    except Exception as exc:
        logger.debug("[Listener] parse error: %s", exc)

    return ids


def _parse_who_is_apdu(apdu: bytes) -> set[int]:
    """Parse WHO-IS optional low/high device instance range."""
    ids: set[int] = set()
    if len(apdu) < 2:
        # Global WHO-IS without range – we can't enumerate IDs
        return ids

    try:
        low = None
        high = None
        i = 0
        while i < len(apdu):
            tag_byte = apdu[i]
            tag_num = (tag_byte >> 4) & 0x0F
            tag_len = tag_byte & 0x07
            i += 1
            if i + tag_len > len(apdu):
                break
            val = int.from_bytes(apdu[i: i + tag_len], "big")
            i += tag_len
            if tag_num == 0:
                low = val
            elif tag_num == 1:
                high = val

        if low is not None and high is not None and low <= high:
            span = high - low + 1
            if span <= 2000:        # sanity cap to avoid flooding the list
                for did in range(low, high + 1):
                    ids.add(did)
                logger.debug("[Listener] WHO-IS device range %d–%d (%d IDs)", low, high, span)
            else:
                # Very wide range — just record the endpoints
                ids.add(low)
                ids.add(high)
                logger.debug("[Listener] WHO-IS wide range %d–%d (recording endpoints only)", low, high)
    except Exception:
        pass

    return ids


def _parse_who_has_apdu(apdu: bytes) -> set[int]:
    """
    Parse WHO-HAS to extract an optional device instance range.
    Object identifier extraction is skipped (we don't match objects to devices).
    """
    ids: set[int] = set()
    try:
        low = None
        high = None
        i = 0
        while i < len(apdu):
            tag_byte = apdu[i]
            tag_num  = (tag_byte >> 4) & 0x0F
            tag_len  = tag_byte & 0x07
            i += 1
            if tag_num >= 2:        # context 2/3 = object identifier, stop
                break
            if i + tag_len > len(apdu):
                break
            val = int.from_bytes(apdu[i: i + tag_len], "big")
            i += tag_len
            if tag_num == 0:
                low = val
            elif tag_num == 1:
                high = val

        if low is not None and high is not None and low <= high:
            span = high - low + 1
            if span <= 2000:
                for did in range(low, high + 1):
                    ids.add(did)
    except Exception:
        pass
    return ids


# ---------------------------------------------------------------------------

class BACnetPassiveListener:
    """
    Listens for BACnet broadcast packets using a raw AF_PACKET socket (Linux).
    This does NOT conflict with BAC0 which already has AF_INET UDP port 47808.

    When WHO-IS / WHO-HAS packets are seen from ``server_ip``, the discovered
    device IDs are passed to ``on_devices_found(device_ids: set[int])`` callback.
    """

    ETH_HDR   = 14    # bytes: dst(6) + src(6) + ethertype(2)
    IP_MIN_HDR = 20
    UDP_HDR   = 8

    def __init__(
        self,
        server_ip: str,
        on_devices_found: Callable[[set[int]], None],
        iface: str = "",
    ):
        self._server_ip = server_ip
        self._callback  = on_devices_found
        self._iface     = iface
        self._task: asyncio.Task | None = None
        self._running = False

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._listen_loop(), name="bacnet-passive-listener")
        logger.info("[Listener] Passive listener started (watching %s)", self._server_ip)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[Listener] Passive listener stopped")

    # ── internal ──────────────────────────────────────────────────────────
    async def _listen_loop(self) -> None:
        loop = asyncio.get_running_loop()
        sock = None
        try:
            # AF_PACKET receives raw Ethernet frames — copy from kernel,
            # does NOT compete with BAC0's UDP socket.
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW,
                                 socket.htons(0x0800))  # 0x0800 = IPv4
            if self._iface:
                sock.bind((self._iface, 0))
            sock.setblocking(False)
            logger.info("[Listener] AF_PACKET raw socket ready")
        except PermissionError:
            logger.warning(
                "[Listener] AF_PACKET needs root/CAP_NET_RAW. "
                "Falling back to UDP SO_REUSEPORT listener."
            )
            sock = None
            await self._listen_loop_udp(loop)
            return
        except OSError as e:
            logger.warning("[Listener] Cannot open raw socket: %s — listener disabled", e)
            return

        try:
            while self._running:
                try:
                    raw = await loop.sock_recv(sock, 65535)
                    self._handle_raw(raw)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.debug("[Listener] recv error: %s", exc)
                    await asyncio.sleep(1)
        finally:
            sock.close()

    async def _listen_loop_udp(self, loop: asyncio.AbstractEventLoop) -> None:
        """Fallback: UDP socket with SO_REUSEPORT — may miss some packets."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", BACNET_UDP_PORT))
            sock.setblocking(False)
            logger.info("[Listener] UDP SO_REUSEPORT fallback on port %d", BACNET_UDP_PORT)

            while self._running:
                try:
                    data, addr = await loop.sock_recvfrom(sock, 4096)
                    if addr[0] == self._server_ip:
                        ids = _parse_bacnet_packet(data)
                        if ids:
                            self._callback(ids)
                except asyncio.CancelledError:
                    break
                except Exception:
                    await asyncio.sleep(1)
        except Exception as e:
            logger.warning("[Listener] UDP fallback failed: %s", e)
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _handle_raw(self, raw: bytes) -> None:
        """Decode Ethernet→IP→UDP→BACnet, filter by src IP and UDP port."""
        try:
            if len(raw) < self.ETH_HDR + self.IP_MIN_HDR + self.UDP_HDR:
                return

            # Ethernet: ethertype must be 0x0800 (IPv4)
            ethertype = struct.unpack_from(">H", raw, 12)[0]
            if ethertype != 0x0800:
                return

            ip_start = self.ETH_HDR
            ip_hdr_len = (raw[ip_start] & 0x0F) * 4
            protocol = raw[ip_start + 9]
            if protocol != 17:  # UDP
                return

            # Source IP
            src_ip = socket.inet_ntoa(raw[ip_start + 12: ip_start + 16])
            if src_ip != self._server_ip:
                return

            udp_start = ip_start + ip_hdr_len
            dst_port = struct.unpack_from(">H", raw, udp_start + 2)[0]
            if dst_port != BACNET_UDP_PORT:
                return

            # BACnet/IP payload starts after UDP header
            payload = raw[udp_start + self.UDP_HDR:]
            ids = _parse_bacnet_packet(payload)
            if ids:
                logger.info("[Listener] WHO-IS from %s → %d device IDs", self._server_ip, len(ids))
                self._callback(ids)

        except Exception:
            pass
