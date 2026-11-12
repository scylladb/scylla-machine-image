# Scylla Machine Image

This repository provides tools to:
- Create cloud machine images with pre-installed Scylla
- Configure Scylla automatically on first boot via cloud-init
- Deploy Scylla clusters easily in cloud environments

## Quick Start

### Building Images

All cloud images are built using the unified `packer/build_image.sh` script.

**Repository URL Format**: Specify the repository URL without `http://` or `https://` prefix. The script automatically prepends `https://`.

**Example repository URLs**:
- Master branch: `downloads.scylladb.com/unstable/scylla/master/deb/unified/latest/scylladb-master/scylla.list`
- For custom repos, you can use the [unified-deb Jenkins job](https://jenkins.scylladb.com/job/releng-testing/job/unified-deb/) to build repository packages if changes are made to boot scripts.

#### AWS
```bash
packer/build_image.sh \
  --target aws \
  --repo downloads.scylladb.com/unstable/scylla/master/deb/unified/latest/scylladb-master/scylla.list \
  --arch x86_64
```

**AWS Credentials**: Configure AWS credentials via environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`) or AWS CLI profile. Optionally customize settings in `packer/ami_variables.json`:
```json
{
  "security_group_id": "sg-xxxxxxxxx",
  "region": "us-east-1",
  "associate_public_ip_address": "true",
  "instance_type": "c4.xlarge"
}
```

#### GCE (Google Cloud)
```bash
packer/build_image.sh \
  --target gce \
  --repo downloads.scylladb.com/unstable/scylla/master/deb/unified/latest/scylladb-master/scylla.list \
  --arch x86_64
```

**GCE Credentials**: Authenticate using `gcloud auth application-default login` or set `GOOGLE_APPLICATION_CREDENTIALS` environment variable. Configure settings in `packer/gce_variables.json`:
```json
{
  "project_id": "your-project-id",
  "region": "us-central1",
  "zone": "us-central1-a",
  "instance_type": "n2-standard-2"
}
```
`build_image.sh` overrides `instance_type` with a default per `--arch` (`n2-standard-2` for x86_64, `c4a-highcpu-2` for aarch64). Pass `--ec2-instance-type` to pick another one.

#### Azure
```bash
packer/build_image.sh \
  --target azure \
  --repo downloads.scylladb.com/unstable/scylla/master/deb/unified/latest/scylladb-master/scylla.list \
  --arch x86_64
```

**Azure Credentials**: Configure Azure credentials in `packer/azure_variables.json`:
```json
{
  "client_id": "your-client-id",
  "client_secret": "your-client-secret",
  "tenant_id": "your-tenant-id",
  "subscription_id": "your-subscription-id",
  "region": "East US",
  "vm_size": "Standard_D4_v4"
}
```

#### OCI (Oracle Cloud Infrastructure)
```bash
packer/build_image.sh \
  --target oci \
  --repo downloads.scylladb.com/unstable/scylla/master/deb/unified/latest/scylladb-master/scylla.list \
  --arch x86_64
```

**OCI Credentials**: Configure OCI CLI with `oci setup config` or provide credentials in `packer/oci_variables.json`. See [packer/oci/OCI_BUILD_GUIDE.md](packer/oci/OCI_BUILD_GUIDE.md) for detailed setup instructions.

### Deploying Clusters

#### AWS CloudFormation
Deploy a Scylla cluster using CloudFormation. The template is a Jinja2 template that must be rendered first:

```bash
# Install jinja2-cli (if not already installed)
pip install jinja2-cli

# Render the template
jinja2 -D arch=x86_64 aws/cloudformation/scylla.yaml.j2 > scylla.yaml

# Deploy the stack
aws cloudformation create-stack \
    --stack-name my-scylla-cluster \
    --template-body file://scylla.yaml \
    --parameters ParameterKey=KeyName,ParameterValue=<your-key> ...
```

See [aws/cloudformation/README.md](aws/cloudformation/README.md) for detailed instructions.

## User-Data Configuration

Scylla machine images support configuration via cloud-init user-data in JSON or YAML format.

See cloud provider documentation for passing user-data:
- **AWS**: [EC2 User Data](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instancedata-add-user-data.html)
- **GCE**: [Startup Scripts](https://cloud.google.com/compute/docs/instances/startup-scripts)
- **Azure**: [Custom Data](https://learn.microsoft.com/en-us/azure/virtual-machines/custom-data)
- **OCI**: [Cloud-Init Scripts](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/launchinginstance.htm)

### Available User-Data Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `scylla_yaml` | object | `{}` | Settings to pass to scylla.yaml (see [Scylla YAML](#scylla-yaml-configuration)) |
| `developer_mode` | boolean | `false` | Enable developer mode |
| `post_configuration_script` | string | `""` | Script to run after configuration (can be base64 encoded) |
| `post_configuration_script_timeout` | integer | `600` | Timeout in seconds for post-configuration script |
| `start_scylla_on_first_boot` | boolean | `true` | Start scylla-server automatically on first boot |
| `tier1_networking` | boolean | `null` (auto-detect) | **GCP only.** Force Tier 1 networking bandwidth (`true`/`false`). When omitted, auto-detected via NIC speed. Only applies to instance types that support Tier 1 (N2/N2D ≥48 vCPUs, Z3). |
| `device_wait_seconds` | integer | `0` | Max seconds to wait for storage devices (recommended: `300`) |
| `io_properties_refresh` | string | `auto` | `auto`, `notify` or `never`, see [I/O properties refresh](#io-properties-refresh) |

### I/O Properties Refresh

`/etc/scylla.d/io_properties.yaml` is written from the I/O preset tables on first boot.
When an upgrade of `scylla-machine-image` ships a corrected preset, the node picks it up
on the package upgrade and again on every boot, before `scylla-server` starts. It never
restarts Scylla and never runs `iotune`: the new values take effect at the next restart
of `scylla-server`, which the rolling upgrade already does.

A file is updated only when it is ours: we recorded writing it, or it holds values one
of our presets wrote (`common/*_io_params_history.yaml`). A file that was edited, has
comments, extra disks or keys, or an `io.conf` changed by hand is left alone; the preset
goes to `io_properties.yaml.new` and the login banner shows the command to use it.

The policy is one of:

- `auto` (default): update files that are ours.
- `notify`: never update, only write the `.new` candidate and the banner notice.
- `never`: do nothing.

Where it is set:

1. **At launch**, with the `io_properties_refresh` user-data option. First boot copies
   it into `/etc/scylla/io_properties_refresh_policy`. User-data is read only then:
   changing it later has no effect.
2. **On a running node**, by editing `/etc/scylla/io_properties_refresh_policy` (one
   word). This file is the only setting the refresh reads; when it is missing, the
   policy is `auto`.

To keep a single node's file without changing the policy, use `--pin` below.

```bash
S=/opt/scylladb/scylla-machine-image/scylla_cloud_io_setup
sudo $S --status            # what a refresh would do, and why
sudo $S --refresh --force   # use the current preset even for a file that is not ours
sudo $S --rollback          # restore the previous file (.bak) and pin it
sudo $S --pin               # keep this file (e.g. after running iotune), hide the notice
sudo $S --unpin             # resume refreshing
```

#### Adding or changing a preset

Nodes are recognised as ours by `common/<cloud>_io_params_history.yaml`, so every preset
change has to reach that file too. A unit test fails when a value in a table is missing
from its history.

1. Add or change the entry in `common/<cloud>_io_params.yaml`. Values are whole-instance,
   see [`tools/io_properties_measurements`](tools/io_properties_measurements/README.md).
2. Regenerate the history, from a full clone with the release branches of this repo and
   of the enterprise repo fetched (older releases' presets are part of it):

   ```bash
   git fetch upstream '+refs/heads/branch-*:refs/remotes/upstream/branch-*'
   git fetch git@github.com:scylladb/scylla-enterprise-machine-image.git \
       '+refs/heads/branch-*:refs/remotes/enterprise/branch-*'
   ./tools/gen_io_params_history.py
   ```

3. Commit the table and the history files together, and backport them together: the
   history is generated on `next` and copied unchanged to release branches.
4. A **new family or size** that was a supported class before its preset existed: nodes
   launched before it hold `iotune` output, and get a notice, not an update.
5. A **new way to look a preset up** (keyed by something other than the instance type,
   like GCP's disk counts or OCI's flex ocpus): give the cloud's `history_keys()` in
   `scylla_cloud_io_setup` the same key, and make `render()` in the tool emit it.

### Scylla YAML Configuration

The `scylla_yaml` field passes settings directly to the Scylla configuration file. See the [official Scylla YAML documentation](https://docs.scylladb.com/operating-scylla/scylla-yaml/) for all available options.

Common settings with machine image defaults:

| Setting | Default | Description |
|---------|---------|-------------|
| `cluster_name` | Auto-generated | Name of the cluster |
| `auto_bootstrap` | `true` | Enable auto-bootstrap |
| `listen_address` | Instance private IP | Address to listen on |
| `broadcast_rpc_address` | Instance private IP | RPC broadcast address |
| `endpoint_snitch` | `Ec2Snitch` (AWS) | Snitch for cloud topology |
| `rpc_address` | `0.0.0.0` | RPC listen address |
| `seed_provider` | Instance private IP | Seed node addresses |

### Example User-Data

#### JSON Format
```json
{
  "scylla_yaml": {
    "cluster_name": "my-cluster",
    "seed_provider": [{
      "class_name": "org.apache.cassandra.locator.SimpleSeedProvider",
      "parameters": [{"seeds": "10.0.1.1,10.0.1.2"}]
    }]
  },
  "post_configuration_script": "#!/bin/bash\necho 'Configuration complete'",
  "start_scylla_on_first_boot": true,
  "device_wait_seconds": 300
}
```

#### YAML Format
```yaml
scylla_yaml:
  cluster_name: my-cluster
  seed_provider:
    - class_name: org.apache.cassandra.locator.SimpleSeedProvider
      parameters:
        - seeds: 10.0.1.1,10.0.1.2
post_configuration_script: |
  #!/bin/bash
  echo 'Configuration complete'
start_scylla_on_first_boot: true
device_wait_seconds: 300
```

#### Using MIME Multipart (Advanced)

For complex scenarios requiring multiple cloud-init features, use MIME multipart format with `x-scylla/json` or `x-scylla/yaml` content type:

```mime
Content-Type: multipart/mixed; boundary="===============BOUNDARY=="
MIME-Version: 1.0

--===============BOUNDARY==
Content-Type: x-scylla/yaml
MIME-Version: 1.0
Content-Disposition: attachment; filename="scylla_config.yaml"

scylla_yaml:
  cluster_name: my-cluster
  seed_provider:
    - class_name: org.apache.cassandra.locator.SimpleSeedProvider
      parameters:
        - seeds: 10.0.1.1
start_scylla_on_first_boot: true

--===============BOUNDARY==
Content-Type: text/cloud-config
MIME-Version: 1.0
Content-Disposition: attachment; filename="cloud-config.txt"

#cloud-config
cloud_final_modules:
- [scripts-user, always]

--===============BOUNDARY==--
```

See [cloud-init documentation](https://cloudinit.readthedocs.io/en/latest/topics/format.html#mime-multi-part-archive) for more details.

## Building Packages

The repository includes scripts to build OS packages (RPM/DEB) that configure Scylla on first boot.

### RedHat/CentOS - RPM
```bash
dist/redhat/build_rpm.sh --target centos7
```

Or using Docker:
```bash
docker run -it -v $PWD:/scylla-machine-image -w /scylla-machine-image --rm centos:7.2.1511 \
  bash -c './dist/redhat/build_rpm.sh -t centos7'
```

### Ubuntu/Debian - DEB
```bash
dist/debian/build_deb.sh
```

Or using Docker:
```bash
docker run -it -v $PWD:/scylla-machine-image -w /scylla-machine-image --rm ubuntu:20.04 \
  bash -c './dist/debian/build_deb.sh'
```

## Development

### Setup
See [SETUP.md](SETUP.md) for detailed development environment setup using uv.

### Running Tests
```bash
make test              # Run all tests (excluding integration)
make test-validation   # Run validation tests only
make test-integration  # Run integration tests (requires AWS credentials)
```

### Code Quality
```bash
make format  # Format code
make lint    # Run linters
make check   # Run all checks
```

## Documentation

- [AWS CloudFormation Deployment](aws/cloudformation/README.md)
- [OCI Build Guide](packer/oci/OCI_BUILD_GUIDE.md)
- [Development Setup](SETUP.md)

## License

Apache-2.0 - See [LICENSE](LICENSE) for details.
