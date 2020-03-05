#!/bin/bash -e
#
# Copyright 2020 ScyllaDB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

PACKAGE_NAME="scylla-machine-image"

. /etc/os-release

TARGET=
CLOUD_PROVIDER=

print_usage() {
    echo "build_rpm.sh -t [centos7|redhat] -c [aws|gce|azure]"
    echo "  -t target target distribution"
    echo "  -c cloud provider"
    exit 1
}
while getopts t:c: option
do
 case "${option}"
 in
 t) TARGET=${OPTARG};;
 c) CLOUD_PROVIDER=${OPTARG};;
 *) print_usage;;
 esac
done


echo ${CLOUD_PROVIDER}
echo ${TARGET}



if [[ ! -f /etc/redhat-release ]]; then
    echo "Need Redhat like OS to build RPM"
fi

# On clean CentOS Docker sudo command is not installed
if ! rpm -q sudo; then
    yum install -y sudo
fi


pkg_install() {
    if ! rpm -q $1; then
        sudo yum install -y $1
    else
        echo "$1 already installed."
    fi
}

if [[ ! -e dist/redhat/build_rpm.sh ]]; then
    echo "run build_rpm.sh in top of scylla-machine-image dir"
    exit 1
fi

if [[ "$(arch)" != "x86_64" ]]; then
    echo "Unsupported architecture: $(arch)"
    exit 1
fi

pkg_install rpm-build
pkg_install git
pkg_install python3
pkg_install python3-devel
pkg_install python3-pip

if [[ ! -f /usr/bin/pystache ]]; then
    pkg_install epel-release
    pkg_install python-pip
    pkg_install python2-pystache || pkg_install pystache
fi

echo "Running unit tests"
cd tests/aws
sudo pip3 install pyyaml==5.3
python3 -m unittest test_scylla_configure.py
cd -

echo "Building in $PWD..."

VERSION=$(./SCYLLA-VERSION-GEN)
SCYLLA_VERSION=$(cat build/SCYLLA-VERSION-FILE)
SCYLLA_RELEASE=$(cat build/SCYLLA-RELEASE-FILE)


RPMBUILD=$(readlink -f build/)
mkdir -pv ${RPMBUILD}/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS,CLOUDFORMATION}

git archive --format=tar --prefix=$PACKAGE_NAME-$SCYLLA_VERSION/ HEAD -o $RPMBUILD/SOURCES/$PACKAGE_NAME-$VERSION.tar
pystache dist/redhat/$PACKAGE_NAME.spec.mustache "{ \"version\": \"$SCYLLA_VERSION\", \"release\":
\"$SCYLLA_RELEASE\", \"package_name\": \"$PACKAGE_NAME\", \"cloud_provider\": \"$CLOUD_PROVIDER\",
\"scylla\": true }" > $RPMBUILD/SPECS/$PACKAGE_NAME.spec
if [[ "$TARGET" = "centos7" ]]; then
    rpmbuild -ba --define '_binary_payload w2.xzdio' --define "_topdir $RPMBUILD" --define "dist .el7" $RPM_JOBS_OPTS $RPMBUILD/SPECS/$PACKAGE_NAME.spec
else
    rpmbuild -ba --define '_binary_payload w2.xzdio' --define "_topdir $RPMBUILD" $RPM_JOBS_OPTS $RPMBUILD/SPECS/$PACKAGE_NAME.spec
fi

cp ${RPMBUILD}/../aws/cloudformation/scylla.yaml $RPMBUILD/CLOUDFORMATION/scylla_cluster_${SCYLLA_VERSION}_${SCYLLA_RELEASE}.yaml
