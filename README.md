# scylla-cloud-image

## build

### RedHat like

Currently the only supported mode is:

```
dist/redhat/build_rpm.sh --target centos7 --cloud-provider aws
```

Build using Docker

```
docker run -it -v $PWD:/scylla-cloud-image -w /scylla-cloud-image  --rm centos:7.2.1511 bash -c './dist/redhat/build_rpm.sh -t centos7 -c aws'
```