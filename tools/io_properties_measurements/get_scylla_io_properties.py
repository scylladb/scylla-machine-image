#!/usr/bin/env python3
"""
Script to launch an AWS EC2 instance with given AMI and instance type,
then retrieve and parse values from /etc/scylla.d/io_properties.yaml

Features:
- Supports single instances or entire instance families
- Uses Paramiko SSH client for connection (no system SSH fallback)
- Automatic instance tagging for better identification
- Robust error handling with automatic instance termination
- Configurable timeout values for different instance sizes
"""

import argparse
import concurrent.futures
import os
import re
import stat
import sys
import time

import boto3
import paramiko
import yaml


try:
    from tabulate import tabulate

    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False
from botocore.exceptions import ClientError


def get_wait_times(instance_type):
    """
    Get appropriate wait times based on instance size
    Returns (initial_wait_seconds, io_properties_wait_minutes)
    """
    # Extract size from instance type (e.g., 'large', '2xlarge', '48xlarge')
    size_part = instance_type.split(".")[-1] if instance_type.split(".")[-1] else "large"

    # Size-based wait times
    size_multipliers = {
        "large": 1,
        "xlarge": 1.2,
        "2xlarge": 1.5,
        "3xlarge": 1.8,
        "4xlarge": 2,
        "6xlarge": 2.5,
        "8xlarge": 3,
        "12xlarge": 3.5,
        "16xlarge": 4,
        "18xlarge": 4.5,
        "24xlarge": 5,
        "32xlarge": 6,
        "48xlarge": 7,
    }

    multiplier = size_multipliers.get(size_part, 1)

    # Base wait times - increased for all instance types
    base_initial_wait = 30  # seconds (increased from 10)
    base_io_wait = 30  # minutes (increased from 15)

    initial_wait = int(base_initial_wait * multiplier)
    io_wait_minutes = int(base_io_wait * multiplier)

    return initial_wait, io_wait_minutes


def launch_instance(ami_id, instance_type, key_name, security_group, subnet_id=None, region="us-east-1"):
    """
    Launch an EC2 instance
    """
    ec2 = boto3.resource("ec2", region_name=region)
    ec2_client = boto3.client("ec2", region_name=region)

    # Determine if security_group is an ID or name
    if security_group.startswith("sg-"):
        # It's an ID
        kwargs = {
            "ImageId": ami_id,
            "MinCount": 1,
            "MaxCount": 1,
            "InstanceType": instance_type,
            "KeyName": key_name,
            "SecurityGroupIds": [security_group],
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name", "Value": f"io_properties_setup_{instance_type}"},
                        {"Key": "keep", "Value": "1"},  # Add keep=1 tag for auto-termination after 1 hour
                    ],
                }
            ],
        }
        sg_id = security_group
    else:
        # It's a name
        kwargs = {
            "ImageId": ami_id,
            "MinCount": 1,
            "MaxCount": 1,
            "InstanceType": instance_type,
            "KeyName": key_name,
            "SecurityGroups": [security_group],
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name", "Value": f"io_properties_setup_{instance_type}"},
                        {"Key": "keep", "Value": "1"},  # Add keep=1 tag for auto-termination after 1 hour
                    ],
                }
            ],
        }
        # Find the security group ID
        sg_response = ec2_client.describe_security_groups(GroupNames=[security_group])
        sg_id = sg_response["SecurityGroups"][0]["GroupId"]

    # If no subnet specified, find one in the same VPC as the security group
    # that supports the requested instance type
    if not subnet_id:
        sg_details = ec2_client.describe_security_groups(GroupIds=[sg_id])
        vpc_id = sg_details["SecurityGroups"][0]["VpcId"]

        # Find availability zones that support the instance type
        # Get all availability zones in the region
        azs = ec2_client.describe_availability_zones(
            Filters=[{"Name": "region-name", "Values": [region]}], AllAvailabilityZones=False
        )
        az_names = [az["ZoneName"] for az in azs["AvailabilityZones"]]
        az_response = ec2_client.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[
                {"Name": "instance-type", "Values": [instance_type]},
                {"Name": "location", "Values": az_names},  # All AZs in the specified region
            ],
        )

        supported_azs = [offering["Location"] for offering in az_response["InstanceTypeOfferings"]]
        print(f"Instance type {instance_type} is supported in AZs: {supported_azs}")

        # Find subnets in this VPC that are in supported AZs
        subnet_response = ec2_client.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}, {"Name": "availability-zone", "Values": supported_azs}]
        )

        if subnet_response["Subnets"]:
            # Use the first available subnet in a supported AZ
            subnet_id = subnet_response["Subnets"][0]["SubnetId"]
            az = subnet_response["Subnets"][0]["AvailabilityZone"]
            print(f"Using subnet {subnet_id} in AZ {az} (VPC {vpc_id})")
        else:
            raise Exception(
                f"No subnets found in VPC {vpc_id} for security group {security_group} in supported availability zones"
            )

    # Add subnet if specified or found
    if subnet_id:
        kwargs["SubnetId"] = subnet_id

    instances = ec2.create_instances(**kwargs)
    instance = instances[0]
    print(f"Launched instance {instance.id}")
    return instance


def wait_for_instance(instance):
    """
    Wait for instance to be running
    """
    print("Waiting for instance to be running...")
    instance.wait_until_running()
    instance.reload()
    print(f"Instance is running at {instance.public_ip_address}")

    # Get appropriate wait time based on instance size
    initial_wait, _ = get_wait_times(instance.instance_type)
    print(f"Waiting {initial_wait} seconds for services to start...")
    time.sleep(initial_wait)


def get_io_properties(instance, key_path, username="scyllaadm", override=False):
    """
    SSH into instance using Paramiko SSH client and get IO properties

    Args:
        instance: EC2 instance object
        key_path: Path to SSH private key
        username: SSH username (default: scyllaadm)
        override: If True, force re-running scylla_io_setup by removing existing file
    """
    # First, validate the key file exists
    if not os.path.exists(key_path):
        raise Exception(f"SSH key file not found: {key_path}")

    # Check if key file has appropriate permissions on Unix-like systems
    if os.name != "nt":  # Not Windows
        key_permissions = os.stat(key_path).st_mode
        if key_permissions & stat.S_IRWXG or key_permissions & stat.S_IRWXO:
            print("Warning: SSH key file has group/other permissions. Fixing permissions to 0600.")
            os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # Retry SSH connection up to 5 times
    last_error = None
    for attempt in range(5):
        try:
            print(
                f"Attempting SSH connection (attempt {attempt + 1}/5) to {instance.public_ip_address} as {username}..."
            )
            ssh.connect(
                hostname=instance.public_ip_address,
                username=username,
                key_filename=key_path,
                timeout=60,
                allow_agent=False,  # Don't use the SSH agent
                look_for_keys=False,  # Don't look for keys in ~/.ssh
            )
            print("SSH connection established successfully")
            break
        except paramiko.ssh_exception.AuthenticationException as auth_err:
            last_error = f"Authentication failed: {auth_err} (check key and username)"
            print(f"SSH authentication error: {last_error}")
            if attempt < 4:
                print("Retrying in 30 seconds...")
                time.sleep(30)
            else:
                raise Exception(f"Failed to authenticate via SSH after 5 attempts: {last_error}")
        except paramiko.ssh_exception.NoValidConnectionsError as conn_err:
            last_error = f"Connection error: {conn_err} (check if port 22 is open in security group)"
            print(f"SSH connection error: {last_error}")
            if attempt < 4:
                print("Retrying in 30 seconds...")
                time.sleep(30)
            else:
                raise Exception(f"Failed to establish SSH connection after 5 attempts: {last_error}")
        except Exception as e:
            last_error = str(e)
            print(f"SSH connection failed: {last_error}")
            if attempt < 4:
                print("Retrying in 30 seconds...")
                time.sleep(30)
            else:
                raise Exception(f"Failed to connect via SSH after 5 attempts: {last_error}")

    # Handle override flag: remove existing io_properties.yaml and trigger scylla_io_setup
    if override:
        print("Override flag detected - removing existing IO properties file and running scylla_io_setup...")
        try:
            # Remove existing io_properties.yaml file if it exists
            stdin, stdout, stderr = ssh.exec_command("sudo rm -f /etc/scylla.d/io_properties.yaml")
            stdout.read()  # Wait for command to complete

            # Run scylla_io_setup to regenerate the file
            print("Running scylla_io_setup to generate new IO properties...")
            stdin, stdout, stderr = ssh.exec_command("sudo scylla_io_setup")
            output = stdout.read().decode()
            error_output = stderr.read().decode()

            if error_output:
                print(f"scylla_io_setup stderr: {error_output}")
            if output:
                print(f"scylla_io_setup output: {output}")

            print("scylla_io_setup completed successfully")
        except Exception as e:
            print(f"Warning: Error during override setup: {e}")

    # Get appropriate wait time for IO properties file
    _, io_wait_minutes = get_wait_times(instance.instance_type)
    max_attempts = io_wait_minutes * 6  # 6 attempts per minute (every 10 seconds)

    print(f"Waiting for IO properties file (up to {io_wait_minutes} minutes for {instance.instance_type})...")

    # Wait for the file to be available
    try:
        for i in range(max_attempts):
            stdin, stdout, stderr = ssh.exec_command('test -f /etc/scylla.d/io_properties.yaml && echo "exists"')
            if stdout.read().decode().strip() == "exists":
                break
            if i % 6 == 0:  # Print progress every minute
                minutes_elapsed = i // 6
                print(f"  Still waiting... ({minutes_elapsed}/{io_wait_minutes} minutes)")
            time.sleep(10)
        else:
            raise Exception(
                f"IO properties file not found after {io_wait_minutes} minutes for {instance.instance_type}"
            )

        print("IO properties file found!")

        # Read the file
        stdin, stdout, stderr = ssh.exec_command("cat /etc/scylla.d/io_properties.yaml")
        content = stdout.read().decode()

        return yaml.safe_load(content)
    finally:
        # Ensure we always close the SSH connection
        ssh.close()


def get_instance_types_in_family(family_prefix, ec2_client):
    """
    Get all available instance types for a given family (e.g., 'i8g' -> ['i8g.large', 'i8g.xlarge', ...])
    """
    try:
        # Get all instance types by paginating through all results
        all_instances = []
        response = ec2_client.describe_instance_types()
        all_instances.extend(response["InstanceTypes"])

        while "NextToken" in response:
            response = ec2_client.describe_instance_types(NextToken=response["NextToken"])
            all_instances.extend(response["InstanceTypes"])

        # Filter for instances that exactly match the family
        instance_types = []
        for it in all_instances:
            type_name = it["InstanceType"]
            # Only include instances that exactly match the family (e.g., i8g.large, not i8ge.large)
            if type_name.startswith(f"{family_prefix}.") and "metal" not in type_name:
                instance_types.append(type_name)

        # Sort by size (large, xlarge, 2xlarge, etc.)
        size_order = [
            "large",
            "xlarge",
            "2xlarge",
            "4xlarge",
            "8xlarge",
            "12xlarge",
            "16xlarge",
            "24xlarge",
            "32xlarge",
            "48xlarge",
        ]
        instance_types.sort(key=lambda x: size_order.index(x.split(".")[-1]) if x.split(".")[-1] in size_order else 999)

        return instance_types

    except Exception as e:
        print(f"Error querying instance types for family {family_prefix}: {e}")
        return []


def launch_instance_for_family(ami_id, instance_type, key_name, security_group, subnet_id, ec2_client):
    """
    Launch a single instance for a specific instance type
    """
    ec2 = boto3.resource("ec2", region_name=ec2_client.meta.region_name)

    # Determine if security_group is an ID or name
    if security_group.startswith("sg-"):
        kwargs = {
            "ImageId": ami_id,
            "MinCount": 1,
            "MaxCount": 1,
            "InstanceType": instance_type,
            "KeyName": key_name,
            "SecurityGroupIds": [security_group],
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name", "Value": f"io_properties_setup_{instance_type}"},
                        {"Key": "keep", "Value": "1"},  # Add keep=1 tag for auto-termination after 1 hour
                    ],
                }
            ],
        }
        sg_id = security_group
    else:
        kwargs = {
            "ImageId": ami_id,
            "MinCount": 1,
            "MaxCount": 1,
            "InstanceType": instance_type,
            "KeyName": key_name,
            "SecurityGroups": [security_group],
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name", "Value": f"io_properties_setup_{instance_type}"},
                        {"Key": "keep", "Value": "1"},  # Add keep=1 tag for auto-termination after 1 hour
                    ],
                }
            ],
        }
        sg_response = ec2_client.describe_security_groups(GroupNames=[security_group])
        sg_id = sg_response["SecurityGroups"][0]["GroupId"]

    # If no subnet specified, find one in the same VPC as the security group
    if not subnet_id:
        sg_details = ec2_client.describe_security_groups(GroupIds=[sg_id])
        vpc_id = sg_details["SecurityGroups"][0]["VpcId"]

        # Find availability zones that support the instance type
        az_response = ec2_client.describe_instance_type_offerings(
            LocationType="availability-zone", Filters=[{"Name": "instance-type", "Values": [instance_type]}]
        )

        supported_azs = [offering["Location"] for offering in az_response["InstanceTypeOfferings"]]

        # Find subnets in this VPC that are in supported AZs
        subnet_response = ec2_client.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}, {"Name": "availability-zone", "Values": supported_azs}]
        )

        if subnet_response["Subnets"]:
            subnet_id = subnet_response["Subnets"][0]["SubnetId"]
            az = subnet_response["Subnets"][0]["AvailabilityZone"]
            print(f"Using subnet {subnet_id} in AZ {az} for {instance_type}")

    # Add subnet if specified or found
    if subnet_id:
        kwargs["SubnetId"] = subnet_id

    instances = ec2.create_instances(**kwargs)
    instance = instances[0]
    print(f"Launched {instance_type} instance: {instance.id}")
    return instance


def format_output(properties, instance_type=""):
    """
    Format IO properties output as human-readable table
    """
    return format_as_table(properties, instance_type)


def format_as_table(properties, instance_type):
    """
    Format properties as a table
    """
    if not properties:
        return f"No properties found for {instance_type}"

    rows = []

    def flatten_dict(d, prefix=""):
        """Recursively flatten nested dictionaries and lists"""
        for key, value in d.items():
            full_key = f"{prefix}.{key}" if prefix else key

            if isinstance(value, dict):
                flatten_dict(value, full_key)
            elif isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
                # Handle list of dictionaries (like 'disks' array)
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        item_prefix = f"{full_key}[{i}]" if len(value) > 1 else full_key
                        flatten_dict(item, item_prefix)
            else:
                rows.append([full_key, str(value)])

    flatten_dict(properties)

    if HAS_TABULATE:
        return tabulate(rows, headers=["Property", "Value"], tablefmt="grid")
    # Simple table format without tabulate
    output = f"IO Properties for {instance_type}:\n"
    output += "-" * 50 + "\n"
    for row in rows:
        output += f"{row[0]:<30} | {row[1]}\n"
    return output


def process_instance_parallel(
    instance_type, ami_id, key_name, security_group, subnet_id, key_path, username, region, override=False
):
    """
    Process a single instance (for parallel execution)
    Ensures proper instance cleanup even when errors occur
    """
    instance = None  # Initialize instance variable to ensure it's in scope for error handler
    start_time = time.time()

    try:
        print(f"Starting processing of {instance_type}...")
        ec2_client = boto3.client("ec2", region_name=region)
        instance = launch_instance_for_family(ami_id, instance_type, key_name, security_group, subnet_id, ec2_client)

        # Store the instance ID for logging
        instance_id = instance.id
        print(f"Launched instance {instance_id} for {instance_type}")

        wait_for_instance(instance)
        properties = get_io_properties(instance, key_path, username, override)

        # Terminate instance
        instance.terminate()
        print(f"Completed processing of {instance_type} (instance {instance_id})")

        elapsed_time = time.time() - start_time
        print(f"Total processing time for {instance_type}: {elapsed_time:.1f} seconds")

        return instance_type, properties
    except Exception as e:
        elapsed_time = time.time() - start_time
        instance_id = instance.id if instance else "unknown"

        print(f"Error processing {instance_type} (instance {instance_id}) after {elapsed_time:.1f} seconds: {e}")

        # Ensure instance is terminated if it exists
        try:
            if instance:
                print(f"Terminating instance {instance.id} due to error in parallel processing")
                instance.terminate()
                print(f"Instance {instance.id} terminated after error")
        except Exception as term_err:
            print(f"Warning: Error while terminating instance {instance_id} after failure: {term_err}")

        return instance_type, {"error": str(e)}


def load_config(config_file="config.yaml"):
    """
    Load configuration from YAML file
    """
    if not os.path.exists(config_file):
        return {}

    try:
        with open(config_file) as f:
            config = yaml.safe_load(f)
        return config if config else {}
    except Exception as e:
        print(f"Warning: Could not load config file {config_file}: {e}")
        return {}


def save_config(config, config_file="config.yaml"):
    """
    Save configuration to YAML file
    """
    try:
        with open(config_file, "w") as f:
            yaml.dump(config, f, default_flow_style=False)
        print(f"Configuration saved to {config_file}")
    except Exception as e:
        print(f"Warning: Could not save config file {config_file}: {e}")


def get_absolute_params_path(relative_path):
    """
    Convert a relative path to an absolute path from the script directory
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if relative_path.startswith("../"):
        # If a relative path starting with ../ is provided, resolve it from the script directory
        return os.path.abspath(os.path.join(script_dir, relative_path))
    return relative_path


def sort_aws_instance_types(instance_types):
    """
    Sort AWS instance types in logical order:
    1. Group by instance family, sorted alphabetically by family components
    2. Within each family, sort by size (large, xlarge, 2xlarge, etc.)
    3. Put .ALL variants after the specific sizes for each family

    Family sorting logic:
    - First by first letter (c, i, m, r, x, z, etc.)
    - Then by number (if present)
    - Then by additional letters alphabetically

    Args:
        instance_types (list): List of instance type strings

    Returns:
        list: Sorted list of instance types
    """

    # Define the logical order of instance sizes
    size_order = [
        "medium",
        "large",
        "xlarge",
        "2xlarge",
        "3xlarge",
        "4xlarge",
        "6xlarge",
        "8xlarge",
        "9xlarge",
        "12xlarge",
        "16xlarge",
        "18xlarge",
        "24xlarge",
        "32xlarge",
        "48xlarge",
        "metal",
    ]

    def parse_instance_type(instance_type):
        """Parse instance type into family, size, and ALL flag"""
        if instance_type.endswith(".ALL"):
            family = instance_type[:-4]  # Remove .ALL
            size = "ALL"
            is_all = True
        else:
            parts = instance_type.split(".")
            if len(parts) == 2:
                family, size = parts
                is_all = False
            else:
                # Handle edge cases
                family = instance_type
                size = "unknown"
                is_all = False
        return family, size, is_all

    def parse_family_for_sorting(family):
        """
        Parse family name into components for natural sorting.
        Examples:
        - 'i3' -> ('i', 3, '')
        - 'i3en' -> ('i', 3, 'en')
        - 'c5d' -> ('c', 5, 'd')
        - 'im4gn' -> ('i', 0, 'm4gn')  # if no number found, treat as suffix
        """

        # Match pattern: letters + number + optional letters
        # Regex groups:
        #   group 1 = letter prefix (e.g., 'i', 'c')
        #   group 2 = numeric generation (e.g., '3', '5')
        #   group 3 = optional suffix (e.g., 'en', 'd', '')
        match = re.match(r"^([a-z]+)(\d+)([a-z]*)$", family.lower())
        if match:
            prefix, number_str, suffix = match.groups()
            number = int(number_str)
            return (prefix, number, suffix)
        # Fallback: treat entire family as prefix
        return (family.lower(), 0, "")

    def sort_key(instance_type):
        """Generate sort key for instance type"""
        family, size, is_all = parse_instance_type(instance_type)

        # Parse family for natural sorting
        family_sort_key = parse_family_for_sorting(family)

        # Get size order
        if is_all:
            size_idx = len(size_order)  # .ALL goes after all specific sizes
        else:
            try:
                size_idx = size_order.index(size)
            except ValueError:
                size_idx = len(size_order) - 1  # Unknown sizes go before .ALL

        return (family_sort_key, size_idx, is_all)

    return sorted(instance_types, key=sort_key)


def update_aws_params_yaml(all_properties, params_file_path):
    """
    Update aws_io_params.yaml with IO properties from all processed instances
    Handles multiple instances with proper internal family sorting

    Args:
        all_properties (dict): Dictionary of instance types to IO properties
        params_file_path (str): Path to aws_io_params.yaml

    Returns:
        int: Number of instance types updated
    """
    update_count = 0

    # Get absolute path to aws_io_params.yaml
    params_file = get_absolute_params_path(params_file_path)
    print(f"Updating AWS IO parameters in {params_file}...")

    # Group instances by family for proper sorting
    updates_by_family = {}
    for instance_type, properties in all_properties.items():
        # Skip instances that had errors during processing
        if "error" in properties:
            print(f"Skipping {instance_type} due to processing error")
            continue

        # Extract the relevant properties
        if "disks" in properties and len(properties["disks"]) > 0:
            disk_props = properties["disks"][0]

            io_params = {
                "read_iops": int(disk_props.get("read_iops", 0)),
                "read_bandwidth": int(disk_props.get("read_bandwidth", 0)),
                "write_iops": int(disk_props.get("write_iops", 0)),
                "write_bandwidth": int(disk_props.get("write_bandwidth", 0)),
            }

            family = instance_type.split(".")[0]
            if family not in updates_by_family:
                updates_by_family[family] = {}
            updates_by_family[family][instance_type] = io_params
        else:
            print(f"No disk properties found for {instance_type}, skipping")

    # Process updates family by family with proper internal sorting
    for family, instances in updates_by_family.items():
        # Sort instances within the family by size
        sorted_instances = sort_instances_by_size(list(instances.keys()))

        print(f"Updating {family} family with instances: {sorted_instances}")

        # Apply updates in size order
        for instance_type in sorted_instances:
            try:
                # Create a temporary properties structure
                temp_properties = {
                    "disks": [
                        {
                            "read_iops": instances[instance_type]["read_iops"],
                            "read_bandwidth": instances[instance_type]["read_bandwidth"],
                            "write_iops": instances[instance_type]["write_iops"],
                            "write_bandwidth": instances[instance_type]["write_bandwidth"],
                        }
                    ]
                }

                # Actual update logic
                # Read existing YAML file (if exists)
                if os.path.exists(params_file):
                    with open(params_file) as f:
                        try:
                            params_data = yaml.safe_load(f) or {}
                        except Exception:
                            params_data = {}
                else:
                    params_data = {}

                # Update or insert the instance type's IO properties
                # Assume top-level key is instance type
                instance_changed = False
                if instance_type not in params_data or params_data[instance_type] != temp_properties:
                    params_data[instance_type] = temp_properties
                    instance_changed = True

                # Write back only if changed
                if instance_changed:
                    with open(params_file, "w") as f:
                        yaml.safe_dump(params_data, f, default_flow_style=False, sort_keys=False)
                    update_count += 1
                    print(f"Updated parameters for {instance_type}")
                else:
                    print(f"No changes needed for {instance_type}")

            except Exception as e:
                print(f"Error updating {instance_type}: {e}")

    if update_count > 0:
        print(f"Successfully updated {update_count} instance types in {params_file}")
    else:
        print("No updates needed")

    return update_count


def sort_instances_by_size(instance_types):
    """
    Sort instance types by size within the same family

    Args:
        instance_types (list): List of instance types from the same family

    Returns:
        list: Sorted instance types
    """
    size_order = [
        "medium",
        "large",
        "xlarge",
        "2xlarge",
        "3xlarge",
        "4xlarge",
        "6xlarge",
        "8xlarge",
        "9xlarge",
        "12xlarge",
        "16xlarge",
        "18xlarge",
        "24xlarge",
        "32xlarge",
        "48xlarge",
        "metal",
        "ALL",
    ]

    def sort_key(instance_type):
        if "." not in instance_type:
            return (999, instance_type)  # Fallback for malformed types

        size = instance_type.split(".", 1)[1]
        try:
            size_idx = size_order.index(size)
        except ValueError:
            size_idx = 998  # Unknown sizes go near the end, before ALL

        return (size_idx, instance_type)

    return sorted(instance_types, key=sort_key)


def show_progress(current, total, instance_type=""):
    """
    Show progress indicator
    """
    percentage = int((current / total) * 100)
    bar_length = 40
    filled_length = int(bar_length * current / total)
    bar = "█" * filled_length + "-" * (bar_length - filled_length)
    print(f"\rProgress: [{bar}] {percentage}% ({current}/{total}) {instance_type}", end="", flush=True)
    if current == total:
        print()  # New line at completion


def update_single_instance_params(instance_type, properties, params_file_path):
    """
    Update aws_io_params.yaml with IO properties for a single instance type
    Simple approach: if exists, replace; if new, add at end

    Args:
        instance_type (str): AWS instance type
        properties (dict): IO properties dictionary
        params_file_path (str): Path to aws_io_params.yaml

    Returns:
        bool: True if updated, False if no changes needed
    """
    # Get absolute path to aws_io_params.yaml
    params_file = get_absolute_params_path(params_file_path)

    # Use the update function from the other module
    try:
        # Load existing YAML data, or start with empty dict
        if os.path.exists(params_file):
            with open(params_file) as f:
                try:
                    data = yaml.safe_load(f) or {}
                except Exception:
                    data = {}
        else:
            data = {}

        # Update or add the instance_type entry
        if data.get(instance_type) == properties:
            # No change needed
            return False
        data[instance_type] = properties

        # Write back to YAML file
        with open(params_file, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=True)
        return True
    except Exception as e:
        print(f"Error updating parameters for {instance_type}: {e}")
        return False


class SetProvidedAction(argparse.Action):
    """Custom action to track which arguments were provided"""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        if not hasattr(namespace, "_provided_args"):
            namespace._provided_args = set()
        namespace._provided_args.add(self.dest)


def main():
    parser = argparse.ArgumentParser(description="Launch EC2 instance and get Scylla IO properties")
    parser.add_argument("--ami", action=SetProvidedAction, help="AMI ID")
    parser.add_argument(
        "--instance-type",
        action=SetProvidedAction,
        help="Instance type (e.g., i8g.2xlarge) or family (e.g., i8g) - hyphens will be converted to dots",
    )
    parser.add_argument(
        "--key-name", action=SetProvidedAction, help="Name of existing EC2 key pair in your AWS account"
    )
    parser.add_argument("--key-path", action=SetProvidedAction, help="Path to private key file")
    parser.add_argument(
        "--security-group", action=SetProvidedAction, help="Security group name or ID that allows SSH access (port 22)"
    )
    parser.add_argument(
        "--subnet-id",
        action=SetProvidedAction,
        help="Subnet ID (optional, auto-detected from security group VPC if not specified)",
    )
    parser.add_argument("--username", default="scyllaadm", help="SSH username (default: scyllaadm for ScyllaDB)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without launching instances")
    parser.add_argument(
        "--max-instances",
        action=SetProvidedAction,
        type=int,
        help="Maximum number of instances to process for a family (default: no limit)",
    )
    parser.add_argument(
        "--parallel", type=int, default=1, help="Number of instances to process in parallel (default: 1)"
    )
    parser.add_argument("--region", default="us-east-1", help="AWS region to use (default: us-east-1)")
    parser.add_argument(
        "--config", default="config.yaml", help="Configuration file to load/save settings (default: config.yaml)"
    )
    parser.add_argument("--save-config", action="store_true", help="Save current settings to config file")
    parser.add_argument(
        "--update-aws-params", action="store_true", help="Update aws_io_params.yaml with collected IO properties"
    )
    parser.add_argument(
        "--aws-params-file",
        action=SetProvidedAction,
        default="../../common/aws_io_params.yaml",
        help="Path to aws_io_params.yaml file (default: ../../common/aws_io_params.yaml)",
    )
    parser.add_argument(
        "--override",
        action="store_true",
        help="Force re-running scylla_io_setup by removing existing io_properties.yaml file",
    )

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    # Override config with command line arguments (only for provided arguments)
    provided_args = getattr(args, "_provided_args", set())

    for key, value in vars(args).items():
        if key in ["config", "save_config", "_provided_args"]:
            continue
        # Only override config if argument was provided on command line
        if key in provided_args:
            config[key] = value

    # Handle boolean flags separately - only override config if they were set to True on command line
    # This allows config file values to be respected when flags are not explicitly provided
    if args.dry_run:
        config["dry_run"] = True
    if args.save_config:
        config["save_config"] = True
    if args.override:
        config["override"] = True

    # Set defaults from config or command line
    args.ami = config.get("ami")
    args.instance_type = config.get("instance_type")
    args.key_name = config.get("key_name")
    args.key_path = config.get("key_path")
    args.security_group = config.get("security_group")
    args.subnet_id = config.get("subnet_id")
    args.username = config.get("username", "scyllaadm")
    args.region = config.get("region", "us-east-1")
    args.parallel = config.get("parallel", 1)

    # Handle boolean flags - use config values if command line flags were not set
    if not args.dry_run:
        args.dry_run = config.get("dry_run", False)
    if not args.save_config:
        args.save_config = config.get("save_config", False)
    if not args.override:
        args.override = config.get("override", False)

    # Safety check: prevent using the same file for config and IO parameters
    if args.save_config and args.update_aws_params:
        config_path = os.path.abspath(args.config)
        io_params_path = os.path.abspath(get_absolute_params_path(args.aws_params_file))

        if config_path == io_params_path:
            print("ERROR: Cannot use the same file for --config and --aws-params-file")
            print(f"Config file: {config_path}")
            print(f"IO params file: {io_params_path}")
            print()
            print("Correct usage:")
            print("  --config config.yaml (for script configuration)")
            print("  --aws-params-file common/aws_io_params.yaml (for IO parameters)")
            print("  --update-aws-params (to enable IO parameter updates)")
            sys.exit(1)

    # Validate required arguments
    if not args.ami and not args.dry_run:
        parser.error("--ami is required")
    if not args.instance_type:
        parser.error("--instance-type is required")
    if not args.key_name and not args.dry_run:
        parser.error("--key-name is required. Specify the name of an existing EC2 key pair in your AWS account.")
    if not args.key_path and not args.dry_run:
        parser.error("--key-path is required")
    if not args.security_group and not args.dry_run:
        parser.error(
            "--security-group is required. Specify a security group name or ID that allows SSH access (port 22)."
        )

    # Expand user path
    if args.key_path:
        args.key_path = os.path.expanduser(args.key_path)

    # Save config if requested
    if args.save_config:
        # Create a config dict with current values
        current_config = {
            "ami": args.ami,
            "instance_type": args.instance_type,
            "key_name": args.key_name,
            "key_path": args.key_path,
            "security_group": args.security_group,
            "subnet_id": args.subnet_id,
            "username": args.username,
            "region": args.region,
            "parallel": args.parallel,
            "max_instances": args.max_instances,
            "dry_run": args.dry_run,
            "override": args.override,
        }
        save_config(current_config, args.config)

    # Fix common instance type formatting issues
    if args.instance_type and "-" in args.instance_type:
        # Convert i8g-2xlarge to i8g.2xlarge
        args.instance_type = args.instance_type.replace("-", ".")
        print(f"Corrected instance type to: {args.instance_type}")

    # Check if it's a family (no dot) or specific instance type (has dot)
    if "." not in args.instance_type:
        # It's a family - get all instance types in this family
        print(f"Detecting instance family: {args.instance_type}")
        ec2_client = boto3.client("ec2", region_name=args.region)
        instance_types = get_instance_types_in_family(args.instance_type, ec2_client)

        if not instance_types:
            raise Exception(f"No instance types found for family {args.instance_type}")

        print(f"Found {len(instance_types)} instance types in {args.instance_type} family:")
        for it in instance_types:
            print(f"  - {it}")

        # Apply max-instances limit if specified
        if args.max_instances and len(instance_types) > args.max_instances:
            instance_types = instance_types[: args.max_instances]
            print(f"Limiting to first {args.max_instances} instance types: {instance_types}")

        # Dry-run mode
        if args.dry_run:
            print(f"Dry-run: Would process {len(instance_types)} instance types without launching instances.")
            return

        # Confirmation for multiple instances
        if len(instance_types) > 1:
            try:
                response = input(
                    f"About to launch {len(instance_types)} instances. This may incur costs. Continue? (y/N): "
                )
                if response.lower() != "y":
                    print("Operation cancelled by user.")
                    return
            except EOFError:
                # Handle non-interactive environments
                print("Non-interactive environment detected. Skipping confirmation.")

        # Process each instance type
        all_properties = {}
        total_instances = len(instance_types)
        completed = 0

        if args.parallel and args.parallel > 1:
            print(f"Processing {total_instances} instances in parallel (max {args.parallel} concurrent)...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as executor:
                futures = {
                    executor.submit(
                        process_instance_parallel,
                        it,
                        args.ami,
                        args.key_name,
                        args.security_group,
                        args.subnet_id,
                        args.key_path,
                        args.username,
                        args.region,
                        args.override,
                    ): it
                    for it in instance_types
                }
                for future in concurrent.futures.as_completed(futures):
                    instance_type, properties = future.result()
                    all_properties[instance_type] = properties

                    completed += 1
                    show_progress(completed, total_instances, f"Completed {instance_type}")
        else:
            print(f"Processing {total_instances} instances sequentially...")
            for idx, instance_type in enumerate(instance_types):
                if args.max_instances and idx >= args.max_instances:
                    print(f"Stopping after {args.max_instances} instances due to --max-instances limit")
                    break
                print(f"\n=== Processing {instance_type} ({idx + 1}/{total_instances}) ===")
                start_time = time.time()
                instance = None  # Initialize instance variable to ensure it's in scope for error handler

                try:
                    if args.dry_run:
                        print(f"Dry run: would launch {instance_type}")
                        continue

                    instance = launch_instance_for_family(
                        args.ami, instance_type, args.key_name, args.security_group, args.subnet_id, ec2_client
                    )

                    # Store instance ID for better logging
                    instance_id = instance.id
                    print(f"Launched instance {instance_id} for {instance_type}")

                    wait_for_instance(instance)
                    properties = get_io_properties(instance, args.key_path, args.username, args.override)
                    all_properties[instance_type] = properties

                    # Terminate instance
                    instance.terminate()
                    print(f"Terminated {instance_type} instance {instance_id}")

                    elapsed_time = time.time() - start_time
                    print(f"Total processing time for {instance_type}: {elapsed_time:.1f} seconds")

                except Exception as e:
                    elapsed_time = time.time() - start_time
                    print(f"Error processing {instance_type} after {elapsed_time:.1f} seconds: {e}")
                    all_properties[instance_type] = {"error": str(e)}

                    # Ensure instance is terminated if it exists
                    try:
                        if instance:
                            instance_id = instance.id
                            print(f"Terminating instance {instance_id} due to error")
                            instance.terminate()
                            print(f"Instance {instance_id} terminated after error")
                    except Exception as term_err:
                        print(f"Warning: Error while terminating instance after failure: {term_err}")

                completed += 1
                show_progress(completed, total_instances, f"Completed {instance_type}")

        # Display summary
        print("\n=== IO Properties Summary ===")
        for instance_type, props in all_properties.items():
            print(f"\n{instance_type}:")
            if "error" in props:
                print(f"  Error: {props['error']}")
            else:
                print(format_output(props, instance_type))

    else:
        # It's a specific instance type - use original logic
        try:
            if args.dry_run:
                print(f"Dry run: would launch {args.instance_type}")
            else:
                print("Processing single instance...")
                show_progress(0, 1, f"Starting {args.instance_type}")

                instance = None  # Initialize instance variable to ensure it's in scope for error handler
                start_time = time.time()

                try:
                    # Launch the instance and store its ID for better logging
                    instance = launch_instance(
                        args.ami, args.instance_type, args.key_name, args.security_group, args.subnet_id, args.region
                    )
                    instance_id = instance.id
                    print(f"Launched instance {instance_id} for {args.instance_type}")
                    show_progress(1, 4, "Instance launched")

                    # Wait for the instance to be ready
                    wait_for_instance(instance)
                    show_progress(2, 4, "Instance ready")

                    # Retrieve IO properties
                    properties = get_io_properties(instance, args.key_path, args.username, args.override)
                    show_progress(3, 4, "IO properties retrieved")

                    # Display results
                    print(f"\nIO Properties for {args.instance_type}:")
                    print(format_output(properties, args.instance_type))

                    # Terminate instance
                    instance.terminate()
                    show_progress(4, 4, "Instance terminated")
                    print(f"Instance {instance_id} terminated")

                    elapsed_time = time.time() - start_time
                    print(f"Total processing time: {elapsed_time:.1f} seconds")

                except Exception as e:
                    # Calculate elapsed time for error reporting
                    elapsed_time = time.time() - start_time
                    print(f"Error after {elapsed_time:.1f} seconds: {e}")

                    # Ensure instance is terminated if it exists
                    if instance:
                        try:
                            instance_id = instance.id
                            print(f"Terminating instance {instance_id} due to error")
                            instance.terminate()
                            print(f"Instance {instance_id} terminated after error")
                        except Exception as term_err:
                            print(f"Warning: Error while terminating instance after failure: {term_err}")
                    # Re-raise the exception to be caught by the outer try/except
                    raise

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]
            if "groupId" in error_message and "invalid" in error_message.lower():
                print(f'AWS Error: The security group "{args.security_group}" does not exist or is not accessible.')
                print("Please check the security group name/ID or create one that allows SSH access.")
                print(
                    'You can list available security groups with: aws ec2 describe-security-groups --query "SecurityGroups[*].{Name:GroupName,ID:GroupId}"'
                )
                print("Or specify a different security group with --security-group <group-name-or-id>")
            else:
                print(f"AWS Error: {e}")
        except Exception as e:
            print(f"Error: {e}")

    # Save configuration if requested
    if args.save_config:
        save_config(config, args.config)

    # Update aws_io_params.yaml at the end if requested
    if args.update_aws_params and not args.dry_run:
        if "." not in args.instance_type:
            # Instance family processing - update with all collected properties
            if "all_properties" in locals() and all_properties:
                print("\n=== Updating AWS IO Parameters ===")
                updates = update_aws_params_yaml(all_properties, args.aws_params_file)
                print(f"Updated {updates} instance types in AWS IO parameters file")
        else:
            # Single instance processing - update with the properties we collected
            if "properties" in locals() and properties:
                print("\n=== Updating AWS IO Parameters ===")
                if update_single_instance_params(args.instance_type, properties, args.aws_params_file):
                    print(f"Updated parameters for {args.instance_type} in AWS IO parameters file")
                else:
                    print(f"No changes needed for {args.instance_type}")


if __name__ == "__main__":
    main()
