#
# Copyright 2020 ScyllaDB
#
# SPDX-License-Identifier: Apache-2.0

import pprint
import logging
import subprocess
import uuid

import pytest
import boto3
from botocore.errorfactory import ClientError


def run_on_node(node_ip, cmd):
    output = subprocess.run(
        [
            "/bin/bash",
            "-c",
            f'ssh -i ~/.ssh/scylla-qa-ec2  -o "UserKnownHostsFile=/dev/null" -o "StrictHostKeyChecking=no" centos@{node_ip} {cmd}',
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    logging.info(output.stderr.decode("utf-8").strip())
    return output.stdout.decode("utf-8").strip()


@pytest.fixture(scope="session")
def cfn_scylla_cluster(request):
    region = request.config.getoption("--region")
    client = boto3.client("cloudformation", region_name=region)
    ec2 = boto3.client('ec2', region_name=region)
    availability_zone = ec2.describe_availability_zones()['AvailabilityZones'][0]['ZoneName']

    if request.config.getoption("--stack-name"):
        name = request.config.getoption("--stack-name")
    else:
        rand_suffix = uuid.uuid4().hex[:6]
        name = f"scylla-cfn-test-{rand_suffix}"

    try:
        response = client.create_stack(
            StackName=name,
            TemplateBody=open("scylla.yaml").read(),
            Parameters=[
                {"ParameterKey": "KeyName", "ParameterValue": "scylla-qa-ec2"},
                {"ParameterKey": "InstanceType", "ParameterValue": "i3.large"},
                {"ParameterKey": "AvailabilityZone", "ParameterValue": availability_zone},
                {"ParameterKey": "ClusterName", "ParameterValue": name},
                {"ParameterKey": "InstanceCount", "ParameterValue": "3"},
                {
                    "ParameterKey": "ScyllaAmi",
                    "ParameterValue": request.config.getoption("--ami"),
                },
            ],
        )
        logging.info(pprint.pformat(response))

    except ClientError as ex:
        logging.info(ex)

    logging.info(f"waiting for cloudformation [{name}] to complete")

    waiter = client.get_waiter("stack_create_complete")
    response = waiter.wait(StackName=name)

    resources = client.list_stack_resources(StackName=name)

    outputs = client.describe_stacks(StackName=name)
    logging.info(pprint.pformat(outputs))
    outputs = {
        item["OutputKey"]: item["OutputValue"]
        for item in outputs["Stacks"][0]["Outputs"]
    }

    yield resources, outputs

    if not request.session.testsfailed and not request.config.getoption("--keep-cfn"):
        response = client.delete_stack(StackName=name)
        logging.info(response)

        waiter = client.get_waiter("stack_delete_complete")
        response = waiter.wait(StackName=name)
        logging.info(response)


def wait_nodes_ready(resources, region):
    ec2 = boto3.client("ec2", region_name=region)
    ok_waiter = ec2.get_waiter("instance_status_ok")

    instances = [
        r["PhysicalResourceId"]
        for r in resources["StackResourceSummaries"]
        if r["ResourceType"] == "AWS::EC2::Instance"
    ]

    logging.debug(pprint.pformat(instances))

    response = ok_waiter.wait(InstanceIds=instances)
    logging.info(pprint.pformat(response))


def test_cluster_up(request, cfn_scylla_cluster):
    resources, outputs = cfn_scylla_cluster
    region = request.config.getoption("--region")
    wait_nodes_ready(resources, region)


def test_connect(cfn_scylla_cluster):
    resources, outputs = cfn_scylla_cluster
    nodes_ip_addresses = [v for k, v in outputs.items() if "PublicIp" in k]

    for node_ip in nodes_ip_addresses:
        logging.info(f"connecting to node {node_ip}")
        output = run_on_node(node_ip, "scylla --version")
        logging.info(output)


def test_nodetool_status(cfn_scylla_cluster):
    resources, outputs = cfn_scylla_cluster
    nodes_ip_addresses = [v for k, v in outputs.items() if "PublicIp" in k]
    node_private_ips = [v for k, v in outputs.items() if "PrivateIp" in k]

    for node_ip in nodes_ip_addresses:
        logging.info(f"running nodetool on node {node_ip}")
        output = run_on_node(node_ip, "nodetool status")
        logging.info(output)
        for private_ip in node_private_ips:
            assert f"UN  {private_ip}" in output


def test_cassandra_stress(cfn_scylla_cluster):
    resources, outputs = cfn_scylla_cluster
    nodes_ip_addresses = [v for k, v in outputs.items() if "PublicIp" in k]

    for node_ip in nodes_ip_addresses:
        logging.info(f"running c-s to node {node_ip}")
        output = run_on_node(
            node_ip, "cassandra-stress write n=40000 -rate threads=40 -node 172.31.0.11"
        )
        logging.info(output)
        assert "Total errors              :          0 [WRITE: 0]" in output
