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

PRODUCT=scylla
DIR=$(dirname $(readlink -f $0))

print_usage() {
    echo "build_image.sh --localrpm --repo [URL] --target [distribution]"
    echo "  --localrpm           deploy locally built rpms"
    echo "  --repo               repository for both install and update, specify .repo file URL"
    echo "  --repo-for-install   repository for install, specify .repo file URL"
    echo "  --repo-for-update    repository for update, specify .repo file URL"
    echo "  --product            scylla or scylla-enterprise"
    echo "  --download-no-server download all rpms needed excluding scylla using .repo provided in --repo-for-install"
    exit 1
}
LOCALRPM=0
DOWNLOAD_ONLY=0

REPO_FOR_INSTALL=
while [ $# -gt 0 ]; do
    case "$1" in
        "--localrpm")
            LOCALRPM=1
            shift 1
            ;;
        "--repo")
            REPO_FOR_INSTALL=$2
            INSTALL_ARGS="$INSTALL_ARGS --repo $2"
            shift 2
            ;;
        "--repo-for-install")
            REPO_FOR_INSTALL=$2
            INSTALL_ARGS="$INSTALL_ARGS --repo-for-install $2"
            shift 2
            ;;
        "--repo-for-update")
            INSTALL_ARGS="$INSTALL_ARGS --repo-for-update $2"
            shift 2
            ;;
        "--product")
            PRODUCT=$2
            shift 2
            ;;
        "--download-no-server")
            DOWNLOAD_ONLY=1
            shift 1
            ;;
        *)
            print_usage
            ;;
    esac
done

get_version_from_local_rpm () {
  RPM=$1
  RELEASE=$(rpm -qi $RPM | awk '/Release/ { print $3 }' )
  VERSION=$(rpm -qi $RPM | awk '/Version/ { print $3 }' )
  echo "$VERSION-$RELEASE"
}

get_version_from_remote_rpm () {
    RPM=$1
    VERSION=$(yum provides $RPM | grep "Provide.*=" | awk '{print $5}'| tail -1)
    echo "$VERSION"
}

check_rpm_exists () {
    BASE_DIR=$1
    rpm_files="$BASE_DIR/$PRODUCT-server*.x86_64.rpm $BASE_DIR/$PRODUCT-machine-image*.noarch.rpm $BASE_DIR/$PRODUCT-jmx*.noarch.rpm $BASE_DIR/$PRODUCT-tools-*.noarch.rpm $BASE_DIR/$PRODUCT-python3*.x86_64.rpm"
    for rpm in $rpm_files
    do
        if [[ ! -f "$rpm" ]]; then
            echo "ERROR: Matching RPM file not found [$rpm]"
        exit 1
        fi
    done
}

if [ $LOCALRPM -eq 1 ]; then
    INSTALL_ARGS="$INSTALL_ARGS --localrpm"

    check_rpm_exists $DIR/files

    SCYLLA_VERSION=$(get_version_from_rpm $DIR/files/$PRODUCT-server*.x86_64.rpm)
    SCYLLA_MACHINE_IMAGE_VERSION=$(get_version_from_rpm $DIR/files/$PRODUCT-machine-image*.noarch.rpm)
    SCYLLA_JMX_VERSION=$(get_version_from_rpm $DIR/files/$PRODUCT-jmx*.noarch.rpm)
    SCYLLA_TOOLS_VERSION=$(get_version_from_rpm $DIR/files/$PRODUCT-tools-*.noarch.rpm)
    SCYLLA_PYTHON3_VERSION=$(get_version_from_rpm $DIR/files/$PRODUCT-python3*.x86_64.rpm)
elif [ $DOWNLOAD_ONLY -eq 1 ]; then
    if [ -z "$REPO_FOR_INSTALL" ]; then
        print_usage
        exit 1
    fi

    TMPREPO=$(mktemp -u -p /etc/yum.repos.d/ --suffix .repo)
    sudo curl -o $TMPREPO $REPO_FOR_INSTALL
    cd files
    yumdownloader $PRODUCT $PRODUCT-machine-image $PRODUCT-jmx $PRODUCT-tools-core $PRODUCT-tools $PRODUCT-python3
    sudo rm -f $TMPREPO
    exit 0
else
    if [ -z "$REPO_FOR_INSTALL" ]; then
        print_usage
        exit 1
    fi

    TMPREPO=$(mktemp -u -p /etc/yum.repos.d/ --suffix .repo)
    sudo curl -o $TMPREPO $REPO_FOR_INSTALL

    SCYLLA_VERSION=$(get_version_from_remote_rpm $PRODUCT-server)
    SCYLLA_MACHINE_IMAGE_VERSION=$(get_version_from_remote_rpm $PRODUCT-machine-image)
    SCYLLA_JMX_VERSION=$(get_version_from_remote_rpm $PRODUCT-jmx)
    SCYLLA_TOOLS_VERSION=$(get_version_from_remote_rpm $PRODUCT-tools)
    SCYLLA_PYTHON3_VERSION=$(get_version_from_remote_rpm $PRODUCT-python3)

    sudo rm -f $TMPREPO

fi


if [ ! -f variables.json ]; then
    echo "'variables.json' not found. Please create it before start building GCE Image."
    echo "See variables.json.example"
    exit 1
fi

cd $DIR
mkdir -p build

export PACKER_LOG=1
export PACKER_LOG_PATH=build/packer.log
echo "Scylla versions:"
echo "SCYLLA_VERSION: $SCYLLA_VERSION"
echo "SCYLLA_MACHINE_IMAGE_VERSION: $SCYLLA_MACHINE_IMAGE_VERSION"
echo "SCYLLA_JMX_VERSION: $SCYLLA_JMX_VERSION"
echo "SCYLLA_TOOLS_VERSION: $SCYLLA_TOOLS_VERSION"
echo "SCYLLA_PYTHON3_VERSION: $SCYLLA_PYTHON3_VERSION"
echo "Calling Packer..."
/usr/bin/packer build -force -on-error=abort -var-file=variables.json -var install_args="$INSTALL_ARGS" -var scylla_version="$SCYLLA_VERSION" -var scylla_machine_image_version="$SCYLLA_MACHINE_IMAGE_VERSION" -var scylla_jmx_version="$SCYLLA_JMX_VERSION" -var scylla_tools_version="$SCYLLA_TOOLS_VERSION" -var scylla_python3_version="$SCYLLA_PYTHON3_VERSION"  scylla_gce.json
