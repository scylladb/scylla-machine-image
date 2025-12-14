#!/bin/bash
set -e
set -o pipefail

# ============== Configuration ==============
# The compartment OCID is provided as a command-line argument.
# This is the compartment where Packer will create instances and images.

# --- You can change these names if you wish ---
USER_NAME="packer-builder-user"
GROUP_NAME="packer-builder-group"
POLICY_NAME="PackerBuilderPolicy"
KEY_DIR="$HOME/.oci/packer_keys"
PRIVATE_KEY_PATH="$KEY_DIR/oci_api_key.pem"
PUBLIC_KEY_PATH="$KEY_DIR/oci_api_key_public.pem"

# ===========================================

# Check if Compartment OCID is provided as an argument
if [ -z "$1" ]; then
    echo "ERROR: The Compartment OCID must be provided as the first argument."
    echo "Usage: $0 <compartment_ocid>"
    exit 1
fi
COMPARTMENT_ID="$1"

echo "Starting OCI Packer user setup for compartment: $COMPARTMENT_ID"


# 1. Create a directory for the API keys
mkdir -p "$KEY_DIR"
echo "Created directory for keys at $KEY_DIR"

# 2. Check for existing group or create a new one
echo "Ensuring IAM group '$GROUP_NAME' exists..."
GROUP_ID=$(oci iam group list --name "$GROUP_NAME" | jq -r --arg gn "$GROUP_NAME" '(.data[] | select(.name == $gn) | .id)')

if [ -z "$GROUP_ID" ]; then
    echo "Group '$GROUP_NAME' not found. Creating it..."
    GROUP_ID=$(oci iam group create --name "$GROUP_NAME" --description "Group for Packer image builder user" | jq -r '.data.id')
    if [ -z "$GROUP_ID" ]; then
        echo "ERROR: Failed to create group '$GROUP_NAME'."
        exit 1
    fi
    echo "Group created with ID: $GROUP_ID"
else
    echo "Found existing group with ID: $GROUP_ID"
fi


# 3. Check for existing user or create a new one
echo "Ensuring IAM user '$USER_NAME' exists..."
USER_ID=$(oci iam user list --name "$USER_NAME" | jq -r --arg un "$USER_NAME" '(.data[] | select(.name == $un) | .id)')

if [ -z "$USER_ID" ]; then
    echo "User '$USER_NAME' not found. Creating it..."
    USER_ID=$(oci iam user create --name "$USER_NAME" --description "Service account for Packer image builds" --email "${USER_NAME}@noreply.com" | jq -r '.data.id')
    if [ -z "$USER_ID" ]; then
        echo "ERROR: Failed to create user '$USER_NAME'."
        exit 1
    fi
    echo "User created with ID: $USER_ID"
else
    echo "Found existing user with ID: $USER_ID"
    echo "Ensuring user details are up-to-date..."
    oci iam user update --user-id "$USER_ID" --description "Service account for Packer image builds" --email "${USER_NAME}@noreply.com" > /dev/null
fi

# 4. Add User to Group
echo "Checking if user '$USER_NAME' is in group '$GROUP_NAME'..."
# Check if user is already in group by listing group members
IS_MEMBER=$(oci iam group list-users --group-id "$GROUP_ID" --all --output json 2>/dev/null | jq -r --arg uid "$USER_ID" '.data[] | select(.id == $uid) | .id')
if [ -z "$IS_MEMBER" ]; then
    echo "Adding user '$USER_NAME' to group '$GROUP_NAME'..."
    oci iam group add-user --user-id "$USER_ID" --group-id "$GROUP_ID"
    echo "User added to group successfully."
else
    echo "User '$USER_NAME' is already in group '$GROUP_NAME'."
fi


# 5. Define and Create Policy
# Note: Packer needs to manage compute images and use subnets for building.
POLICY_STATEMENTS='["Allow group '"${GROUP_NAME}"' to manage instances in compartment id '"${COMPARTMENT_ID}"'", "Allow group '"${GROUP_NAME}"' to manage instance-images in compartment id '"${COMPARTMENT_ID}"'", "Allow group '"${GROUP_NAME}"' to manage volumes in compartment id '"${COMPARTMENT_ID}"'", "Allow group '"${GROUP_NAME}"' to manage volume-attachments in compartment id '"${COMPARTMENT_ID}"'", "Allow group '"${GROUP_NAME}"' to use virtual-network-family in compartment id '"${COMPARTMENT_ID}"'", "Allow group '"${GROUP_NAME}"' to read app-catalog-listing in compartment id '"${COMPARTMENT_ID}"'", "Allow group '"${GROUP_NAME}"' to manage tag-namespaces in compartment id '"${COMPARTMENT_ID}"'", "Allow group '"${GROUP_NAME}"' to use compute-image-capability-schema in tenancy", "Allow group '"${GROUP_NAME}"' to inspect compartments in tenancy"]'

echo "Ensuring IAM policy '$POLICY_NAME' exists..."
POLICY_ID=$(oci iam policy list --compartment-id "$COMPARTMENT_ID" | jq -r --arg name "$POLICY_NAME" '.data[] | select(.name == $name) | .id')

if [ -z "$POLICY_ID" ]; then
    echo "Policy '$POLICY_NAME' not found. Creating it..."
    oci iam policy create --name "$POLICY_NAME" \
        --description "Policy with minimal permissions for Packer builds" \
        --compartment-id "$COMPARTMENT_ID" \
        --statements "$POLICY_STATEMENTS" > /dev/null
    echo "Policy created successfully."
else
    echo "Policy '$POLICY_NAME' already exists. Updating statements..."
    oci iam policy update --policy-id "$POLICY_ID" \
        --statements "$POLICY_STATEMENTS" \
        --version-date "" \
        --force > /dev/null
    echo "Policy updated successfully."
fi

# 6. Generate and upload API key
if [ -f "$PRIVATE_KEY_PATH" ]; then
    echo "API private key already exists at $PRIVATE_KEY_PATH. Skipping generation."
else
    echo "Generating new API private/public key pair..."
    openssl genrsa -out "$PRIVATE_KEY_PATH" 2048
    chmod 600 "$PRIVATE_KEY_PATH"
    openssl rsa -pubout -in "$PRIVATE_KEY_PATH" -out "$PUBLIC_KEY_PATH"
    echo "Keys generated."
fi

echo "Checking if public key is already uploaded..."
FINGERPRINT_TO_CHECK=$(openssl rsa -pubin -in "$PUBLIC_KEY_PATH" -outform DER | openssl md5 -c | awk '{print $2}')

EXISTING_KEY_FP=$(oci iam user api-key list --user-id "$USER_ID" | jq -r --arg fp "$FINGERPRINT_TO_CHECK" '.data[] | select(.fingerprint == $fp) | .fingerprint')

if [ -z "$EXISTING_KEY_FP" ]; then
    echo "Uploading public key to user account..."
    API_KEY_FINGERPRINT=$(oci iam user api-key upload --user-id "$USER_ID" --key-file "$PUBLIC_KEY_PATH" | jq -r '.data.fingerprint')
    echo "Public key uploaded successfully."
else
    echo "A key with the same fingerprint is already uploaded for this user."
    API_KEY_FINGERPRINT=$FINGERPRINT_TO_CHECK
fi

if [ -z "$API_KEY_FINGERPRINT" ]; then
    echo "ERROR: Failed to get API key fingerprint."
    exit 1
fi

# 7. Get Tenancy ID and Region from your OCI config
echo "Retrieving Tenancy OCID and Region from your OCI config..."
OCI_CONFIG_FILE="${OCI_CLI_CONFIG_FILE:-$HOME/.oci/config}"
OCI_PROFILE="${OCI_CLI_PROFILE:-DEFAULT}"

if [ ! -f "$OCI_CONFIG_FILE" ]; then
    echo "ERROR: OCI config file not found at $OCI_CONFIG_FILE"
    exit 1
fi

# Parse the config file for the specified profile
TENANCY_ID=$(awk -F'=' -v profile="[$OCI_PROFILE]" '
    $0 == profile { found=1; next }
    /^\[/ { found=0 }
    found && /^tenancy/ { gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; exit }
' "$OCI_CONFIG_FILE")

if [ -z "$TENANCY_ID" ]; then
    echo "ERROR: Could not retrieve Tenancy OCID from your OCI config. Make sure your OCI CLI is configured with a default profile."
    exit 1
fi

REGION=$(awk -F'=' -v profile="[$OCI_PROFILE]" '
    $0 == profile { found=1; next }
    /^\[/ { found=0 }
    found && /^region/ { gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; exit }
' "$OCI_CONFIG_FILE")

if [ -z "$REGION" ]; then
    echo "ERROR: Could not retrieve Region from your OCI config. Make sure your OCI CLI is configured with a default profile."
    exit 1
fi
echo "Found Tenancy: $TENANCY_ID, Region: $REGION"

# 8. Create OCI config file for the new user
CONFIG_FILE_PATH="$KEY_DIR/config"
echo "Creating OCI config file for the new user at $CONFIG_FILE_PATH..."
cat > "$CONFIG_FILE_PATH" <<-EOF
[DEFAULT]
user=${USER_ID}
fingerprint=${API_KEY_FINGERPRINT}
tenancy=${TENANCY_ID}
region=${REGION}
key_file=${PRIVATE_KEY_PATH}
EOF
echo "OCI config file created."

# 9. Output results
echo ""
echo "======================================================"
echo "✅ OCI Packer User Setup Complete!"
echo "======================================================"
echo "Add the following details to your Packer configuration (e.g., as environment variables):"
echo ""
echo "export PKR_VAR_tenancy_ocid=\"$TENANCY_ID\""
echo "export PKR_VAR_user_ocid=\"$USER_ID\""
echo "export PKR_VAR_fingerprint=\"$API_KEY_FINGERPRINT\""
echo "export PKR_VAR_private_key_path=\"$PRIVATE_KEY_PATH\""
echo ""
echo "Or use them directly in your packer.pkr.hcl file."
echo "------------------------------------------------------"
echo "An OCI CLI configuration file for this user has been created at:"
echo "\"$CONFIG_FILE_PATH\""
echo ""
echo "You can use it with the OCI CLI, for example:"
echo "oci --config-file \"$CONFIG_FILE_PATH\" os ns get"
echo "------------------------------------------------------"
echo "Tenancy OCID:    $TENANCY_ID"
echo "User OCID:       $USER_ID"
echo "Key Fingerprint: $API_KEY_FINGERPRINT"
echo "Private Key at:  $PRIVATE_KEY_PATH"
echo ""
echo "IMPORTANT: Protect your private key file. It is your credential!"
echo "======================================================"
