FROM docker.io/scylladb/scylla:4.1.6 as base

# Install scripts dependencies.
RUN yum -y install epel-release && \
    yum -y clean expire-cache && \
    yum -y update && \
    yum install -y hwloc ethtool python3-yaml python3 python3-devel gcc && \
    yum clean all

RUN pip3 install pyyaml psutil

ARG cloud_provider

COPY $cloud_provider/scylla_create_devices /opt/scylladb/scylla-machine-image/scylla_create_devices
COPY k8s/scylla_k8s_node_setup /opt/scylladb/scylla-machine-image/scylla_k8s_node_setup

ENTRYPOINT ["/opt/scylladb/scylla-machine-image/scylla_k8s_node_setup"]
