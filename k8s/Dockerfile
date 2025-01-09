# This node setup image uses old ScyllaDB version as a base because disk setup scripts inside allowed to
# provide locations of raid, mount etc. Newer images have them hardcoded and they don't match host paths within the container.
#
# Using older version of ScyllaDB image is ok'ish from security point of view,
# because we do run `yum update` as one of the steps so we get all the OS/packages bug fixes.
#
# !!! This setup is considered **deprecated** and will be removed soon in favor of different, safer solution. !!!
FROM docker.io/scylladb/scylla:6.2.2 as base

# Disable scylla repo, as 4.1 is already EOL.
RUN yum-config-manager --disable scylla --disable scylla-generic --disable scylladb-scylla-3rdparty

# Install scripts dependencies.
RUN yum -y install epel-release && \
    yum -y clean expire-cache && \
    yum -y update && \
    yum install -y hwloc ethtool python3-yaml python3 python3-devel gcc && \
    yum clean all

RUN pip3 install pyyaml psutil

ARG cloud_provider

COPY "k8s/${cloud_provider}_scylla_create_devices" /opt/scylladb/scylla-machine-image/scylla_create_devices
COPY k8s/scylla_k8s_node_setup /opt/scylladb/scylla-machine-image/scylla_k8s_node_setup

ENTRYPOINT ["/opt/scylladb/scylla-machine-image/scylla_k8s_node_setup"]
