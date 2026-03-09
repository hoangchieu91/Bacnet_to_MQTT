#!/bin/bash
# BACnet Policy Routing — force 192.168.20.0/24 traffic via ens38, bypass Tailscale
#
# Install:
#   sudo cp scripts/bacnet-routes.sh /usr/local/sbin/bacnet-routes.sh
#   sudo chmod +x /usr/local/sbin/bacnet-routes.sh
#   sudo cp scripts/bacnet-routes.service /etc/systemd/system/
#   sudo systemctl daemon-reload
#   sudo systemctl enable --now bacnet-routes.service

BACNET_TABLE=200
BACNET_NAME=bacnet
BACNET_SRC=192.168.20.113
BACNET_NET=192.168.20.0/24
BACNET_DEV=ens38
BACNET_GW=192.168.20.254

# Ensure routing table name entry exists
grep -q "^${BACNET_TABLE}" /etc/iproute2/rt_tables \
    || echo "${BACNET_TABLE} ${BACNET_NAME}" >> /etc/iproute2/rt_tables

# Wait for interface to come up
for i in $(seq 1 15); do
    ip link show "$BACNET_DEV" 2>/dev/null | grep -q 'state UP' && break
    echo "Waiting for ${BACNET_DEV} (${i}/15)..."
    sleep 2
done

# Add/replace routes in table 200
ip route replace "${BACNET_NET}" dev "${BACNET_DEV}" src "${BACNET_SRC}" table "${BACNET_TABLE}"
ip route replace default via "${BACNET_GW}" dev "${BACNET_DEV}" table "${BACNET_TABLE}"

# Add policy rules (idempotent: delete then re-add)
ip rule del from "${BACNET_SRC}" lookup "${BACNET_TABLE}" 2>/dev/null
ip rule add from "${BACNET_SRC}" lookup "${BACNET_TABLE}" priority 100

ip rule del to "${BACNET_NET}" lookup "${BACNET_TABLE}" 2>/dev/null
ip rule add to   "${BACNET_NET}" lookup "${BACNET_TABLE}" priority 101

echo "BACnet routing rules applied:"
ip rule show | grep "prio 10[01]"
ip route show table ${BACNET_TABLE}
