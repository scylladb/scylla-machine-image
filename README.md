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
| `device_wait_seconds` | integer | `0` | Max seconds to wait for storage devices (recommended: `300`) |

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
