## Using cloudformation

### Generating the template

```bash
uv pip install jinja2-cli
jinja2 -D arch=x86_64 ./aws/cloudformation/scylla.yaml.j2 > cfn_scylla.yaml

# check it's correct 
aws cloudformation validate-template --region eu-west-1 --template-body file://cfn_scylla.yaml
```


### Running one example

```bash
sed -i s/placeholder-eu-west-1/ami-0c7e8aa9d71d48f26/ cfn_scylla.yaml 

aws cloudformation create-stack \
    --region eu-west-1 \
    --stack-name ScyllaCFN \
    --template-body file://cfn_scylla.yaml \
    --parameters ParameterKey=KeyName,ParameterValue=scylla_test_id_ed25519 \
                 ParameterKey=PublicAccessCIDR,ParameterValue=0.0.0.0/0 \
                 ParameterKey=ScyllaClusterName,ParameterValue=test \
                 ParameterKey=AvailabilityZone,ParameterValue=eu-west-1a \
                 ParameterKey=EnablePublicAccess,ParameterValue=true \
    --tags Key=Environment,Value=Staging \
           Key=Owner,Value="fruch"
```

