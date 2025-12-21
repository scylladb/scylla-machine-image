#!/bin/bash

set -euo pipefail

if ! command -v oci &> /dev/null
then
    echo "Error: The OCI CLI ('oci') could not be found. Please ensure it is installed and in your PATH." >&2
    exit 1
fi

COMPARTMENT_ID=${1:-${OCI_COMPARTMENT_ID:-}}

if [ -z "${COMPARTMENT_ID}" ]; then
  echo "Usage: $0 <compartment_id>" >&2
  echo "Alternatively, you can set the OCI_COMPARTMENT_ID environment variable." >&2
  exit 1
fi

OUTPUT_FILE="oci_net_params.json"

echo "Generating '${OUTPUT_FILE}' for compartment '${COMPARTMENT_ID}'..."

# The command to fetch and format the shape data.
# For Flex shapes, networkingBandwidthInGbps is null. Instead, we use
# defaultPerOcpuInGbps from the networkingBandwidthOptions object.
oci_command="oci compute shape list --compartment-id '${COMPARTMENT_ID}' --query 'data[].[shape, \"networking-bandwidth-in-gbps\", \"networking-bandwidth-options\".\"default-per-ocpu-in-gbps\"]' --output json"

if ! oci_output=$(eval "${oci_command}"); then
    echo "Error: Failed to execute OCI command. Please check your OCI configuration, permissions, and that the compartment OCID is correct." >&2
    exit 1
fi

echo "${oci_output}" > "${OUTPUT_FILE}"

if [ ! -s "${OUTPUT_FILE}" ]; then
    echo "Warning: '${OUTPUT_FILE}' was created but is empty." >&2
    echo "This could mean that no shapes were found in the specified compartment or there's a permissions issue." >&2
fi

echo "Successfully generated '${OUTPUT_FILE}'."
echo "Note: The output may contain 'null' values for bandwidth. This is expected."
echo "For fixed shapes, the second value is the total bandwidth."
echo "For Flex shapes, the third value is the bandwidth per OCPU, and the total bandwidth is calculated at runtime."
