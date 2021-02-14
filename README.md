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

### <a href="scylla_yaml"></a>Scylla YAML
All fields that would pass down to scylla.yaml configuration file

see [https://docs.scylladb.com/operating-scylla/scylla-yaml/](https://docs.scylladb.com/operating-scylla/scylla-yaml/) for all the possible configuration availble
listed here only the one get defaults scylla AMI

* **Object Properties**    
    * **cluster_name** ([*string*](https://docs.python.org/library/stdtypes.html#str)) – Name of the cluster (*default=`generated name that would work for only one node cluster`*)
    * **experimental** ([*boolean*](https://docs.python.org/library/stdtypes.html#boolean-values)) – To enable all experimental features add to the scylla.yaml (*default=’false’*)
    * **auto_bootstrap** ([*boolean*](https://docs.python.org/library/stdtypes.html#boolean-values)) – Enable auto bootstrap (*default=’true’*)
    * **listen_address** ([*string*](https://docs.python.org/library/stdtypes.html#str)) – Defaults to ec2 instance private ip
    * **broadcast_rpc_address** ([*string*](https://docs.python.org/library/stdtypes.html#str)) – Defaults to ec2 instance private ip
    * **endpoint_snitch** ([*string*](https://docs.python.org/library/stdtypes.html#str)) – Defaults to ‘org.apache.cassandra.locator.Ec2Snitch’
    * **rpc_address** ([*string*](https://docs.python.org/library/stdtypes.html#str)) – Defaults to ‘0.0.0.0’
    * **seed_provider** (*mapping*) – Defaults to ec2 instance private ip

### Example usage of user-data

Spinning a new node connecting to “10.0.219.209” as a seed, and installing cloud-init-cfn package at first boot.

```json
{
     "scylla_yaml": {
         "cluster_name": "test-cluster",
         "experimental": true,
         "seed_provider": [{"class_name": "org.apache.cassandra.locator.SimpleSeedProvider",
                            "parameters": [{"seeds": "10.0.219.209"}]}],
     },
     "post_configuration_script": "#! /bin/bash\nyum install cloud-init-cfn",
     "start_scylla_on_first_boot": true
}
```

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

