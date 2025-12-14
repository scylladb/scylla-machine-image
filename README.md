# Scylla Machine Image
Provides following
- Create an image with pre-installed Scylla
- Allow to configure the database when an instance is launched first time
- Easy cluster creation

## OS Package
RPM/DEB package that is pre-installed in the image.
Responsible for configuring Scylla during first boot of the instance.

## Create an image

### AWS
```shell script
aws/ami/build_ami.sh
```

### GCE (Google Cloud)
```shell script
packer/build_image.sh --target gce ...
```

### Azure
```shell script
packer/build_image.sh --target azure ...
```

### OCI (Oracle Cloud Infrastructure)
```shell script
packer/build_image.sh --target oci ...
```

## Scylla AMI user-data Format v2

Scylla AMI user-data should be passed as a json object, as described below

see AWS docs for how to pass user-data into ec2 instances:
[https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instancedata-add-user-data.html](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instancedata-add-user-data.html)
---
### EC2 User-Data
User Data that can pass when create EC2 instances

* **Object Properties**
    * **scylla_yaml** ([`Scylla YAML`](#scylla_yaml)) – Mapping of all fields that would pass down to scylla.yaml configuration file
    * **scylla_startup_args** (*list*) – embedded information about the user that created the issue (NOT YET IMPLEMENTED) (*default=’[]’*)
    * **developer_mode** ([*boolean*](https://docs.python.org/library/stdtypes.html#boolean-values)) – Enables developer mode (*default=’false’*)
    * **post_configuration_script** ([*string*](https://docs.python.org/library/stdtypes.html#str)) – A script to run once AMI first configuration is finished, can be a string encoded in base64. (*default=’’*)
    * **post_configuration_script_timeout** ([*int*](https://docs.python.org/library/stdtypes.html#int)) – Time in seconds to limit the post_configuration_script (*default=’600’*)
    * **start_scylla_on_first_boot** ([*boolean*](https://docs.python.org/library/stdtypes.html#boolean-values)) – If true, scylla-server would boot at AMI boot (*default=’true’*)
    * **device_wait_seconds** ([*int*](https://docs.python.org/library/stdtypes.html#int)) – Maximum seconds to wait for storage devices to appear before configuring RAID. Useful in cloud environments where device attachment may be delayed (*default=’0’*)

### <a href="scylla_yaml"></a>Scylla YAML
All fields that would pass down to scylla.yaml configuration file

see [https://docs.scylladb.com/operating-scylla/scylla-yaml/](https://docs.scylladb.com/operating-scylla/scylla-yaml/) for all the possible configuration availble
listed here only the one get defaults scylla AMI

* **Object Properties**    
    * **cluster_name** ([*string*](https://docs.python.org/library/stdtypes.html#str)) – Name of the cluster (*default=`generated name that would work for only one node cluster`*)
    * **auto_bootstrap** ([*boolean*](https://docs.python.org/library/stdtypes.html#boolean-values)) – Enable auto bootstrap (*default=’true’*)
    * **listen_address** ([*string*](https://docs.python.org/library/stdtypes.html#str)) – Defaults to ec2 instance private ip
    * **broadcast_rpc_address** ([*string*](https://docs.python.org/library/stdtypes.html#str)) – Defaults to ec2 instance private ip
    * **endpoint_snitch** ([*string*](https://docs.python.org/library/stdtypes.html#str)) – Defaults to ‘org.apache.cassandra.locator.Ec2Snitch’
    * **rpc_address** ([*string*](https://docs.python.org/library/stdtypes.html#str)) – Defaults to ‘0.0.0.0’
    * **seed_provider** (*mapping*) – Defaults to ec2 instance private ip

### Example usage of user-data

Spinning a new node connecting to “10.0.219.209” as a seed, and installing cloud-init-cfn package at first boot.

#### using json
```json
{
     "scylla_yaml": {
         "cluster_name": "test-cluster",
         "seed_provider": [{"class_name": "org.apache.cassandra.locator.SimpleSeedProvider",
                            "parameters": [{"seeds": "10.0.219.209"}]}],
     },
     "post_configuration_script": "#! /bin/bash\nyum install cloud-init-cfn",
     "start_scylla_on_first_boot": true
}
```

#### using yaml
```yaml
scylla_yaml:
  cluster_name: test-cluster
  seed_provider:
    - class_name: org.apache.cassandra.locator.SimpleSeedProvider
      parameters:
        - seeds: 10.0.219.209
post_configuration_script: "#! /bin/bash\nyum install cloud-init-cfn"
start_scylla_on_first_boot: true
```

#### using mimemultipart

If other feature of cloud-init are needed, one can use mimemultipart, and pass
a json/yaml with `x-scylla/yaml` or `x-scylla/json`

more information on cloud-init multipart user-data:

https://cloudinit.readthedocs.io/en/latest/topics/format.html#mime-multi-part-archive

```mime
Content-Type: multipart/mixed; boundary="===============5438789820677534874=="
MIME-Version: 1.0

--===============5438789820677534874==
Content-Type: x-scylla/yaml
MIME-Version: 1.0
Content-Disposition: attachment; filename="scylla_machine_image.yaml"

scylla_yaml:
  cluster_name: test-cluster
  seed_provider:
    - class_name: org.apache.cassandra.locator.SimpleSeedProvider
      parameters:
        - seeds: 10.0.219.209
post_configuration_script: "#! /bin/bash\nyum install cloud-init-cfn"
start_scylla_on_first_boot: true

--===============5438789820677534874==
Content-Type: text/cloud-config; charset="us-ascii"
MIME-Version: 1.0
Content-Transfer-Encoding: 7bit
Content-Disposition: attachment; filename="cloud-config.txt"

#cloud-config
cloud_final_modules:
- [scripts-user, always]

--===============5438789820677534874==--
```

example of creating the multipart message by python code:

```python
import json
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart

msg = MIMEMultipart()

scylla_image_configuration = dict(
    scylla_yaml=dict(
        cluster_name="test_cluster",
        listen_address="10.23.20.1",
        broadcast_rpc_address="10.23.20.1",
        seed_provider=[{
            "class_name": "org.apache.cassandra.locator.SimpleSeedProvider",
            "parameters": [{"seeds": "10.23.20.1"}]}],
    )
)
part = MIMEBase('x-scylla', 'json')
part.set_payload(json.dumps(scylla_image_configuration, indent=4, sort_keys=True))
part.add_header('Content-Disposition', 'attachment; filename="scylla_machine_image.json"')
msg.attach(part)

cloud_config = """
#cloud-config
cloud_final_modules:
- [scripts-user, always]
"""
part = MIMEBase('text', 'cloud-config')
part.set_payload(cloud_config)
part.add_header('Content-Disposition', 'attachment; filename="cloud-config.txt"')
msg.attach(part)

print(msg)
```

## Device Wait Mechanism

The `wait_for_devices` functionality provides resilience when configuring storage in cloud environments where block device attachment may be delayed. This is particularly important during instance initialization when storage devices might not be immediately available.

### How It Works

The `scylla_create_devices` script includes a `wait_for_devices` function that:

1. **Polls for device availability** - Repeatedly checks for storage devices at 5-second intervals
2. **Waits up to a configurable timeout** - Controlled by the `device_wait_seconds` parameter
3. **Returns immediately when devices are found** - Stops waiting as soon as any device appears
4. **Provides logging** - Outputs status messages during the wait process

### Configuration

You can configure the wait timeout via user-data:

```json
{
    "device_wait_seconds": 300,
    "scylla_yaml": {
        "cluster_name": "my-cluster"
    }
}
```

Or in YAML format:

```yaml
device_wait_seconds: 300
scylla_yaml:
  cluster_name: my-cluster
```

**Default**: `0` (no waiting - devices must be available immediately)
**Recommended for cloud environments**: `300` seconds (5 minutes)

### Use Cases

- **OCI (Oracle Cloud Infrastructure)** - Block volume attachment can't be delayed during instance launch
- **AWS with EBS volumes** - Attached volumes may take time to appear
- **Azure with managed disks** - Disk attachment timing can vary
- **GCE with persistent disks** - Similar attachment delays may occur

## Creating a Scylla cluster using the Machine Image
### AWS - CloudFormation
Use template `aws/cloudformation/scylla.yaml`.
Currently, maximum 10 nodes cluster is supported.

## Building scylla-machine-image package

### RedHat like - RPM

Currently the only supported mode is:

```
dist/redhat/build_rpm.sh --target centos7 --cloud-provider aws
```

Build using Docker

```
docker run -it -v $PWD:/scylla-machine-image -w /scylla-machine-image  --rm centos:7.2.1511 bash -c './dist/redhat/build_rpm.sh -t centos7 -c aws'
```

### Ubuntu - DEB

```
dist/debian/build_deb.sh
```

Build using Docker

```
docker run -it -v $PWD:/scylla-machine-image -w /scylla-machine-image  --rm ubuntu:20.04 bash -c './dist/debian/build_deb.sh'
```

## Building docs

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install sphinx sphinx-jsondomain sphinx-markdown-builder
make html
make markdown
```

