Scylla AMI user-data Format v2
==============================

Scylla AMI user-data should be passed as a json object, as described below 

see AWS docs for how to pass user-data into ec2 instances:
https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instancedata-add-user-data.html

-----

.. json:object:: EC2 User-Data

    User Data that can pass when create EC2 instances

    :property scylla_yaml: Mapping of all fields that would pass down to scylla.yaml configuration file
    :proptype scylla_yaml: :json:object:`Scylla YAML`
    
    :property scylla_startup_args: embedded information about the user that created the issue (NOT YET IMPLEMENTED)
    :proptype scylla_startup_args: list
    :options scylla_startup_args: default='[]'
    
    :property developer_mode: Enables developer mode
    :proptype developer_mode: boolean
    :options developer_mode: default='false'

    :property post_configuration_script: A script to run once AMI first configuration is finished, can be a string encoded in base64.
    :proptype post_configuration_script: string
    :options post_configuration_script: default=''
    
    :property post_configuration_script_timeout: Time in secoands to limit the `post_configuration_script` 
    :proptype post_configuration_script_timeout: int    
    :options post_configuration_script_timeout: default='600'
    
    :property start_scylla_on_first_boot: If true, scylla-server would boot at AMI boot
    :proptype start_scylla_on_first_boot: boolean    
    :options start_scylla_on_first_boot: default='true'   


.. json:object:: Scylla YAML

    All fields that would pass down to scylla.yaml configuration file
    see https://docs.scylladb.com/operating-scylla/scylla-yaml/ for all the possible configuration available
    listed here only the one get defaults scylla AMI

    :property cluster_name: Name of the cluster
    :proptype cluster_name: string
    :options cluster_name: default=[generated name that would work for only one node cluster]
    
    :property experimental: To enable all experimental features add to the scylla.yaml
    :proptype experimental: boolean
    :options experimental: default='false'
 
    :property listen_address: Defaults to ec2 instance private ip
    :proptype listen_address: string

    :property broadcast_rpc_address: Defaults to ec2 instance private ip
    :proptype broadcast_rpc_address: string

    :property endpoint_snitch: Defaults to 'org.apache.cassandra.locator.Ec2Snitch'
    :proptype endpoint_snitch: string

    :property rpc_address: Defaults to '0.0.0.0'
    :proptype rpc_address: string

    :property seed_provider: Defaults to ec2 instance private ip
    :proptype seed_provider: mapping



Example
-------

Spinning a new node connecting to "10.0.219.209" as a seed, and installing cloud-init-cfn package at first boot.

.. code-block:: json

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

