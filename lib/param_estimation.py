import json
import yaml
from lib.scylla_cloud import get_cloud_instance, is_ec2

def estimate_streaming_bandwidth():
    cloud_instance = get_cloud_instance()
    net_bw = 0
    if is_ec2():
        instance_type = cloud_instance.instancetype
        # Generated with
        # aws ec2 describe-instance-types --query "InstanceTypes[].[InstanceType, NetworkInfo.NetworkPerformance, NetworkInfo.NetworkCards[0].BaselineBandwidthInGbps]" --output json
        with open('/opt/scylladb/scylla-machine-image/aws_net_params.json') as f:
            netinfo = json.load(f)
            instance_info = [info for info in netinfo if info[0] == instance_type]
            if len(instance_info) != 0:
                net_bw = int(instance_info[0][2] * 1024 * 1024 * 1024) #gbps -> bps
    # TODO: other clouds

    return int((.75 * net_bw) / (8 * 1024*1024)) # MB/s

