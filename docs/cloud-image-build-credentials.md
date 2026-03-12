# Cloud Image Build Credentials Setup

This document describes the GitHub Actions secrets required for the
`build-cloud-image.yaml` workflow, which builds cloud images on-demand from PRs
using `build/{cloud}-{arch}` labels.

## AWS

### Secrets

| Secret | Description |
|--------|-------------|
| `AWS_ACCESS_KEY_ID` | IAM user access key ID |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret access key |

### Setup

1. Create an IAM user (e.g., `gha-image-builder`) with programmatic access.
2. Attach a policy granting EC2 image build permissions:
   - `ec2:RunInstances`, `ec2:TerminateInstances`, `ec2:DescribeInstances`
   - `ec2:CreateImage`, `ec2:RegisterImage`, `ec2:DeregisterImage`
   - `ec2:DescribeImages`, `ec2:CopyImage`, `ec2:ModifyImageAttribute`
   - `ec2:CreateSecurityGroup`, `ec2:DeleteSecurityGroup`, `ec2:AuthorizeSecurityGroupIngress`
   - `ec2:CreateKeyPair`, `ec2:DeleteKeyPair`
   - `ec2:CreateTags`, `ec2:DescribeSubnets`, `ec2:DescribeSecurityGroups`
   - `ec2:CreateSnapshot`, `ec2:DeleteSnapshot`, `ec2:DescribeSnapshots`
   - `ec2:DescribeVolumes`, `ec2:CreateVolume`, `ec2:DeleteVolume`
3. Generate an access key and store `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`
   as repository secrets.

Region, security group, and other defaults are configured in `packer/ami_variables.json`.

## GCE

### Secrets

| Secret | Description |
|--------|-------------|
| `GCE_SERVICE_ACCOUNT_JSON` | Full JSON key for a GCP service account |

### Setup

1. In the `scylla-images` GCP project, create a service account
   (e.g., `gha-image-builder@scylla-images.iam.gserviceaccount.com`).
2. Grant the following roles:
   - `roles/compute.admin` (Compute Admin)
   - `roles/storage.admin` (Storage Admin)
3. Create and download a JSON key for the service account.
4. Store the entire JSON content as the `GCE_SERVICE_ACCOUNT_JSON` repository secret.

The workflow writes this to a temporary file and sets `GOOGLE_APPLICATION_CREDENTIALS`.
Project and zone are configured in `packer/gce_variables.json`.

## Azure

### Secrets

| Secret | Description |
|--------|-------------|
| `AZURE_CLIENT_ID` | Service principal application (client) ID |
| `AZURE_CLIENT_SECRET` | Service principal password/secret |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |

### Setup

1. Create a service principal:
   ```bash
   az ad sp create-for-rbac --name "gha-image-builder" \
     --role Contributor \
     --scopes /subscriptions/<SUBSCRIPTION_ID>/resourceGroups/SCYLLA-IMAGES
   ```
2. The command outputs `appId`, `password`, and `tenant` — store these as
   `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, and `AZURE_TENANT_ID` respectively.
3. Store your subscription ID as `AZURE_SUBSCRIPTION_ID`.

`build_image.sh` handles `az login --service-principal` automatically when these
environment variables are set (see line 264).

## OCI (Oracle Cloud Infrastructure)

### Secrets

| Secret | Description |
|--------|-------------|
| `OCI_USER_OCID` | OCID of the OCI user |
| `OCI_TENANCY_OCID` | OCID of the OCI tenancy |
| `OCI_FINGERPRINT` | Fingerprint of the API signing key |
| `OCI_KEY_FILE_CONTENTS` | Base64-encoded PEM private key |
| `OCI_REGION` | OCI region (e.g., `us-ashburn-1`) |
| `OCI_COMPARTMENT_OCID` | OCID of the compartment for images |
| `OCI_SUBNET_OCID` | OCID of the subnet for build instances |
| `OCI_AVAILABILITY_DOMAIN` | Availability domain for build instances |

### Setup

1. In the OCI console, go to **User Settings > API Keys > Add API Key**.
2. Upload or generate a PEM key pair.
3. Note the fingerprint, user OCID, and tenancy OCID from the configuration preview.
4. Base64-encode the private PEM key:
   ```bash
   base64 -w0 ~/.oci/oci_api_key.pem
   ```
5. Store the values as repository secrets per the table above.
6. Set the infrastructure IDs (`OCI_COMPARTMENT_OCID`, `OCI_SUBNET_OCID`,
   `OCI_AVAILABILITY_DOMAIN`) to point at the resources where build instances
   should launch.

The workflow decodes the PEM key to a temporary file and sets `OCI_CLI_KEY_FILE`.

## Jenkins

### Secrets

| Secret | Description |
|--------|-------------|
| `JENKINS_USERNAME` | Jenkins API user |
| `JENKINS_TOKEN` | Jenkins API token |

These are shared with the existing `trigger_jenkins.yaml` workflow. The image
build workflow triggers `on-demand-image-test/{cloud}` jobs on Jenkins, passing:

- `IMAGE_ID` — the built image identifier
- `CLOUD_TARGET` — cloud name (aws/gce/azure/oci)
- `ARCH` — architecture (x86_64/aarch64)
- `PR_NUMBER`, `PR_BRANCH`, `PR_SHA` — PR context

These Jenkins jobs need to be created as parameterized jobs that accept
the above parameters.
