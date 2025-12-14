# Building Scylla Images for Oracle Cloud Infrastructure (OCI)

This directory contains the necessary files to build Scylla machine images for Oracle Cloud Infrastructure using Packer.

## Prerequisites

1.  **OCI CLI**: Install and configure the OCI CLI.
    *   Installation: `bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)"`
    *   Configuration: `oci setup config`

2.  **Packer**: Ensure Packer is installed (version 1.8.0 or later recommended).

3.  **OCI Account**: You need:
    *   A valid OCI tenancy.
    *   A compartment to store the image.
    *   A VCN (Virtual Cloud Network) with a subnet that has internet access (via a NAT or Internet Gateway).
    *   Appropriate IAM permissions to create compute instances and images.

## Configuration

### 1. OCI Variables

Create a `packer/oci_variables.json` file with your OCI-specific configuration. You can copy `packer/oci_variables.json.example` as a template.

```json
{
  "availability_domain": "ewbj:US-ASHBURN-AD-1",
  "compartment_ocid": "ocid1.tenancy.oc1..aaaaaaaaky2vquxp7fzklpudykqspjdxvzq4noowkqwxessmc4tgjj6s3uzq",
  "subnet_ocid": "ocid1.subnet.oc1.iad.aaaaaaaa6cixtdwvab774brfct5fmusejfbgaywyt3oksr5pirrl2k6gzoea",
  "region": "us-ashburn-1",
  "tenancy_ocid": "ocid1.tenancy.oc1..aaaaaaaaky2vquxp7fzklpudykqspjdxvzq4noowkqwxessmc4tgjj6s3uzq",
  "user_ocid": "ocid1.user.oc1..aaaaaaaa5ycrcuogdjbst6wkbpyykn6tbx2adldkvb7wsb5fstisaqua5tja",
  "fingerprint": "d6:22:b1:69:ba:7e:4e:16:51:93:8c:c5:11:63:b0:2c",
  "key_file": "~/.oci/fruch@scylladb.com-2025-12-15T21_28_33.842Z.pem",
  "instance_shape": "VM.Standard3.Flex",
  "instance_shape_config_ocpus": "2",
  "instance_shape_config_memory_in_gbs": "16"
}
```

### 2. Base Image

The builder will automatically find the latest Canonical Ubuntu 24.04 image for your region.

### 3. Defined Tags (Optional but Recommended)

The Packer build process will tag the resulting image with a set of defined tags. To use this feature, you need to create the tag definitions in your OCI tenancy first.

A script is provided to automate this process: `packer/oci/setup_oci_tags.sh`.

**To set up the defined tags:**

1.  **Create a Tag Namespace:**
    *   In the OCI Console, go to **Identity & Security** -> **Identity** -> **Tag Namespaces**.
    *   Create a new tag namespace (e.g., `scylla`). Note its OCID.

2.  **Edit the script:**
    *   Open `packer/oci/setup_oci_tags.sh` and replace the placeholder `TAG_NAMESPACE_OCID` with the OCID of the namespace you just created.

3.  **Run the script:**
    ```bash
    ./packer/oci/setup_oci_tags.sh
    ```
    This will create all the necessary tag keys in your namespace.

## Building the Image

The `packer/build_image.sh` script is the main entry point for building images.

```bash
./packer/build_image.sh \
  --target oci \
  --repo downloads.scylladb.com/unstable/scylla/master/deb/unified/latest/scylladb-master/scylla.list \
  --branch master \
  --scylla-release 2026.1 \
  --version 2026.1.0 \
  --arch x86_64 \
  --json-file packer/oci.json
```

*   `--target oci`: Specifies an OCI build.
*   `--repo`: The URL to the Scylla repository `.list` file.
*   `--version`, `--scylla-release`: The Scylla version to install.
*   `--arch`: The architecture (`x86_64` or `aarch64`).
*   `--json-file`: The path to your variables file (if not using the default `packer/oci_variables.json`).

The build process can take 20-30 minutes.

## Image Capability Schema

After the image is successfully built, the `build_image.sh` script will automatically run the `setup_oci_image_capability_schema.sh` script. This script creates and attaches an Image Capability Schema to the new image, which defines the supported configurations for instances launched from this image (e.g., supported shapes, volume types).

The schema sets `Storage.LocalDataVolumeType` to `NVME` by default.

## Using the Image

Once the image is built, you can launch instances from it using the OCI Console or OCI CLI. The image OCID will be printed at the end of the Packer build log.

### User Data

You can provide user data at launch time to configure the Scylla node. See `packer/user_data_example.json` for an example.

## Troubleshooting

*   **Logs:** Packer logs are located at `build/packer.log`.
*   **Authentication:** Ensure your `~/.oci/config` is correct and the specified user has the required permissions.
*   **Networking:** The subnet used for the build must have outbound internet access.

```