import json
import os
import urllib.error
import urllib.request

from lib.scylla_cloud import get_cloud_instance, is_azure, is_ec2, is_gce, is_oci


def _get_gcp_primary_interface() -> str:
    """Return the first non-loopback interface (gVNIC uses ens4 on Ubuntu, eth0 on RHEL)."""
    try:
        for name in sorted(os.listdir("/sys/class/net")):
            if name != "lo":
                return name
    except OSError:
        # sysfs network interface listing unavailable; fall back to common default.
        return "eth0"
    return "eth0"


def _get_nic_speed_mbps() -> int | None:
    """Read NIC link speed from sysfs. Returns Mbps integer or None on failure.

    On GCP gVNIC, this queries the hypervisor via Admin Queue and reflects
    actual provisioned bandwidth (32000 for default n2-standard-48, 50000 for Tier 1).
    Requires no API scope.
    """
    try:
        iface = _get_gcp_primary_interface()
        with open(f"/sys/class/net/{iface}/speed") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _query_compute_api_tier() -> str | None:
    """Query the Compute Engine API for networkPerformanceConfig.totalEgressBandwidthTier.

    Returns 'TIER_1', 'DEFAULT', or None if API unavailable (no compute-ro scope).
    Requires compute.readonly scope on the VM service account.
    """
    metadata = "http://metadata.google.internal/computeMetadata/v1"
    hdr = {"Metadata-Flavor": "Google"}
    try:

        def meta(path):
            req = urllib.request.Request(f"{metadata}/{path}", headers=hdr)
            return urllib.request.urlopen(req, timeout=3).read().decode()

        token_raw = meta("instance/service-accounts/default/token")
        token = json.loads(token_raw)["access_token"]
        project = meta("project/project-id")
        zone = meta("instance/zone").rsplit("/", 1)[-1]
        instance = meta("instance/name")

        api_url = (
            f"https://compute.googleapis.com/compute/v1/"
            f"projects/{project}/zones/{zone}/instances/{instance}"
            f"?fields=networkPerformanceConfig"
        )
        req = urllib.request.Request(api_url, headers={"Authorization": f"Bearer {token}"})
        data = json.loads(urllib.request.urlopen(req, timeout=5).read())
        return data.get("networkPerformanceConfig", {}).get("totalEgressBandwidthTier", "DEFAULT")
    except Exception:
        return None


def _query_metadata_tier1_attribute() -> bool | None:
    """Read scylla_tier1_networking from GCP instance metadata attributes.

    This allows setting the override at VM creation time via:
        gcloud compute instances create ... --metadata=scylla_tier1_networking=true

    Returns True, False, or None if the attribute is absent or unreadable.
    Requires no API scope (instance/attributes are always readable).
    """
    metadata = "http://metadata.google.internal/computeMetadata/v1"
    hdr = {"Metadata-Flavor": "Google"}
    try:
        req = urllib.request.Request(f"{metadata}/instance/attributes/scylla_tier1_networking", headers=hdr)
        value = urllib.request.urlopen(req, timeout=3).read().decode().strip().lower()
        if value in ("true", "1", "yes"):
            return True
        if value in ("false", "0", "no"):
            return False
        return None  # Unrecognized value — treat as absent
    except Exception:
        return None


def _detect_gcp_tier1(default_bw_gbps: float, tier1_bw_gbps: float | None, tier1_override: bool | None = None) -> bool:
    """Detect whether GCP Tier 1 networking is active. Conservative: returns False if uncertain.

    Detection order:
    1. User override (tier1_override param from cloud-init user-data) — always wins
    2. GCP instance metadata attribute scylla_tier1_networking — set at VM creation time
    3. /sys/class/net/<iface>/speed vs known default — no scope needed
    4. Compute Engine API networkPerformanceConfig — requires compute-ro scope
    5. Conservative default: False
    """
    if tier1_bw_gbps is None:
        return False  # Instance type doesn't support Tier 1

    # 1. User override from cloud-init user-data wins unconditionally
    if tier1_override is not None:
        return tier1_override

    # 2. GCP instance metadata attribute (set at VM creation, no scope required)
    metadata_override = _query_metadata_tier1_attribute()
    if metadata_override is not None:
        return metadata_override

    # 3. sysfs speed comparison (zero-dependency)
    speed_mbps = _get_nic_speed_mbps()
    if speed_mbps is not None:
        default_mbps = int(default_bw_gbps * 1000)
        return speed_mbps > default_mbps

    # 4. Compute Engine API (requires compute-ro scope)
    api_tier = _query_compute_api_tier()
    return api_tier == "TIER_1"  # Conservative default: False when uncertain


def estimate_streaming_bandwidth(cloud_instance=None):
    if cloud_instance is None:
        cloud_instance = get_cloud_instance()
    net_bw = 0
    if is_ec2():
        instance_type = cloud_instance.instancetype
        # Generated with
        # aws ec2 describe-instance-types --query "InstanceTypes[].[InstanceType, NetworkInfo.NetworkPerformance, NetworkInfo.NetworkCards[0].BaselineBandwidthInGbps]" --output json
        with open("/opt/scylladb/scylla-machine-image/aws_net_params.json") as f:
            netinfo = json.load(f)
            instance_info = [info for info in netinfo if info[0] == instance_type]
            if len(instance_info) != 0:
                net_bw = int(instance_info[0][2] * 1000 * 1000 * 1000)  # Gbps -> bps

    elif is_oci():
        instance_type = cloud_instance.instancetype
        # Generated with
        # oci compute shape list -c <compartment_id> --query "data[].[shape, networkingBandwidthInGbps, networkingBandwidthPerOcpuInGbps]" --output json
        with open("/opt/scylladb/scylla-machine-image/oci_net_params.json") as f:
            netinfo = json.load(f)
            instance_info_list = [info for info in netinfo if info[0] == instance_type]
            if instance_info_list:
                instance_info = instance_info_list[0]
                net_bw_gbps = 0

                # instance_info[2] is networkingBandwidthPerOcpuInGbps (for flex shapes)
                if instance_info[2]:
                    ocpus = cloud_instance.ocpus
                    if ocpus:
                        net_bw_gbps = instance_info[2] * ocpus

                # instance_info[1] is networkingBandwidthInGbps (for non-flex shapes)
                if instance_info[1]:
                    net_bw_gbps = instance_info[1]

                if net_bw_gbps:
                    net_bw = int(net_bw_gbps * 1000 * 1000 * 1000)  # Gbps -> bps

    elif is_azure():
        instance_type = cloud_instance.instancetype
        # Data from Azure documentation for L-series VMs
        # https://docs.microsoft.com/en-us/azure/virtual-machines/sizes/
        with open("/opt/scylladb/scylla-machine-image/azure_net_params.json") as f:
            netinfo = json.load(f)
            if instance_type in netinfo:
                net_bw_gbps = netinfo[instance_type]["Network_Limit_Gbps"]
                net_bw = int(net_bw_gbps * 1000 * 1000 * 1000)  # Gbps -> bps

    elif is_gce():
        instance_type = cloud_instance.instancetype
        tier1_override = getattr(cloud_instance, "_tier1_override", None)
        # Data from GCP documentation for N2, N2D, and Z3 machine families
        # https://cloud.google.com/compute/docs/network-bandwidth
        # Format: [instance_type, default_bandwidth_gbps, tier1_bandwidth_gbps]
        with open("/opt/scylladb/scylla-machine-image/gcp_net_params.json") as f:
            netinfo = json.load(f)
            instance_info = [info for info in netinfo if info[0] == instance_type]
            if instance_info:
                default_bw_gbps = instance_info[0][1]
                tier1_bw_gbps = instance_info[0][2]  # None if type doesn't support Tier 1
                use_tier1 = _detect_gcp_tier1(default_bw_gbps, tier1_bw_gbps, tier1_override)
                net_bw_gbps = tier1_bw_gbps if (use_tier1 and tier1_bw_gbps) else default_bw_gbps
                net_bw = int(net_bw_gbps * 1000 * 1000 * 1000)  # Gbps -> bps

    return int((0.75 * net_bw) / (8 * 1024 * 1024))  # MiB/s
