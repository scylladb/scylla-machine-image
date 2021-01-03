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

CLOUD_PROVIDER=

print_usage() {
    echo "build_image.sh -c [aws|gce|azure]"
    echo "  -c cloud provider"
    exit 1
}
while getopts c: option
do
 case "${option}"
 in
 c) CLOUD_PROVIDER=${OPTARG};;
 *) print_usage;;
 esac
done

if [[ ! -e k8s/build_image.sh ]]; then
    echo "run build_image.sh in top of scylla-machine-image dir"
    exit 1
fi

echo "Building in $PWD..."

VERSION=$(./SCYLLA-VERSION-GEN)
PACKAGE_NAME="scylladb/scylla-machine-image-k8s-$CLOUD_PROVIDER:$VERSION"

docker build . -f k8s/Dockerfile --build-arg cloud_provider=$CLOUD_PROVIDER -t $PACKAGE_NAME