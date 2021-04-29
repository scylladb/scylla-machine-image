FROM centos:7

RUN yum install -y wget curl unzip yum-utils sudo git

ENV PACKER_VERSION=1.5.1
ENV EXPECTED="3305ede8886bc3fd83ec0640fb87418cc2a702b2cb1567b48c8cb9315e80047d  packer_linux_amd64.zip"

RUN wget -nv https://releases.hashicorp.com/packer/${PACKER_VERSION}/packer_${PACKER_VERSION}_linux_amd64.zip -O packer_linux_amd64.zip && \
    echo $EXPECTED | sha256sum --check && \
    unzip -x packer_linux_amd64.zip -d /usr/bin && \
    rm packer_linux_amd64.zip 
