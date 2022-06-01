Scylla AMI user-data Format v3
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

    :property auto_bootstrap: Enable auto bootstrap
    :proptype experimental: boolean
    :options experimental: default='true'
 
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

using json
++++++++++
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

using yaml
++++++++++
.. code-block:: yaml

    scylla_yaml:
      cluster_name: test-cluster
      experimental: true
      seed_provider:
        - class_name: org.apache.cassandra.locator.SimpleSeedProvider
          parameters:
            - seeds: 10.0.219.209
      post_configuration_script: "#! /bin/bash\nyum install cloud-init-cfn"
      start_scylla_on_first_boot: true

using mimemultipart
++++++++++++++++++++

If other feature of cloud-init are needed, one can use mimemultipart, and pass
a json/yaml with `x-scylla/yaml` or `x-scylla/json`

more information on cloud-init multipart user-data:

https://cloudinit.readthedocs.io/en/latest/topics/format.html#mime-multi-part-archive

.. code-block:: mime

    Content-Type: multipart/mixed; boundary="===============5438789820677534874=="
    MIME-Version: 1.0

    --===============5438789820677534874==
    Content-Type: x-scylla/yaml
    MIME-Version: 1.0
    Content-Disposition: attachment; filename="scylla_machine_image.yaml"

    scylla_yaml:
      cluster_name: test-cluster
      experimental: true
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

example of creating the multipart message by python code:

.. code-block:: python
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