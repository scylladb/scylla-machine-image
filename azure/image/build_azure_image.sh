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
EXIT_STATUS=0
DRY_RUN=false
source ../../SCYLLA-VERSION-GEN

PRODUCT=$(cat build/SCYLLA-PRODUCT-FILE)
BUILD_ID=$(date -u '+%FT%H-%M-%S')
DIR=$(dirname $(readlink -f $0))

print_usage() {
    echo "build_azure_image.sh --localdeb --repo [URL]"
    echo "  --localdeb  deploy locally built debs"
    echo "  --repo  repository for both install and update, specify .list file URL"
    echo "  --repo-for-install  repository for install, specify .list file URL"
    echo "  --repo-for-update  repository for update, specify .list file URL"
    echo "  --product          scylla or scylla-enterprise"
    echo "  --download-no-server  download all deb needed excluding scylla from repo-for-install"
    echo "  --dry-run            validate template only (image is not built)"
    echo "  --build-id           Set unique build ID, will be part of Azure image name"
    echo "  --log-file           Path for log. Default build/azure_image.log on current dir"
    exit 1
}
LOCALDEB=0
DOWNLOAD_ONLY=0
PACKER_SUB_CMD="build -force -on-error=abort"
REPO_FOR_INSTALL=
PACKER_LOG_PATH=build/packer.log

while [ $# -gt 0 ]; do
    case "$1" in
        "--localdeb")
            LOCALDEB=1
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
            INSTALL_ARGS="$INSTALL_ARGS --product $2"
            shift 2
            ;;
        "--build-id")
            BUILD_ID=$2
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
    VERSION=$( apt-cache madison "$DEB"|head -n1|awk '{print $3}')
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

import_gpg_key () {
  TMPREPO=$(mktemp -u -p /etc/apt/sources.list.d/ --suffix .list)
  sudo curl -o $TMPREPO $REPO_FOR_INSTALL
  sudo apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 5e08fbd8b5d6ec9c
  sudo apt-get update --allow-insecure-repositories -y
}

if [ $LOCALDEB -eq 1 ]; then
    INSTALL_ARGS="$INSTALL_ARGS --localdeb"

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
        print_usage
        exit 1
    fi

    import_gpg_key

    cd "$DIR"/files
    apt-get download --allow-unauthenticated "$PRODUCT" "$PRODUCT"-machine-image "$PRODUCT"-jmx "$PRODUCT"-tools-core "$PRODUCT"-tools "$PRODUCT"-python3
    sudo rm -f $TMPREPO
    exit 0
else
    if [ -z "$REPO_FOR_INSTALL" ]; then
        print_usage
        exit 1
    fi
    import_gpg_key

    SCYLLA_VERSION=$(get_version_from_remote_deb $PRODUCT-server)
    SCYLLA_MACHINE_IMAGE_VERSION=$(get_version_from_remote_deb $PRODUCT-machine-image)
    SCYLLA_JMX_VERSION=$(get_version_from_remote_deb $PRODUCT-jmx)
    SCYLLA_TOOLS_VERSION=$(get_version_from_remote_deb $PRODUCT-tools)
    SCYLLA_PYTHON3_VERSION=$(get_version_from_remote_deb $PRODUCT-python3)

    sudo rm -f $TMPREPO

fi

SCYLLA_IMAGE_DESCRIPTION="scylla-$SCYLLA_VERSION scylla-machine-image-$SCYLLA_MACHINE_IMAGE_VERSION scylla-jmx-$SCYLLA_JMX_VERSION scylla-tools-$SCYLLA_TOOLS_VERSION scylla-python3-$SCYLLA_PYTHON3_VERSION"

if [ ! -f variables.json ]; then
    echo "create variables.json before start building AZURE"
    exit 1
fi

cd $DIR
mkdir -p build

REGION="EAST US"
SSH_USERNAME=azureuser

export PACKER_LOG=1
export PACKER_LOG_PATH=build/azure-image.log
echo "Scylla versions:"
echo "SCYLLA_VERSION: $SCYLLA_VERSION"
echo "SCYLLA_MACHINE_IMAGE_VERSION: $SCYLLA_MACHINE_IMAGE_VERSION"
echo "SCYLLA_JMX_VERSION: $SCYLLA_JMX_VERSION"
echo "SCYLLA_TOOLS_VERSION: $SCYLLA_TOOLS_VERSION"
echo "SCYLLA_PYTHON3_VERSION: $SCYLLA_PYTHON3_VERSION"
echo "BUILD_ID: $BUILD_ID"
echo "Calling Packer..."

/usr/bin/packer ${PACKER_SUB_CMD} \
  -var-file=variables.json \
  -var install_args="$INSTALL_ARGS" \
  -var region="$REGION" \
  -var ssh_username="$SSH_USERNAME" \
  -var scylla_version="$SCYLLA_VERSION" \
  -var scylla_machine_image_version="$SCYLLA_MACHINE_IMAGE_VERSION" \
  -var scylla_jmx_version="$SCYLLA_JMX_VERSION" \
  -var scylla_tools_version="$SCYLLA_TOOLS_VERSION" \
  -var scylla_python3_version="$SCYLLA_PYTHON3_VERSION" \
  -var scylla_image_description="${SCYLLA_IMAGE_DESCRIPTION:0:255}" \
  -var client_id="$AZURE_CLIENT_ID" \
  -var client_secret="$AZURE_CLIENT_SECRET" \
  -var tenant_id="$AZURE_TENANT_ID" \
  -var subscription_id="$AZURE_SUBSCRIPTION_ID" \
  -var scylla_build_id="$BUILD_ID" scylla_azure.json

  # For some errors packer gives a success status even if fails.
  # Search log for errors
  if $DRY_RUN ; then
    echo "DryRun: No need to grep errors on log"
  else
    grep "Builds finished. The artifacts of successful builds are:" $PACKER_LOG_PATH
    if [ $? -ne 0 ] ; then
      echo "Error: No Builds finished line found on log."
      EXIT_STATUS=1
    else
      echo "Success: Builds finished line found on log"
    fi
  fi

  exit $EXIT_STATUS
