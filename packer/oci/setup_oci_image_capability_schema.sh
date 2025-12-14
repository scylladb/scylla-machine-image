#!/usr/bin/env bash
#
# Script to create an Image Capability Schema and attach it to a specific OCI image
# 
# This script creates a capability schema that defines the supported configurations
# for a Scylla machine image (e.g., supported shapes, network types, etc.)
#
# Usage: 
#   ./setup_oci_image_capability_schema.sh --image-id <image_ocid> [options]
#
# Options:
#   --image-id <ocid>              Image OCID (required)
#   --compartment-id <ocid>        Compartment OCID (optional, uses image's compartment if not specified)
#   --schema-name <name>           Schema name (default: scylla-image-capabilities)
#   --schema-file <path>           Path to JSON file with schema definition (optional)
#   --help                         Show this help message

set -euo pipefail

# Default values
SCHEMA_NAME="scylla-image-capabilities"
IMAGE_ID=""
COMPARTMENT_ID=""
SCHEMA_FILE=""

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

OCI_CMD="$HOME/bin/oci"

# Function to print colored messages
print_info() {
    echo -e "${BLUE}ℹ ${NC}$1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

# Function to show usage
show_usage() {
    cat << EOF
Usage: $0 --image-id <image_ocid> [options]

Create an Image Capability Schema and attach it to a specific OCI image.

Options:
  --image-id <ocid>              Image OCID (required)
  --compartment-id <ocid>        Compartment OCID (optional, uses image's compartment if not specified)
  --schema-name <name>           Schema name (default: scylla-image-capabilities)
  --schema-file <path>           Path to JSON file with schema definition (optional)
  --help                         Show this help message

Examples:
  # Basic usage - create schema for an image
  $0 --image-id ocid1.image.oc1.iad.xxx

  # Specify compartment and custom schema name
  $0 --image-id ocid1.image.oc1.iad.xxx \\
     --compartment-id ocid1.compartment.oc1..xxx \\
     --schema-name my-custom-schema

  # Use a custom schema definition from a file
  $0 --image-id ocid1.image.oc1.iad.xxx \\
     --schema-file /path/to/schema.json

EOF
}

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --image-id)
            IMAGE_ID="$2"
            shift 2
            ;;
        --compartment-id)
            COMPARTMENT_ID="$2"
            shift 2
            ;;
        --schema-name)
            SCHEMA_NAME="$2"
            shift 2
            ;;
        --schema-file)
            SCHEMA_FILE="$2"
            shift 2
            ;;
        --help)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# Validate required parameters
if [[ -z "$IMAGE_ID" ]]; then
    print_error "Image ID is required"
    show_usage
    exit 1
fi

# Check if OCI CLI is installed
if ! command -v $OCI_CMD &> /dev/null; then
    print_error "OCI CLI is not installed. Please install it first."
    echo "Visit: https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm"
    exit 1
fi

# Check if jq is installed
if ! command -v jq &> /dev/null; then
    print_error "jq is not installed. Please install it first."
    echo "  Ubuntu/Debian: sudo apt-get install jq"
    echo "  RHEL/CentOS: sudo yum install jq"
    exit 1
fi

print_info "Creating Image Capability Schema for image: ${IMAGE_ID}"
echo "=========================================================================="

# Get image details to retrieve compartment if not specified
if [[ -z "$COMPARTMENT_ID" ]]; then
    print_info "Retrieving image details to get compartment ID..."
    IMAGE_DETAILS=$($OCI_CMD compute image get --image-id "${IMAGE_ID}" 2>/dev/null || true)
    
    if [[ -z "$IMAGE_DETAILS" ]]; then
        print_error "Failed to retrieve image details. Please check the image ID and your OCI CLI configuration."
        exit 1
    fi
    
    COMPARTMENT_ID=$(echo "$IMAGE_DETAILS" | jq -r '.data."compartment-id"')
    IMAGE_NAME=$(echo "$IMAGE_DETAILS" | jq -r '.data."display-name"')
    print_success "Found image: ${IMAGE_NAME}"
    print_success "Using compartment: ${COMPARTMENT_ID}"
fi

# Generate default schema definition if not provided
if [[ -z "$SCHEMA_FILE" ]]; then
    print_info "Generating default Image Capability Schema definition..."
    
    TEMP_SCHEMA_FILE=$(mktemp)
    cat > "$TEMP_SCHEMA_FILE" << 'EOF_SCHEMA'
{
  "schema_data": {
    "Compute.Firmware": {
      "descriptorType": "enumstring",
      "source": "IMAGE",
      "defaultValue": "UEFI_64",
      "values": ["UEFI_64", "BIOS"]
    },
    "Compute.LaunchMode": {
      "descriptorType": "enumstring",
      "source": "IMAGE",
      "defaultValue": "NATIVE",
      "values": ["NATIVE", "PARAVIRTUALIZED", "EMULATED"]
    },
    "Network.AttachmentType": {
      "descriptorType": "enumstring",
      "source": "IMAGE",
      "defaultValue": "VFIO",
      "values": ["VFIO", "PARAVIRTUALIZED"]
    },
    "Storage.ConsistentVolumeNaming": {
      "descriptorType": "boolean",
      "source": "IMAGE",
      "defaultValue": "true"
    },
    "Storage.LocalDataVolumeType": {
      "descriptorType": "enumstring",
      "source": "IMAGE",
      "defaultValue": "NVME",
      "values": ["NVME", "PARAVIRTUALIZED"]
    },
    "Storage.RemoteDataVolumeType": {
      "descriptorType": "enumstring",
      "source": "IMAGE",
      "defaultValue": "PARAVIRTUALIZED",
      "values": ["PARAVIRTUALIZED", "SCSI", "IDE"]
    }
  }
}
EOF_SCHEMA
    SCHEMA_FILE="$TEMP_SCHEMA_FILE"
    print_success "Generated default schema definition"
else
    print_info "Using schema definition from: ${SCHEMA_FILE}"
    
    if [[ ! -f "$SCHEMA_FILE" ]]; then
        print_error "Schema file not found: ${SCHEMA_FILE}"
        exit 1
    fi
    
    # Validate JSON
    if ! jq empty "$SCHEMA_FILE" 2>/dev/null; then
        print_error "Invalid JSON in schema file: ${SCHEMA_FILE}"
        exit 1
    fi
fi

# Create the Image Capability Schema
print_info "Creating Image Capability Schema: ${SCHEMA_NAME}"

# Read schema data from file
SCHEMA_DATA=$(jq -c '.schema_data' "$SCHEMA_FILE")

# Get the current global image capability schema version
print_info "Fetching current global image capability schema version..."
GLOBAL_SCHEMA=$($OCI_CMD compute global-image-capability-schema list --all 2>/dev/null | jq -r '.data[0]')
GLOBAL_SCHEMA_ID=$(echo "$GLOBAL_SCHEMA" | jq -r '.id')
GLOBAL_SCHEMA_VERSION=$(echo "$GLOBAL_SCHEMA" | jq -r '."current-version-name"')

if [[ -z "$GLOBAL_SCHEMA_VERSION" ]]; then
    print_error "Failed to retrieve global image capability schema version"
    exit 1
fi

print_success "Using global schema version: ${GLOBAL_SCHEMA_VERSION}"

# Step 1: Check if the image already has a schema attached by listing schemas for this image
print_info "Checking if image already has a capability schema..."
IMAGE_SCHEMAS=$($OCI_CMD compute image-capability-schema list \
    --compartment-id "${COMPARTMENT_ID}" \
    --image-id "${IMAGE_ID}" \
    --all 2>/dev/null || true)

IMAGE_SCHEMA_ID=$(echo "$IMAGE_SCHEMAS" | jq -r '.data[0].id // ""')

SCHEMA_ID=""

if [[ -n "$IMAGE_SCHEMA_ID" ]] && [[ "$IMAGE_SCHEMA_ID" != "null" ]]; then
    print_warning "Image already has a capability schema attached: ${IMAGE_SCHEMA_ID}"
    SCHEMA_ID="$IMAGE_SCHEMA_ID"
    
    # Get the schema details
    EXISTING_SCHEMA_NAME=$(echo "$IMAGE_SCHEMAS" | jq -r '.data[0]."display-name" // ""')
    
    print_info "Existing schema name: ${EXISTING_SCHEMA_NAME}"
    print_info "Updating existing schema with current capabilities..."
    
    UPDATE_OUTPUT=$($OCI_CMD compute image-capability-schema update \
        --image-capability-schema-id "${SCHEMA_ID}" \
        --schema-data "$SCHEMA_DATA" \
        --force \
        2>&1)
    
    if [[ $? -eq 0 ]]; then
        print_success "Updated Image Capability Schema: ${SCHEMA_ID}"
    else
        print_error "Failed to update Image Capability Schema"
        echo "$UPDATE_OUTPUT"
        exit 1
    fi
else
    print_info "No existing schema found on image."
    
    # Step 2: Check if a schema with this name already exists in the compartment
    print_info "Checking if schema with name '${SCHEMA_NAME}' already exists..."
    EXISTING_SCHEMA=$($OCI_CMD compute image-capability-schema list \
        --compartment-id "${COMPARTMENT_ID}" \
        --all 2>/dev/null | jq -r --arg name "$SCHEMA_NAME" '.data[] | select(."display-name" == $name) | .id' | head -n1 || true)

    if [[ -n "$EXISTING_SCHEMA" ]]; then
        print_warning "Schema '${SCHEMA_NAME}' already exists: ${EXISTING_SCHEMA}"
        SCHEMA_ID="$EXISTING_SCHEMA"
        
        # Update the existing schema with our schema data
        print_info "Updating existing schema with current capabilities..."
        UPDATE_OUTPUT=$($OCI_CMD compute image-capability-schema update \
            --image-capability-schema-id "${SCHEMA_ID}" \
            --schema-data "$SCHEMA_DATA" \
            --force \
            2>&1)
        
        if [[ $? -eq 0 ]]; then
            print_success "Updated Image Capability Schema: ${SCHEMA_ID}"
        else
            print_error "Failed to update Image Capability Schema"
            echo "$UPDATE_OUTPUT"
            exit 1
        fi
        
        # Attach the schema to the image
        print_info "Attaching schema to image..."
        IMAGE_UPDATE_OUTPUT=$($OCI_CMD compute image update \
            --image-id "${IMAGE_ID}" \
            --image-capability-schema-id "${SCHEMA_ID}" \
            --force \
            2>&1)
        
        if [[ $? -ne 0 ]]; then
            print_error "Failed to attach schema to image"
            echo "$IMAGE_UPDATE_OUTPUT"
            exit 1
        fi
    else
        # Step 3: Create a new Image Capability Schema
        print_info "Creating new Image Capability Schema: ${SCHEMA_NAME}"
        
        CREATE_OUTPUT=$($OCI_CMD compute image-capability-schema create \
            --compartment-id "${COMPARTMENT_ID}" \
            --image-id "${IMAGE_ID}" \
            --global-image-capability-schema-version-name "${GLOBAL_SCHEMA_VERSION}" \
            --display-name "${SCHEMA_NAME}" \
            --schema-data "$SCHEMA_DATA" \
            2>&1)
        
        if [[ $? -eq 0 ]]; then
            SCHEMA_ID=$(echo "$CREATE_OUTPUT" | jq -r '.data.id')
            print_success "Created Image Capability Schema: ${SCHEMA_ID}"
        else
            print_error "Failed to create Image Capability Schema"
            echo "$CREATE_OUTPUT"
            exit 1
        fi
        
        # Wait for schema to become active
        print_info "Waiting for schema to become ACTIVE..."
        sleep 3
    fi
fi

# Step 4: Verify the attachment
print_info "Verifying schema attachment..."
sleep 2

VERIFY_SCHEMAS=$($OCI_CMD compute image-capability-schema list \
    --compartment-id "${COMPARTMENT_ID}" \
    --image-id "${IMAGE_ID}" \
    --all 2>/dev/null || true)

VERIFY_OUTPUT=$(echo "$VERIFY_SCHEMAS" | jq -r '.data[0].id // ""')

if [[ "$VERIFY_OUTPUT" == "$SCHEMA_ID" ]]; then
    print_success "Schema successfully attached and verified!"
else
    print_warning "Schema ID mismatch. Expected: ${SCHEMA_ID}, Got: ${VERIFY_OUTPUT}"
    print_warning "Please verify manually."
fi

# Clean up temporary file if we created one
if [[ -n "${TEMP_SCHEMA_FILE:-}" ]] && [[ -f "${TEMP_SCHEMA_FILE}" ]]; then
    rm -f "${TEMP_SCHEMA_FILE}"
fi

# Summary
echo ""
echo "=========================================================================="
print_success "Image Capability Schema setup completed!"
echo ""
echo "Summary:"
echo "  Schema ID:     ${SCHEMA_ID}"
echo "  Schema Name:   ${SCHEMA_NAME}"
echo "  Image ID:      ${IMAGE_ID}"
echo "  Compartment:   ${COMPARTMENT_ID}"
echo ""
echo "You can verify the schema with:"
echo "  oci compute image-capability-schema get --image-capability-schema-id ${SCHEMA_ID}"
echo ""
echo "You can view image capabilities with:"
echo "  oci compute image-shape-compatibility-entry list --image-id ${IMAGE_ID}"
echo "=========================================================================="
