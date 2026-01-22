import json

from lib.scylla_cloud import get_cloud_instance, is_azure, is_ec2, is_oci


def estimate_streaming_bandwidth():
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

    # TODO: other clouds

    return int((0.75 * net_bw) / (8 * 1024 * 1024))  # MiB/s
