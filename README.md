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

## Configure ScyllaDB
When an instance is created using Scylla Machine Image we can configure it during first boot.
We do this via user-data (AWS)
### User data format
The format is JSON and allows us:
- set any `scylla.yaml` paramater
- run a script after the configuration is done
- control whether to start Scylla after the first boot

Defaults are:
```json
{
    'scylla_yaml': {
        'cluster_name': "scylladb-cluster-<LINUX time now>",
        'experimental': false,
        'auto_bootstrap': true,
        'listen_address': "<a private IP of the instance>",
        'broadcast_rpc_address': "<a private IP of the instance>",
        'endpoint_snitch': "org.apache.cassandra.locator.Ec2Snitch",
        'rpc_address': "0.0.0.0",
        'seed_provider': [{'class_name': 'org.apache.cassandra.locator.SimpleSeedProvider',
                           'parameters': [{'seeds': "<a private IP of the instance>"}]}],
    },
    'developer_mode': false,
    'post_configuration_script': '',
    'post_configuration_script_timeout': 600,
    'start_scylla_on_first_boot': true
}
```

- `scylla_yaml` - same params that are supported in `scylla.yaml` but in JSON format
- `developer_mode` - will set Scylla in developer mode
- `post_configuration_script` -  base64 encoded bash script that will be executed after the configuraiton is done
- `post_configuration_script_timeout` - maximum run time for the `post_configuration_script` in seconds
- `start_scylla_on_first_boot` - whether to start Scylla after the configuration is finished

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

## Building docs

```bash
python3 -m .venv
source .venv/bin/activate
pip install sphinx sphinx-jsondomain
make html
```

TODO: upload to gh-pages
