FROM centos:7

RUN yum install -y wget curl unzip yum-utils sudo

ENV PACKER_VERSION=1.3.2
ENV EXPECTED="5e51808299135fee7a2e664b09f401b5712b5ef18bd4bad5bc50f4dcd8b149a1  packer_linux_amd64.zip"

RUN wget -nv https://releases.hashicorp.com/packer/${PACKER_VERSION}/packer_${PACKER_VERSION}_linux_amd64.zip -O packer_linux_amd64.zip && \
    echo $EXPECTED | sha256sum --check && \
    unzip -x packer_linux_amd64.zip -d /usr/bin && \
    rm packer_linux_amd64.zip 
