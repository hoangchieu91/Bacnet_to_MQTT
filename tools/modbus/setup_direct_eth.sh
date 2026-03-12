#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# Direct Ethernet Setup — Pi-to-Pi without switch/router
#
# Chạy trên MỖI Pi để cấu hình IP tĩnh cho kết nối trực tiếp.
# Chỉ cần 1 dây LAN nối Pi-1 ↔ Pi-2.
#
# Usage:
#   # Trên Pi-1 (Inline):
#   sudo bash setup_direct_eth.sh pi1
#
#   # Trên Pi-2 (Passive):
#   sudo bash setup_direct_eth.sh pi2
#
#   # Kiểm tra kết nối:
#   sudo bash setup_direct_eth.sh test
#
#   # Gỡ bỏ (revert):
#   sudo bash setup_direct_eth.sh remove
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────
ETH_IFACE="eth0"           # Ethernet interface (thường là eth0)
PI1_IP="192.168.100.1"
PI2_IP="192.168.100.2"
SUBNET="24"
NET_NAME="direct-pi"
CONF_FILE="/etc/NetworkManager/system-connections/${NET_NAME}.nmconnection"
NETPLAN_FILE="/etc/netplan/99-direct-pi.yaml"

# ── Colors ────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ── Detect Ethernet interface ─────────────────────────────────────
detect_eth() {
    # Find first non-loopback, non-wlan interface
    local iface
    for iface in $(ls /sys/class/net/ | grep -v '^lo$' | grep -v '^wlan'); do
        if [ -d "/sys/class/net/$iface" ]; then
            ETH_IFACE="$iface"
            return 0
        fi
    done
    # Fallback
    ETH_IFACE="eth0"
}

# ── Setup using nmcli (NetworkManager) ────────────────────────────
setup_nm() {
    local MY_IP="$1"
    local PEER_IP="$2"
    local ROLE="$3"

    info "Setting up via NetworkManager on $ETH_IFACE"

    # Remove existing connection if any
    nmcli connection delete "$NET_NAME" 2>/dev/null || true

    # Create connection
    nmcli connection add \
        type ethernet \
        con-name "$NET_NAME" \
        ifname "$ETH_IFACE" \
        ipv4.method manual \
        ipv4.addresses "${MY_IP}/${SUBNET}" \
        ipv6.method disabled \
        connection.autoconnect yes \
        connection.autoconnect-priority 100

    # Bring up
    nmcli connection up "$NET_NAME"

    ok "Interface $ETH_IFACE → $MY_IP/$SUBNET ($ROLE)"
    ok "Peer expected at $PEER_IP"
}

# ── Setup using ip command (fallback) ─────────────────────────────
setup_ip() {
    local MY_IP="$1"
    local PEER_IP="$2"
    local ROLE="$3"

    info "Setting up via ip command on $ETH_IFACE"

    # Flush existing addresses
    ip addr flush dev "$ETH_IFACE" 2>/dev/null || true

    # Set IP
    ip addr add "${MY_IP}/${SUBNET}" dev "$ETH_IFACE"
    ip link set "$ETH_IFACE" up

    ok "Interface $ETH_IFACE → $MY_IP/$SUBNET ($ROLE)"
    ok "Peer expected at $PEER_IP"

    warn "This is TEMPORARY — will reset on reboot"
    warn "For persistent config, install NetworkManager or create netplan file"

    # Create netplan file for persistence
    if [ -d /etc/netplan ]; then
        cat > "$NETPLAN_FILE" <<EOF
# Dual-Pi direct Ethernet connection
network:
  version: 2
  ethernets:
    ${ETH_IFACE}:
      addresses:
        - ${MY_IP}/${SUBNET}
      dhcp4: false
EOF
        info "Netplan file created: $NETPLAN_FILE"
        info "Run 'sudo netplan apply' to make persistent"
    fi
}

# ── Test connection ───────────────────────────────────────────────
test_connection() {
    info "Testing connection..."
    detect_eth

    # Detect our IP
    local MY_IP=$(ip -4 addr show "$ETH_IFACE" 2>/dev/null | grep -oP '192\.168\.100\.\d+' | head -1)

    if [ -z "$MY_IP" ]; then
        err "No 192.168.100.x address found on $ETH_IFACE"
        err "Run 'sudo bash $0 pi1' or 'sudo bash $0 pi2' first"
        exit 1
    fi

    local PEER_IP
    if [ "$MY_IP" = "$PI1_IP" ]; then
        PEER_IP="$PI2_IP"
        info "This is Pi-1 ($MY_IP) → testing Pi-2 ($PEER_IP)"
    else
        PEER_IP="$PI1_IP"
        info "This is Pi-2 ($MY_IP) → testing Pi-1 ($PEER_IP)"
    fi

    echo ""

    # Ping test
    if ping -c 3 -W 2 "$PEER_IP" > /dev/null 2>&1; then
        ok "✅ Ping OK — $PEER_IP reachable"
    else
        err "❌ Ping FAILED — $PEER_IP unreachable"
        echo ""
        warn "Checklist:"
        echo "  1. Dây LAN đã cắm chưa?"
        echo "  2. Pi bên kia đã chạy setup chưa?"
        echo "  3. Interface $ETH_IFACE có link? Check: ip link show $ETH_IFACE"
        exit 1
    fi

    # API test
    if command -v curl &>/dev/null; then
        local HTTP_CODE
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://${PEER_IP}:8766/api/sniffer/report" --connect-timeout 3 2>/dev/null || echo "000")
        if [ "$HTTP_CODE" = "200" ]; then
            ok "✅ API OK — http://${PEER_IP}:8766 responding"
        else
            warn "⚠️  API on port 8766 returned HTTP $HTTP_CODE (service may not be running)"
        fi
    fi

    # SSH test
    if command -v ssh &>/dev/null; then
        if ssh -o ConnectTimeout=3 -o BatchMode=yes "$PEER_IP" "echo ok" 2>/dev/null; then
            ok "✅ SSH OK"
        else
            info "SSH requires key or password"
        fi
    fi

    echo ""
    ok "Connection test complete"

    # Show suggested config
    echo ""
    info "To use this in config.yaml:"
    echo "  dual_pi:"
    echo "    enabled: true"
    echo "    pi2_url: \"http://${PEER_IP}:8766\""
}

# ── Remove configuration ─────────────────────────────────────────
remove_config() {
    info "Removing direct-pi configuration..."
    detect_eth

    # Remove NetworkManager connection
    if command -v nmcli &>/dev/null; then
        nmcli connection delete "$NET_NAME" 2>/dev/null && ok "Removed NM connection" || true
    fi

    # Remove netplan file
    if [ -f "$NETPLAN_FILE" ]; then
        rm -f "$NETPLAN_FILE"
        ok "Removed $NETPLAN_FILE"
        if command -v netplan &>/dev/null; then
            netplan apply 2>/dev/null || true
        fi
    fi

    # Flush IP
    ip addr flush dev "$ETH_IFACE" 2>/dev/null || true
    ip link set "$ETH_IFACE" up 2>/dev/null || true

    ok "Configuration removed. DHCP should resume."
}

# ── Main ──────────────────────────────────────────────────────────
show_help() {
    echo ""
    echo "  Dual-Pi Direct Ethernet Setup"
    echo "  ══════════════════════════════"
    echo ""
    echo "  Usage: sudo bash $0 <command>"
    echo ""
    echo "  Commands:"
    echo "    pi1     Setup this Pi as Pi-1 (Inline) — IP: $PI1_IP"
    echo "    pi2     Setup this Pi as Pi-2 (Passive) — IP: $PI2_IP"
    echo "    test    Test connection to peer Pi"
    echo "    remove  Remove configuration"
    echo "    help    Show this help"
    echo ""
    echo "  Requirements:"
    echo "    • 1 Ethernet cable (straight or crossover, both work)"
    echo "    • Run 'pi1' on one Pi, 'pi2' on the other"
    echo "    • Then run 'test' to verify"
    echo ""
    echo "  Topology:"
    echo "    Pi-1 ($PI1_IP) ──── LAN cable ──── Pi-2 ($PI2_IP)"
    echo ""
}

main() {
    if [ $# -lt 1 ]; then
        show_help
        exit 0
    fi

    detect_eth
    info "Detected interface: $ETH_IFACE"

    case "$1" in
        pi1)
            if command -v nmcli &>/dev/null; then
                setup_nm "$PI1_IP" "$PI2_IP" "Pi-1 Inline"
            else
                setup_ip "$PI1_IP" "$PI2_IP" "Pi-1 Inline"
            fi

            echo ""
            info "Next steps:"
            echo "  1. Cắm dây LAN từ Pi-1 vào Pi-2"
            echo "  2. Trên Pi-2 chạy: sudo bash $0 pi2"
            echo "  3. Test: sudo bash $0 test"
            echo "  4. Cập nhật config.yaml:"
            echo "       dual_pi:"
            echo "         enabled: true"
            echo "         pi2_url: \"http://$PI2_IP:8766\""
            ;;

        pi2)
            if command -v nmcli &>/dev/null; then
                setup_nm "$PI2_IP" "$PI1_IP" "Pi-2 Passive"
            else
                setup_ip "$PI2_IP" "$PI1_IP" "Pi-2 Passive"
            fi

            echo ""
            info "Next steps:"
            echo "  1. Cắm dây LAN từ Pi-2 vào Pi-1"
            echo "  2. Trên Pi-1 chạy: sudo bash $0 pi1"
            echo "  3. Test: sudo bash $0 test"
            ;;

        test)
            test_connection
            ;;

        remove)
            remove_config
            ;;

        help|--help|-h)
            show_help
            ;;

        *)
            err "Unknown command: $1"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
