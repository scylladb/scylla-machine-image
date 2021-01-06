Edit variables.json and run aws/ami/build_ami.sh, it will build & upload AMI.

When deploying the AMI, be sure to add two more disks(sdb, sdc) to construct RAID.

### Prerequire
AMI build script only works on distrubution with docker installed.

### AMI variables
Create dist/ami/variables.json with following format, specify AMI variables:
```
{
    "access_key": "xxx",
    "secret_key": "xxx",
    "subnet_id": "subnet-xxx",
    "security_group_id": "sg-xxx",
    "region": "us-east-1",
    "associate_public_ip_address": "true",
    "instance_type": "c4.xlarge",
    "ami_prefix": "YOUR_NAME-"
}
```

You can use dist/ami/variables.json.example as template.

### Build AMI from locally built rpm
If you want to make AMI with modified scylla source code, this is what you want.
and move all files into `aws/ami/files`, to find building instruction for all needed packages see:
https://github.com/scylladb/scylla/blob/master/docs/building-packages.md#scylla-server

To build AMI from locally built rpm, run
```
# build scyll-machine-image (it's not yet part of the repo, since not merge to master yet)
./dist/redhat/build_rpm.sh -t centos -c aws
cp build/RPMS/noarch/scylla-machine-image-*.rpm ./aws/ami/files/

SCYLLA_DIR=~/Projects/scylla

cd $SCYLLA_DIR
./SCYLLA-VERSION-GEN
PRODUCT=$(cat build/SCYLLA-PRODUCT-FILE)
REPO=`./scripts/scylla_current_repo --target centos`

cd ./aws/ami

# optionaly: download all other rpms except scylla-server from the repo
./build_with_docker.sh --product $PRODUCT --repo $REPO --download-no-server

# copy the built scylla RPMs
cp $SCYLLA_DIR/build/redhat/RPMS/x86_64/*.rpm ./files/

./build_with_docker.sh --localrpm --product $PRODUCT --repo-for-update $REPO

```

### Build AMI locally from rpms:

To build ami locally, you will need all rpms. you could find them for example by this link:
http://downloads.scylladb.com/relocatable/unstable/master/2020-12-20T00%3A11%3A59Z/rpm/

1. Download all rpms to aws/ami/files folder

2. Build scylla-machine-image rpm file. If you use the redhat os, the you could
use instructions above. If you use debian like os, then you could use docker
_commit and push your changes_

```
cd scylla-machine-image
docker run -v `pwd`:/scylla-machine-image scylladb/packer-builder /bin/bash -c "cd /scylla-machine-image; ./dist/redhat/build_rpm.sh -t centos"
cp build/RPMS/noarch/scylla-machine-image-*.rpm ./aws/ami/files/
```

3. create file aws/ami/variables.json with valid settings. See example variables.json.example
4. then you can build your ami:
```
./build_with_docker.sh --localrpm --product scylla
```

5. At the end you will get the ami id in us-east-1 region.

### Build AMI from yum repository
To build AMI from unstable yum repository, run
```
./build_with_docker.sh --repo http://downloads.scylladb.com.s3.amazonaws.com/rpm/unstable/centos/master/latest/scylla.repo
```
