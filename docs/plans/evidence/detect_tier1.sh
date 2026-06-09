#!/bin/bash
# Tier 1 networking detection test suite
# Run this on a GCP VM to probe all available detection methods
# Usage: bash detect_tier1.sh 2>&1 | tee output.txt

set -euo pipefail
IFACE=${1:-eth0}
DIVIDER="================================================================"

header() { echo ""; echo "$DIVIDER"; echo "=== $1 ==="; echo "$DIVIDER"; }

header "SYSTEM INFO"
echo "Hostname: $(hostname)"
echo "Kernel: $(uname -r)"
echo "Date: $(date -u)"
echo "Interface: $IFACE"

header "TEST 1: ethtool link speed (Primary Tier 1 signal)"
if command -v ethtool &>/dev/null; then
    ethtool "$IFACE" 2>&1 || echo "ethtool failed on $IFACE"
    echo ""
    echo "-- Just speed line --"
    ethtool "$IFACE" 2>/dev/null | grep -i speed || echo "No Speed line found"
else
    echo "ethtool NOT INSTALLED"
fi

header "TEST 2: Compute Engine API self-query (requires compute-ro scope)"
if command -v curl &>/dev/null && command -v jq &>/dev/null; then
    TOKEN=$(curl -sf "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" \
      -H "Metadata-Flavor: Google" 2>/dev/null | jq -r '.access_token' 2>/dev/null || echo "")
    if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
        echo "FAILED: Could not get access token (no service account or no scopes)"
    else
        PROJECT=$(curl -sf -H "Metadata-Flavor: Google" \
          "http://metadata.google.internal/computeMetadata/v1/project/project-id" 2>/dev/null || echo "")
        ZONE=$(curl -sf -H "Metadata-Flavor: Google" \
          "http://metadata.google.internal/computeMetadata/v1/instance/zone" 2>/dev/null \
          | rev | cut -d/ -f1 | rev || echo "")
        INSTANCE=$(curl -sf -H "Metadata-Flavor: Google" \
          "http://metadata.google.internal/computeMetadata/v1/instance/name" 2>/dev/null || echo "")
        echo "Project: $PROJECT  Zone: $ZONE  Instance: $INSTANCE"
        echo ""
        RESULT=$(curl -sf \
          "https://compute.googleapis.com/compute/v1/projects/$PROJECT/zones/$ZONE/instances/$INSTANCE?fields=name,networkPerformanceConfig,networkInterfaces" \
          -H "Authorization: Bearer $TOKEN" 2>/dev/null | jq . 2>/dev/null || echo "API_CALL_FAILED")
        echo "$RESULT"
    fi
else
    echo "curl or jq NOT INSTALLED"
fi

header "TEST 3: NIC type from instance metadata server"
NIC_TYPE=$(curl -sf -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/nic-type" \
  2>/dev/null || echo "UNAVAILABLE")
echo "nic-type: $NIC_TYPE"

header "TEST 4: gVNIC driver info (ethtool -i and modinfo)"
echo "-- ethtool -i --"
ethtool -i "$IFACE" 2>&1 || echo "ethtool -i failed"
echo ""
echo "-- modinfo gve --"
modinfo gve 2>&1 || echo "modinfo gve: module not found"
echo ""
echo "-- lsmod | grep gve --"
lsmod | grep gve || echo "gve module not in lsmod"
echo ""
echo "-- /sys/class/net/$IFACE/device/driver --"
readlink -f "/sys/class/net/$IFACE/device/driver" 2>/dev/null || echo "symlink not found"

header "TEST 5: Full network interface metadata (recursive)"
curl -sf -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/?recursive=true" \
  2>/dev/null | python3 -m json.tool 2>/dev/null \
  || curl -sf -H "Metadata-Flavor: Google" \
     "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/?recursive=true" \
     2>/dev/null \
  || echo "FAILED: metadata server unreachable"

header "TEST 6: Undocumented bandwidth-tier metadata endpoint"
echo "HTTP response code and body:"
curl -sv -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/bandwidth-tier" \
  2>&1 || echo "curl failed"

header "TEST 7: Machine type + CPU count from metadata"
MTYPE=$(curl -sf -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/machine-type" 2>/dev/null \
  | rev | cut -d/ -f1 | rev || echo "UNAVAILABLE")
echo "machine-type: $MTYPE"
echo "nproc: $(nproc)"
echo "CPU info: $(grep -m1 'model name' /proc/cpuinfo 2>/dev/null || echo N/A)"

header "TEST 8: Instance attributes (custom metadata)"
echo "Attribute keys:"
curl -sf -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/attributes/" \
  2>/dev/null || echo "UNAVAILABLE or empty"

header "BONUS: ethtool stats (gve-specific counters)"
ethtool -S "$IFACE" 2>&1 | head -40 || echo "ethtool -S failed"

header "BONUS: ip link details"
ip link show "$IFACE" 2>&1 || echo "ip link failed"

header "BONUS: dmesg gve messages"
dmesg 2>/dev/null | grep -i "gve\|gvnic" | tail -20 || echo "No gve messages in dmesg (or no dmesg access)"

header "SUMMARY"
echo "ethtool speed: $(ethtool $IFACE 2>/dev/null | grep -i speed | head -1 || echo N/A)"
echo "nic-type:      $(curl -sf -H 'Metadata-Flavor: Google' 'http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/nic-type' 2>/dev/null || echo N/A)"
echo "machine-type:  $MTYPE"
echo "gve loaded:    $(lsmod | grep -c '^gve' || echo 0)"
