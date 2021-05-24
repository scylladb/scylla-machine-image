#!/bin/bash -e
#
# Copyright 2021 ScyllaDB
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

source ../../SCYLLA-VERSION-GEN

PRODUCT=$(cat build/SCYLLA-PRODUCT-FILE)
DIR=$(dirname $(readlink -f $0))
EXIT_STATUS=0
DRY_RUN=false

print_usage() {
    echo "build_deb_ami.sh --localdeb --repo [URL] --target [distribution]"
    echo "  --localdeb            deploy locally built debs"
    echo "  --repo                repository for both install and update, specify .repo/.list file URL"
    echo "  --repo-for-install    repository for install, specify .repo/.list file URL"
    echo "  --repo-for-update     repository for update, specify .repo/.list file URL"
    echo "  --product             scylla or scylla-enterprise"
    echo "  --dry-run             validate template only (image is not built)"
    echo "  --download-no-server  download all deb needed excluding scylla from repo-for-install"
    echo "  --log-file            Path for log. Default build/ami.log on current dir"
    exit 1
}
LOCALDEB=0
DOWNLOAD_ONLY=0
PACKER_SUB_CMD="build -force -on-error=abort"
REPO_FOR_INSTALL=
PACKER_LOG_PATH=build/ami.log

while [ $# -gt 0 ]; do
    case "$1" in
        "--localdeb")
            LOCALDEB=1
            shift 1
            ;;
        "--repo")
            REPO_FOR_INSTALL=$2
            echo "--repo: $REPO_FOR_INSTALL"
            INSTALL_ARGS="$INSTALL_ARGS --repo $2"
            shift 2
            ;;
        "--repo-for-install")
            REPO_FOR_INSTALL=$2
            echo "--repo-for-install: $REPO_FOR_INSTALL"
            INSTALL_ARGS="$INSTALL_ARGS --repo-for-install $2"
            shift 2
            ;;
        "--repo-for-update")
            INSTALL_ARGS="$INSTALL_ARGS --repo-for-update $2"
            shift 2
            ;;
        "--product")
            PRODUCT=$2
            INSTALL_ARGS="$INSTALL_ARGS --product $2"
            shift 2
            ;;
        "--log-file")
            PACKER_LOG_PATH=$2
            shift 2
            ;;
        "--download-no-server")
            DOWNLOAD_ONLY=1
            shift 1
            ;;
        "--dry-run")
            echo "!!! Running in DRY-RUN mode !!!"
            PACKER_SUB_CMD="validate"
            DRY_RUN=true
            shift 1
            ;;
        *)
            echo "ERROR: Illegal option: $1"
            print_usage
            ;;
    esac
done

get_version_from_local_deb () {
    DEB=$1
    VERSION=$(dpkg -f "$DEB" version)
    echo "$VERSION"
}

get_version_from_remote_deb () {
    DEB=$1
    VERSION=$(sudo apt-cache madison "$DEB"|head -n1|awk '{print $3}')
    echo "$VERSION"
}

deb_arch() {
    declare -A darch
    darch=(["x86_64"]=amd64 ["aarch64"]=arm64)
    echo "${darch[$(arch)]}"
}

check_deb_exists () {
    BASE_DIR=$1
    deb_files="$BASE_DIR/$PRODUCT-server*_$(deb_arch).deb $BASE_DIR/$PRODUCT-machine-image*_all.deb $BASE_DIR/$PRODUCT-jmx*_all.deb $BASE_DIR/$PRODUCT-tools-*_all.deb $BASE_DIR/$PRODUCT-python3*_$(deb_arch).deb"
    for deb in $deb_files
    do
        if [[ ! -f "$deb" ]]; then
            echo "ERROR: Matching DEB file not found [$deb]"
        exit 1
        fi
    done
}
declare -A AMI
AMI=(["x86_64"]=ami-0074ee617a234808d ["aarch64"]=ami-0ae74ae9c43584639)
REGION=us-east-1
SSH_USERNAME=ubuntu

if [ $LOCALDEB -eq 1 ]; then
    INSTALL_ARGS="$INSTALL_ARGS --localrpm"

    check_deb_exists "$DIR"/files

    SCYLLA_VERSION=$(get_version_from_local_deb "$DIR"/files/"$PRODUCT"-server*_$(deb_arch).deb)
    SCYLLA_MACHINE_IMAGE_VERSION=$(get_version_from_local_deb "$DIR"/files/"$PRODUCT"-machine-image*_all.deb)
    SCYLLA_JMX_VERSION=$(get_version_from_local_deb "$DIR"/files/"$PRODUCT"-jmx*_all.deb)
    SCYLLA_TOOLS_VERSION=$(get_version_from_local_deb "$DIR"/files/"$PRODUCT"-tools-*_all.deb)
    SCYLLA_PYTHON3_VERSION=$(get_version_from_local_deb "$DIR"/files/"$PRODUCT"-python3*_$(deb_arch).deb)

    cd "$DIR"/files
    dpkg-scanpackages . /dev/null | gzip -9c > Packages.gz
    cd -
elif [ $DOWNLOAD_ONLY -eq 1 ]; then
    if [ -z "$REPO_FOR_INSTALL" ]; then
        echo "ERROR: No --repo or --repo-for-install were given on DOWNLOAD_ONLY run."
        print_usage
        exit 1
    fi

    TMPREPO=$(mktemp -u -p /etc/apt/sources.list.d/ --suffix .list)
    sudo curl -o $TMPREPO $REPO_FOR_INSTALL
    sudo apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 5e08fbd8b5d6ec9c
    sudo apt-get update --allow-insecure-repositories -y
    cd "$DIR"/files
    apt-get download --allow-unauthenticated "$PRODUCT" "$PRODUCT"-machine-image "$PRODUCT"-jmx "$PRODUCT"-tools-core "$PRODUCT"-tools "$PRODUCT"-python3
    sudo rm -f $TMPREPO
    exit 0
else
    if [ -z "$REPO_FOR_INSTALL" ]; then
        echo "ERROR: No --repo or --repo-for-install were given."
        print_usage
        exit 1
    fi

    TMPREPO=$(mktemp -u -p /etc/apt/sources.list.d/ --suffix .list)
    sudo curl -o $TMPREPO $REPO_FOR_INSTALL
    sudo apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 5e08fbd8b5d6ec9c
    sudo apt-get update --allow-insecure-repositories -y

    SCYLLA_VERSION=$(get_version_from_remote_deb $PRODUCT-server)
    SCYLLA_MACHINE_IMAGE_VERSION=$(get_version_from_remote_deb $PRODUCT-machine-image)
    SCYLLA_JMX_VERSION=$(get_version_from_remote_deb $PRODUCT-jmx)
    SCYLLA_TOOLS_VERSION=$(get_version_from_remote_deb $PRODUCT-tools)
    SCYLLA_PYTHON3_VERSION=$(get_version_from_remote_deb $PRODUCT-python3)

    sudo rm -f $TMPREPO

fi

SCYLLA_AMI_DESCRIPTION="scylla-$SCYLLA_VERSION scylla-machine-image-$SCYLLA_MACHINE_IMAGE_VERSION scylla-jmx-$SCYLLA_JMX_VERSION scylla-tools-$SCYLLA_TOOLS_VERSION scylla-python3-$SCYLLA_PYTHON3_VERSION"

if [ ! -f variables.json ]; then
    echo "create variables.json before start building AMI"
    echo "see wiki page: https://github.com/scylladb/scylla/wiki/Building-CentOS-AMI"
    exit 1
fi

cd $DIR
mkdir -p build

export PACKER_LOG=1
export PACKER_LOG_PATH

/usr/bin/packer ${PACKER_SUB_CMD} -var-file=variables.json -var install_args="$INSTALL_ARGS" -var region="$REGION" -var source_ami="${AMI[$(arch)]}" -var ssh_username="$SSH_USERNAME" -var scylla_version="$SCYLLA_VERSION" -var scylla_machine_image_version="$SCYLLA_MACHINE_IMAGE_VERSION" -var scylla_jmx_version="$SCYLLA_JMX_VERSION" -var scylla_tools_version="$SCYLLA_TOOLS_VERSION" -var scylla_python3_version="$SCYLLA_PYTHON3_VERSION" -var scylla_ami_description="${SCYLLA_AMI_DESCRIPTION:0:255}" -var python="/usr/bin/python3" scylla.json

# For some errors packer gives a success status even if fails.
# Search log for errors
if $DRY_RUN ; then
  echo "DryRun: No need to grep errors on log"
else
  grep "us-east-1:" $PACKER_LOG_PATH
  if [ $? -ne 0 ] ; then
    echo "Error: No AMI creation line found on log."
    EXIT_STATUS=1
  else
    echo "Success: AMI creation line found on log"
  fi
fi

exit $EXIT_STATUS
