FROM centos:7

RUN yum install -y wget curl unzip yum-utils sudo git
RUN yum-config-manager --add-repo https://rpm.releases.hashicorp.com/RHEL/hashicorp.repo
RUN yum install -y packer
