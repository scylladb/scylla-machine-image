#!/usr/bin/env bash
#
# Adds modern shape-compatibility entries to a custom-built OCI image so
# customers can launch the image on shape families the OCI auto-detector
# omits (notably the DenseIO Flex family).
#
# This is a workaround for Canonical's Ubuntu 26.04 Minimal not yet being
# onboarded to the OCI marketplace. The custom-imported Ubuntu 26.04
# cloudimg lacks `is-consistent-volume-naming-enabled=true` (an image-
# content-derived flag only set by Canonical's marketplace pipeline), so
# OCI's per-image shape-compat list excludes modern shapes. Manually
# adding entries here unblocks instance launches without changing the
# image content. Once Canonical publishes 26.04 to the OCI marketplace,
# this script can be deleted along with the rest of the custom-image
# bootstrap.
#
# Usage:
#   ./add_image_shape_compat.sh --image-id <image_ocid>

set -euo pipefail

IMAGE_ID=""
OCI_CMD="${OCI_CMD:-$(command -v oci || true)}"

# Shapes present on OCI Canonical-Ubuntu-24.04-Minimal marketplace
# image but absent from a default custom-imported 26.04 image.
SHAPES=(
    VM.DenseIO.E4.Flex
    VM.DenseIO.E5.Flex
    VM.DenseIO.E6.Flex
    BM.DenseIO.E5.128
    BM.Standard3.64
    BM.Standard4.120
    BM.Standard.E5.192
    BM.Standard.E6.256
    BM.Optimized3.36
    VM.Optimized3.Flex
)

while [[ $# -gt 0 ]]; do
    case $1 in
        --image-id) IMAGE_ID="$2"; shift 2 ;;
        --help)     sed -n '2,18p' "$0"; exit 0 ;;
        *)          echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

[[ -z "$IMAGE_ID" ]] && { echo "Error: --image-id is required" >&2; exit 1; }
[[ -z "$OCI_CMD" || ! -x "$OCI_CMD" ]] && { echo "Error: oci CLI not found (set OCI_CMD env var)" >&2; exit 1; }

echo "Adding ${#SHAPES[@]} shape-compat entries to image ${IMAGE_ID}..."
failures=0
for shape in "${SHAPES[@]}"; do
    output=$("$OCI_CMD" compute image-shape-compatibility-entry add \
        --image-id "$IMAGE_ID" --shape-name "$shape" \
        --query 'data.shape' --raw-output 2>&1) && rc=0 || rc=$?
    if [[ "$output" == "$shape" ]]; then
        echo "  + ${shape}"
    elif [[ "$output" == *"already exists"* ]]; then
        echo "  = ${shape} (already present)"
    else
        echo "  ! ${shape}: ${output} (rc=${rc})" >&2
        failures=$((failures + 1))
    fi
done
echo "Done. ${failures} failure(s)."
[[ "$failures" -gt 0 ]] && exit 1
exit 0
