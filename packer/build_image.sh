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

REALDIR=$(dirname $(readlink -f "$0"))
source "$REALDIR"/../SCYLLA-VERSION-GEN

BUILD_ID=$(date -u '+%FT%H-%M-%S')
DIR=$(dirname $(realpath -se $0))
PDIRNAME=$(basename $(realpath -se $DIR/..))
EXIT_STATUS=0
DRY_RUN=false
TARGET=

if [ -L "$0" ]; then
    if [ "$PDIRNAME" = "aws" ] || [ "$PDIRNAME" = "gce" ]; then
        TARGET="$PDIRNAME"
    else
        echo "no target detected"
        exit 1
    fi
fi

print_usage() {
    echo "build_ami.sh --localrpm --repo [URL] --target [distribution]"
    echo "  --localrpm           deploy locally built rpms"
    echo "  --repo               repository for both install and update, specify .repo/.list file URL"
    echo "  --repo-for-install   repository for install, specify .repo/.list file URL"
    echo "  --repo-for-update    repository for update, specify .repo/.list file URL"
    echo "  --product            scylla or scylla-enterprise"
    echo "  [--ami-regions]       Set regions to copy the AMI when done building it (including permissions and tags)"
    echo "  --dry-run            validate template only (image is not built)"
    echo "  --debug              Build on debug mode (cause a 'debug-image-' prefix to be added to the image name)"
    echo "  --build-id           Set unique build ID, will be part of GCE image name"
    echo "  --download-no-server download all rpm needed excluding scylla from 'repo-for-install'"
    echo "  --log-file           Path for log. Default build/ami.log on current dir"
    echo "  --target             Target cloud (aws/gce), needed when using this script directly, and not by soft links"
    exit 1
}
LOCALRPM=0
DOWNLOAD_ONLY=0
PACKER_SUB_CMD="build"
REPO_FOR_INSTALL=
PACKER_LOG_PATH=build/ami.log

while [ $# -gt 0 ]; do
    case "$1" in
        "--localrpm")
            LOCALRPM=1
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
        "--ami-regions"):
            AMI_REGIONS=$2
            echo "--ami-regions prameter: AMI_REGIONS |$AMI_REGIONS|"
            shift 2
            ;;
        "--product")
            PRODUCT=$2
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
        "--debug")
            echo "!!! DEBUG MODE !!!"
            DEBUG=true
            shift 1
            ;;
        "--target")
            if [ -n "$TARGET" ]; then
                print_usage
            fi
            if [ "$2" = "aws" ]; then
                DIR="$REALDIR/../$2/ami"
            elif [ "$2" = "gce" ]; then
                DIR="$REALDIR/../$2/image"
            else
                print_usage
            fi
            cd "$DIR"
            TARGET="$2"
            shift 2
            ;;
        *)
            echo "ERROR: Illegal option: $1"
            print_usage
            ;;
    esac
done

if [ -z "$PRODUCT" ]; then
    PRODUCT=$(cat build/SCYLLA-PRODUCT-FILE)
fi
INSTALL_ARGS="$INSTALL_ARGS --product $PRODUCT"

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
    rpm_files="$BASE_DIR/$PRODUCT-server*.$(arch).rpm $BASE_DIR/$PRODUCT-machine-image*.noarch.rpm $BASE_DIR/$PRODUCT-jmx*.noarch.rpm $BASE_DIR/$PRODUCT-tools-*.noarch.rpm $BASE_DIR/$PRODUCT-python3*.$(arch).rpm"
    for rpm in $rpm_files
    do
        if [[ ! -f "$rpm" ]]; then
            echo "ERROR: Matching RPM file not found [$rpm]"
        exit 1
        fi
    done
}

if [ -z "$TARGET" ]; then
    echo "Missing --target parameter. Please specify target cloud (aws/gce)"
    exit 1
fi

if [ $LOCALRPM -eq 1 ]; then
    INSTALL_ARGS="$INSTALL_ARGS --localrpm"

    check_rpm_exists $DIR/files

    SCYLLA_VERSION=$(get_version_from_local_rpm $DIR/files/$PRODUCT-server*.$(arch).rpm)
    SCYLLA_MACHINE_IMAGE_VERSION=$(get_version_from_local_rpm $DIR/files/$PRODUCT-machine-image*.noarch.rpm)
    SCYLLA_JMX_VERSION=$(get_version_from_local_rpm $DIR/files/$PRODUCT-jmx*.noarch.rpm)
    SCYLLA_TOOLS_VERSION=$(get_version_from_local_rpm $DIR/files/$PRODUCT-tools-*.noarch.rpm)
    SCYLLA_PYTHON3_VERSION=$(get_version_from_local_rpm $DIR/files/$PRODUCT-python3*.$(arch).rpm)
elif [ $DOWNLOAD_ONLY -eq 1 ]; then
    if [ -z "$REPO_FOR_INSTALL" ]; then
        echo "ERROR: No --repo or --repo-for-install were given on DOWNLOAD_ONLY run."
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
        echo "ERROR: No --repo or --repo-for-install were given."
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

if [ "$TARGET" = "aws" ]; then
    SSH_USERNAME=centos
    SOURCE_AMI_OWNER=125523088429
    REGION=us-east-1

    arch="$(uname -m)"
    case "$arch" in
      "x86_64")
        SOURCE_AMI_FILTER="CentOS 7.* x86_64"
        INSTANCE_TYPE="c4.xlarge"
        ;;
      "aarch64")
        SOURCE_AMI_FILTER="CentOS 7.* aarch64"
        INSTANCE_TYPE="im4gn.xlarge"
        ;;
      *)
        echo "Unsupported architecture: $arch"
        exit 1
    esac

    SCYLLA_AMI_DESCRIPTION="scylla-$SCYLLA_VERSION scylla-machine-image-$SCYLLA_MACHINE_IMAGE_VERSION scylla-jmx-$SCYLLA_JMX_VERSION scylla-tools-$SCYLLA_TOOLS_VERSION scylla-python3-$SCYLLA_PYTHON3_VERSION"

    PACKER_ARGS+=(-var region="$REGION")
    PACKER_ARGS+=(-var instance_type="$INSTANCE_TYPE")
    PACKER_ARGS+=(-var source_ami_filter="$SOURCE_AMI_FILTER")
    PACKER_ARGS+=(-var source_ami_owner="$SOURCE_AMI_OWNER")
    PACKER_ARGS+=(-var scylla_ami_description="${SCYLLA_AMI_DESCRIPTION:0:255}")
elif [ "$TARGET" = "gce" ]; then
    SSH_USERNAME=centos
    SOURCE_IMAGE_FAMILY="centos-8"

    PACKER_ARGS+=(-var source_image_family="$SOURCE_IMAGE_FAMILY")
    PACKER_ARGS+=(-var scylla_build_id="$BUILD_ID")
fi

if $DEBUG ; then
  PACKER_ARGS+=(-var image_prefix="debug-image-")
fi

if [ ! -f variables.json ]; then
    echo "create variables.json before start building AMI"
    echo "see wiki page: https://github.com/scylladb/scylla/wiki/Building-CentOS-AMI"
    exit 1
fi

cd $DIR
mkdir -p build

export PACKER_LOG=1
export PACKER_LOG_PATH

/usr/bin/packer ${PACKER_SUB_CMD} \
  -only="$TARGET" \
  -var-file=variables.json \
  -var install_args="$INSTALL_ARGS" \
  -var ssh_username="$SSH_USERNAME" \
  -var scylla_version="$SCYLLA_VERSION" \
  -var scylla_machine_image_version="$SCYLLA_MACHINE_IMAGE_VERSION" \
  -var scylla_jmx_version="$SCYLLA_JMX_VERSION" \
  -var scylla_tools_version="$SCYLLA_TOOLS_VERSION" \
  -var scylla_python3_version="$SCYLLA_PYTHON3_VERSION" \
  -var ami_regions="$AMI_REGIONS" \
  -var arch="$(arch)" \
  -var product="$PRODUCT" \
  "${PACKER_ARGS[@]}" \
  "$REALDIR"/scylla.json

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
