Edit variables.json and run azure/image/build_azure_image.sh, it will build & upload Azure image.

### Azure variables
Create azure/image/variables.json with following format, specify Azure variables:
```
{
  "client_id": "",
  "client_secret": "",
  "tenant_id": "",
  "subscription_id": "",
  "security_group_id": "",
  "region": "",
  "vm_size": ""
}
```

You can use azure/image/variables.json.example as template.

### Build Azure image from locally built rpm
If you want to make Azure with modified scylla source code, this is what you want.
and move all files into `azure/image/files`, to find building instruction for all needed packages see:
https://github.com/scylladb/scylla/blob/master/docs/building-packages.md#scylla-server

To build Azure from locally built rpm, run
```
# build scyll-machine-image (it's not yet part of the repo, since not merge to master yet)
./dist/debian/build_deb.sh
cp build/debian/scylla-machine-image-*.deb ./azure/image/files

SCYLLA_DIR=~/Projects/scylla

cd $SCYLLA_DIR
./SCYLLA-VERSION-GEN
PRODUCT=$(cat build/SCYLLA-PRODUCT-FILE)

cd azure/image


# copy the built scylla RPMs
cp $SCYLLA_DIR/build/dist/<build mode>/debian/*.deb ./files/

./build_with_docker.sh --localrpm --product $PRODUCT --repo-for-update $REPO

```

### Build Azure image locally from deb:

To build Azure locally, you will need all deb. you could find them for example by this link:
http://downloads.scylladb.com/relocatable/unstable/master/2020-12-20T00%3A11%3A59Z/deb/

1. Download all debs to azure/image/files folder

2. Build scylla-machine-image deb file.

3. create file azure/image/variables.json with valid settings. See example variables.json.example
4. then you can build your Azure image:
```
./build_azure_with_docker.sh --localrpm --product scylla
```

### Build Azure from deb repository
To build Azure image from unstable deb repository, run
```
./build_azure_with_docker.sh --repo http://downloads.scylladb.com/unstable/scylla/master/deb/unified/latest/scylladb-master/scylla.list
```
