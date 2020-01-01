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
SCYLLA_DIR=~/Projects/scylla

cd $SCYLLA_DIR
./SCYLLA-VERSION-GEN
PRODUCT=$(cat build/SCYLLA-PRODUCT-FILE)
REPO=`./scripts/scylla_current_repo --target centos`

cd - 
./build_with_docker.sh --localrpm --product $PRODUCT --repo-for-update $REPO
```

### Build AMI from yum repository
To build AMI from unstable yum repository, run
```
./build_with_docker.sh --repo http://downloads.scylladb.com.s3.amazonaws.com/rpm/unstable/centos/master/latest/scylla.repo
```


### Build AMI with personal branch

#### Method 1

```
sudo rm -rf scylla_new
git clone https://github.com/scylladb/scylla scylla_new
cd scylla_new

## different branch uses different submodules, first checkout to right scylla branch (same as your branch)
git checkout -b branch-1.5 remotes/origin/branch-1.5

## init the submoudles
git submodule init

## checkout your own branch
git remote add glommer git@github.com:glommer/scylla.git
git fetch glommer
git checkout -b dev_branch remotes/glommer/for-amos-1.5-preview

## update submodules (which is using the right url)
git submodule update --init --recursive

sudo ./dist/ami/build_ami.sh --localrpm

```

#### Method 2

```
sudo rm -rf scylla_new
git clone https://github.com/scylladb/scylla scylla_new
cd scylla_new
git submodule init
git submodule update --init --recursive

## add scylla-seastar of upstream
cd seastar
git remote add scylla-seastar  git@github.com:scylladb/scylla-seastar.git
git fetch scylla-seastar
cd -

git remote add glommer git@github.com:glommer/scylla.git
git fetch glommer
git checkout -b dev_branch remotes/glommer/for-amos-1.5-preview

## reupdate submodules
git submodule update --init --recursive

sudo ./dist/ami/build_ami.sh --localrpm
```
