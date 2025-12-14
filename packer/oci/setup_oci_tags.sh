#!/usr/bin/env bash
#
# Script to create OCI tag definitions for Scylla machine images
# This script creates all the required tag keys in the specified tag namespace
#
# Usage: ./setup_oci_tags.sh <tag_namespace_ocid>

set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <tag_namespace_ocid>"
    echo "Please provide the OCID of the tag namespace as the first argument."
    exit 1
fi

# Tag namespace OCID from command line
TAG_NAMESPACE_OCID="$1"

echo "Creating tag definitions in namespace: ${TAG_NAMESPACE_OCID}"
echo "=========================================================================="

# Array of tag definitions: "tag_name|description"
TAG_DEFINITIONS=(
    "scylla_version|Scylla database version"
    "machine_image_version|Scylla machine image version"
    "scylla_python3_version|Python 3 version used in the image"
    "user_data_format_version|User data format version"
    "creation_timestamp|Image creation timestamp"
    "branch|Git branch used to build the image"
    "operating_system|Operating system of the image"
    "scylla_build_sha_id|Git SHA ID of the Scylla build"
    "arch|CPU architecture (e.g., x86_64, arm64)"
    "build_tag|Build tag identifier"
    "environment|Build environment (e.g., dev, staging, prod)"
    "build_mode|Build mode (e.g., release, debug)"
)

# Function to check if a tag already exists
tag_exists() {
    local tag_name=$1
    oci iam tag list \
        --tag-namespace-id "${TAG_NAMESPACE_OCID}" \
        --all \
        2>/dev/null | jq -e --arg name "$tag_name" '.data[] | select(.name == $name)' >/dev/null 2>&1
}

# Create each tag
for tag_def in "${TAG_DEFINITIONS[@]}"; do
    IFS='|' read -r tag_name tag_description <<< "$tag_def"
    
    if tag_exists "$tag_name"; then
        echo "✓ Tag '$tag_name' already exists, skipping..."
    else
        echo "Creating tag: $tag_name"
        oci iam tag create \
            --tag-namespace-id "${TAG_NAMESPACE_OCID}" \
            --name "$tag_name" \
            --description "$tag_description"
        echo "✓ Created tag: $tag_name"
    fi
done

echo ""
echo "=========================================================================="
echo "All tag definitions created successfully!"
echo ""
echo "You can now verify the tags with:"
echo "  oci iam tag list --tag-namespace-id ${TAG_NAMESPACE_OCID} --all"
