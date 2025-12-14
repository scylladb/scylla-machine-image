#!/usr/bin/env python3
"""
Script to launch an OCI compute instance with given image and shape,
then retrieve and parse values from /etc/scylla.d/io_properties.yaml

Features:
- Supports single instances or entire shape families
- Uses Paramiko SSH client for connection (no system SSH fallback)
- Automatic instance tagging for better identification
- Robust error handling with automatic instance termination
- Configurable timeout values for different instance sizes
- Support for Flex shapes with configurable OCPUs and memory
"""

import argparse
import concurrent.futures
import os
import re
import stat
import time

import paramiko
import yaml


try:
    from tabulate import tabulate

    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False

import oci
from oci.core import ComputeClient, VirtualNetworkClient
from oci.core.models import (
    CreateVnicDetails,
    InstanceSourceViaImageDetails,
    LaunchInstanceDetails,
    LaunchInstanceShapeConfigDetails,
)


def get_wait_times(shape):
    """
    Get appropriate wait times based on instance shape
    Returns (initial_wait_seconds, io_properties_wait_minutes)
    """
    # Extract size indicators from shape
    # For BM (bare metal) shapes, use longer wait times
    # For VM shapes, base on OCPU count if Flex, otherwise use shape type

    base_initial_wait = 30  # seconds
    base_io_wait = 30  # minutes

    if shape.startswith("BM."):
        # Bare metal instances need more time
        multiplier = 7
    elif "DenseIO" in shape or "Optimized" in shape:
        # I/O optimized shapes may need more time for setup
        multiplier = 5
    elif ".Flex" in shape:
        # Flex shapes - extract OCPU count from shape string if present
        # Format: VM.Standard.E4.Flex-8 (8 OCPUs)
        match = re.search(r"-(\d+)$", shape)
        if match:
            ocpus = int(match.group(1))
            if ocpus >= 32:
                multiplier = 6
            elif ocpus >= 16:
                multiplier = 4
            elif ocpus >= 8:
                multiplier = 2.5
            else:
                multiplier = 1.5
        else:
            multiplier = 2
    else:
        multiplier = 1.5

    initial_wait = int(base_initial_wait * multiplier)
    io_wait_minutes = int(base_io_wait * multiplier)

    return initial_wait, io_wait_minutes


def get_oci_config(config_file=None, profile="DEFAULT"):
    """
    Load OCI configuration from file or environment
    """
    if config_file:
        config = oci.config.from_file(file_location=config_file, profile_name=profile)
    else:
        config = oci.config.from_file(profile_name=profile)

    oci.config.validate_config(config)
    return config


def launch_instance(
    oci_config,
    image_id,
    shape,
    compartment_id,
    subnet_id,
    ssh_public_key_path,
    availability_domain,
    ocpus=None,
    memory_gb=None,
    display_name=None,
):
    """
    Launch an OCI compute instance

    Args:
        oci_config: OCI configuration dict
        image_id: OCID of the image to use
        shape: Instance shape (e.g., VM.Standard.E4.Flex, BM.Standard.E5.192)
        compartment_id: OCID of the compartment
        subnet_id: OCID of the subnet
        ssh_public_key_path: Path to SSH public key file
        availability_domain: Availability domain name
        ocpus: Number of OCPUs (for Flex shapes)
        memory_gb: Memory in GB (for Flex shapes)
        display_name: Display name for the instance

    Returns:
        Instance object
    """
    compute_client = ComputeClient(oci_config)

    # Read SSH public key
    with open(os.path.expanduser(ssh_public_key_path)) as f:
        ssh_public_key = f.read().strip()

    # Create instance details
    if not display_name:
        display_name = f"io_properties_setup_{shape}"

    # Create VNIC details
    vnic_details = CreateVnicDetails(subnet_id=subnet_id, assign_public_ip=True)

    # Create source details
    source_details = InstanceSourceViaImageDetails(image_id=image_id)

    # Prepare launch instance details
    launch_details_kwargs = {
        "compartment_id": compartment_id,
        "availability_domain": availability_domain,
        "shape": shape,
        "display_name": display_name,
        "source_details": source_details,
        "create_vnic_details": vnic_details,
        "metadata": {"ssh_authorized_keys": ssh_public_key},
        "freeform_tags": {
            "keep": "1",  # Auto-termination tag
            "purpose": "io_properties_measurement",
        },
    }

    # Add shape config for Flex shapes
    if ".Flex" in shape:
        if not ocpus:
            raise ValueError(f"OCPUs count required for Flex shape {shape}")
        if not memory_gb:
            # Default memory: 16GB per OCPU for most shapes
            memory_gb = ocpus * 16

        shape_config = LaunchInstanceShapeConfigDetails(ocpus=float(ocpus), memory_in_gbs=float(memory_gb))
        launch_details_kwargs["shape_config"] = shape_config

    launch_details = LaunchInstanceDetails(**launch_details_kwargs)

    # Launch instance
    print(f"Launching instance with shape {shape}...")
    response = compute_client.launch_instance(launch_details)
    instance = response.data

    print(f"Launched instance {instance.id}")
    return instance


def wait_for_instance(oci_config, instance_id, shape):
    """
    Wait for instance to be running and get public IP

    Returns:
        public_ip: Public IP address of the instance
    """
    compute_client = ComputeClient(oci_config)
    vnc_client = VirtualNetworkClient(oci_config)

    print("Waiting for instance to be running...")

    # Wait for instance to reach RUNNING state
    get_instance_response = compute_client.get_instance(instance_id)

    while get_instance_response.data.lifecycle_state != "RUNNING":
        time.sleep(10)
        get_instance_response = compute_client.get_instance(instance_id)
        print(f"Instance state: {get_instance_response.data.lifecycle_state}")

    instance = get_instance_response.data
    print("Instance is running")

    # Get VNIC attachment to find public IP
    vnic_attachments = compute_client.list_vnic_attachments(
        compartment_id=instance.compartment_id, instance_id=instance_id
    ).data

    if not vnic_attachments:
        raise Exception("No VNIC attachments found for instance")

    vnic_id = vnic_attachments[0].vnic_id
    vnic = vnc_client.get_vnic(vnic_id).data
    public_ip = vnic.public_ip

    if not public_ip:
        raise Exception("Instance has no public IP address")

    print(f"Instance public IP: {public_ip}")

    # Get appropriate wait time based on instance shape
    initial_wait, _ = get_wait_times(shape)
    print(f"Waiting {initial_wait} seconds for services to start...")
    time.sleep(initial_wait)

    return public_ip


def get_io_properties(public_ip, key_path, username="scyllaadm", override=False, shape=""):
    """
    SSH into instance using Paramiko SSH client and get IO properties

    Args:
        public_ip: Public IP address of the instance
        key_path: Path to SSH private key
        username: SSH username (default: scyllaadm)
        override: If True, force re-running scylla_io_setup by removing existing file
        shape: Instance shape (for wait time calculation)

    Returns:
        dict: IO properties
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
            print(f"Attempting SSH connection (attempt {attempt + 1}/5) to {public_ip} as {username}...")
            ssh.connect(
                hostname=public_ip,
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
            last_error = f"Connection error: {conn_err} (check if port 22 is open in security list)"
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
    _, io_wait_minutes = get_wait_times(shape)
    max_attempts = io_wait_minutes * 6  # 6 attempts per minute (every 10 seconds)

    print(f"Waiting for IO properties file (up to {io_wait_minutes} minutes for {shape})...")

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
            raise Exception(f"IO properties file not found after {io_wait_minutes} minutes for {shape}")

        print("IO properties file found!")

        # Read the file
        stdin, stdout, stderr = ssh.exec_command("cat /etc/scylla.d/io_properties.yaml")
        content = stdout.read().decode()

        return yaml.safe_load(content)
    finally:
        # Ensure we always close the SSH connection
        ssh.close()


def terminate_instance(oci_config, instance_id):
    """
    Terminate an OCI instance
    """
    compute_client = ComputeClient(oci_config)

    print(f"Terminating instance {instance_id}...")
    compute_client.terminate_instance(instance_id)
    print(f"Instance {instance_id} terminated")


def get_shapes_in_family(oci_config, compartment_id, availability_domain, family_prefix):
    """
    Get all available shapes for a given family prefix

    Args:
        oci_config: OCI configuration dict
        compartment_id: OCID of the compartment
        availability_domain: Availability domain name
        family_prefix: Shape family prefix (e.g., 'VM.Standard.E4', 'BM.DenseIO')

    Returns:
        list: List of shape names
    """
    compute_client = ComputeClient(oci_config)

    try:
        # Get all shapes available in the AD
        shapes_response = compute_client.list_shapes(
            compartment_id=compartment_id, availability_domain=availability_domain
        )

        shapes = []
        for shape in shapes_response.data:
            shape_name = shape.shape
            # Filter shapes that match the family prefix and skip metal shapes if not explicitly requested
            if shape_name.startswith(family_prefix) and (
                "metal" not in shape_name.lower() or "metal" in family_prefix.lower()
            ):
                shapes.append(shape_name)

        # Sort shapes by name
        shapes.sort()

        return shapes

    except Exception as e:
        print(f"Error querying shapes for family {family_prefix}: {e}")
        return []


def get_flex_shape_options(oci_config, compartment_id, availability_domain, shape_name):
    """
    Get available OCPU and memory options for a Flex shape

    Args:
        oci_config: OCI configuration dict
        compartment_id: OCID of the compartment
        availability_domain: Availability domain name
        shape_name: Full Flex shape name (e.g., 'VM.Standard.E4.Flex')

    Returns:
        dict: Dictionary with 'ocpu_options' and 'memory_options' containing min/max values,
              plus 'discrete_configs' for shapes like DenseIO that have fixed configurations,
              or None if shape not found
    """
    compute_client = ComputeClient(oci_config)

    try:
        # Get all shapes available in the AD
        shapes_response = compute_client.list_shapes(
            compartment_id=compartment_id, availability_domain=availability_domain
        )

        for shape in shapes_response.data:
            if shape.shape == shape_name:
                if not shape.is_flexible:
                    return None

                ocpu_opts = shape.ocpu_options
                mem_opts = shape.memory_options

                result = {
                    "ocpu_options": {
                        "min": int(ocpu_opts.min) if ocpu_opts else 1,
                        "max": int(ocpu_opts.max) if ocpu_opts else 64,
                        "max_per_numa_node": int(ocpu_opts.max_per_numa_node)
                        if ocpu_opts and ocpu_opts.max_per_numa_node
                        else None,
                    },
                    "memory_options": {
                        "min_in_gbs": float(mem_opts.min_in_g_bs) if mem_opts else 1,
                        "max_in_gbs": float(mem_opts.max_in_g_bs) if mem_opts else 1024,
                        "min_per_ocpu_in_gbs": float(mem_opts.min_per_ocpu_in_gbs)
                        if mem_opts and mem_opts.min_per_ocpu_in_gbs
                        else 1,
                        "max_per_ocpu_in_gbs": float(mem_opts.max_per_ocpu_in_gbs)
                        if mem_opts and mem_opts.max_per_ocpu_in_gbs
                        else 64,
                        "default_per_ocpu_in_gbs": float(mem_opts.default_per_ocpu_in_g_bs)
                        if mem_opts and mem_opts.default_per_ocpu_in_g_bs
                        else 16,
                    },
                    "discrete_configs": None,
                    "local_disks": shape.local_disks,
                    "local_disk_description": shape.local_disk_description,
                }

                # For DenseIO and Optimized shapes, they have discrete configurations
                # based on NVMe disk count. Each NVMe typically comes with fixed OCPU/memory.
                # Common patterns:
                # - VM.DenseIO.E4.Flex: 8 OCPUs, 12GB/OCPU per NVMe
                # - VM.Optimized3.Flex: similar patterns
                if "DenseIO" in shape_name or "Optimized" in shape_name:
                    discrete_configs = generate_denseio_configurations(result)
                    if discrete_configs:
                        result["discrete_configs"] = discrete_configs

                return result

        return None

    except Exception as e:
        print(f"Error querying shape options for {shape_name}: {e}")
        return None


def generate_denseio_configurations(shape_options):
    """
    Generate discrete configurations for DenseIO/Optimized Flex shapes.

    These shapes have fixed configurations where each NVMe disk comes with
    a specific OCPU count and memory amount. The configurations are:
    - 1 NVMe: 8 OCPUs, 96 GB memory
    - 2 NVMes: 16 OCPUs, 192 GB memory
    - 3 NVMes: 24 OCPUs, 288 GB memory
    - 4 NVMes: 32 OCPUs, 384 GB memory
    - 5 NVMes: 40 OCPUs, 480 GB memory
    - 6 NVMes: 48 OCPUs, 576 GB memory

    Memory is 12GB per OCPU for DenseIO.E4/E5 (not the default 16GB the API reports).

    Args:
        shape_options: Dictionary with ocpu_options and memory_options

    Returns:
        list: List of tuples (ocpus, memory_gb) representing valid configurations
    """
    ocpu_min = shape_options["ocpu_options"]["min"]
    ocpu_max = shape_options["ocpu_options"]["max"]

    # DenseIO shapes have 12 GB per OCPU (not the 16 GB default the API reports)
    # This is a fixed ratio for DenseIO shapes
    mem_per_ocpu = 12

    # DenseIO shapes have specific OCPU configurations based on NVMe count
    # Each NVMe comes with 8 OCPUs
    # Available configs: 8, 16, 24, 32, 40, 48 OCPUs (1-6 NVMes)
    possible_ocpus = [8, 16, 24, 32, 40, 48, 56, 64, 72, 80, 88, 96]

    configs = []
    for ocpus in possible_ocpus:
        if ocpu_min <= ocpus <= ocpu_max:
            memory_gb = ocpus * mem_per_ocpu
            configs.append((ocpus, int(memory_gb)))

    return configs


def generate_flex_ocpu_configurations(shape_options):
    """
    Generate a list of OCPU configurations to test based on shape options.

    For shapes with discrete configurations (like DenseIO.Flex), returns those exact configs.
    For standard Flex shapes, uses powers of 2 starting from min up to max.

    Args:
        shape_options: Dictionary from get_flex_shape_options()

    Returns:
        list: List of tuples (ocpus, memory_gb) to test, or list of ocpu counts for standard shapes
    """
    if not shape_options:
        # Fallback defaults if options not available
        return [1, 2, 4, 8, 16, 32, 64]

    # Check if this shape has discrete configurations (e.g., DenseIO.Flex)
    if "discrete_configs" in shape_options and shape_options["discrete_configs"]:
        # Return the discrete configurations as-is
        return shape_options["discrete_configs"]

    ocpu_min = shape_options["ocpu_options"]["min"]
    ocpu_max = shape_options["ocpu_options"]["max"]

    # Generate powers of 2 within the valid range
    ocpus = []
    current = 1
    while current <= ocpu_max:
        if current >= ocpu_min:
            ocpus.append(current)
        current *= 2

    # Always include max if it's not already included and is a reasonable value
    if ocpu_max not in ocpus and ocpu_max <= 256:
        ocpus.append(ocpu_max)

    # Sort the list
    ocpus.sort()

    return ocpus


def get_all_flex_shapes(oci_config, compartment_id, availability_domain):
    """
    Get all available Flex shapes and their OCPU/memory options from OCI API.

    Args:
        oci_config: OCI configuration dict
        compartment_id: OCID of the compartment
        availability_domain: Availability domain name

    Returns:
        dict: Dictionary mapping shape names to their options (ocpu_options, memory_options)
    """
    compute_client = ComputeClient(oci_config)
    flex_shapes = {}

    try:
        # Get all shapes available in the AD
        shapes_response = compute_client.list_shapes(
            compartment_id=compartment_id, availability_domain=availability_domain
        )

        for shape in shapes_response.data:
            if shape.is_flexible:
                ocpu_opts = shape.ocpu_options
                mem_opts = shape.memory_options

                flex_shapes[shape.shape] = {
                    "ocpu_options": {
                        "min": int(ocpu_opts.min) if ocpu_opts else 1,
                        "max": int(ocpu_opts.max) if ocpu_opts else 64,
                        "max_per_numa_node": int(ocpu_opts.max_per_numa_node)
                        if ocpu_opts and ocpu_opts.max_per_numa_node
                        else None,
                    },
                    "memory_options": {
                        "min_in_gbs": float(mem_opts.min_in_g_bs) if mem_opts else 1,
                        "max_in_gbs": float(mem_opts.max_in_g_bs) if mem_opts else 1024,
                        "min_per_ocpu_in_gbs": float(mem_opts.min_per_ocpu_in_gbs)
                        if mem_opts and mem_opts.min_per_ocpu_in_gbs
                        else 1,
                        "max_per_ocpu_in_gbs": float(mem_opts.max_per_ocpu_in_gbs)
                        if mem_opts and mem_opts.max_per_ocpu_in_gbs
                        else 64,
                        "default_per_ocpu_in_gbs": float(mem_opts.default_per_ocpu_in_g_bs)
                        if mem_opts and mem_opts.default_per_ocpu_in_g_bs
                        else 16,
                    },
                }

        return flex_shapes

    except Exception as e:
        print(f"Error querying flex shapes: {e}")
        return {}


def list_available_shapes(oci_config, compartment_id, availability_domain):
    """
    List all available shapes from OCI API with their details.

    Args:
        oci_config: OCI configuration dict
        compartment_id: OCID of the compartment
        availability_domain: Availability domain name

    Returns:
        list: List of shape dictionaries with name, is_flexible, ocpus, memory_in_gbs
    """
    compute_client = ComputeClient(oci_config)
    shapes_list = []

    try:
        shapes_response = compute_client.list_shapes(
            compartment_id=compartment_id, availability_domain=availability_domain
        )

        for shape in shapes_response.data:
            shape_info = {
                "name": shape.shape,
                "is_flexible": shape.is_flexible,
                "ocpus": shape.ocpus,
                "memory_in_gbs": shape.memory_in_gbs,
            }

            if shape.is_flexible:
                ocpu_opts = shape.ocpu_options
                mem_opts = shape.memory_options
                shape_info["ocpu_options"] = {
                    "min": int(ocpu_opts.min) if ocpu_opts else 1,
                    "max": int(ocpu_opts.max) if ocpu_opts else 64,
                }
                shape_info["memory_options"] = {
                    "min_in_gbs": float(mem_opts.min_in_g_bs) if mem_opts else 1,
                    "max_in_gbs": float(mem_opts.max_in_g_bs) if mem_opts else 1024,
                    "default_per_ocpu_in_gbs": float(mem_opts.default_per_ocpu_in_g_bs)
                    if mem_opts and mem_opts.default_per_ocpu_in_g_bs
                    else 16,
                }

            shapes_list.append(shape_info)

        # Sort by name
        shapes_list.sort(key=lambda x: x["name"])
        return shapes_list

    except Exception as e:
        print(f"Error listing shapes: {e}")
        return []


def format_output(properties, shape=""):
    """
    Format IO properties output as human-readable table
    """
    return format_as_table(properties, shape)


def format_as_table(properties, shape):
    """
    Format properties as a table
    """
    if not properties:
        return f"No properties found for {shape}"

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
    output = f"IO Properties for {shape}:\n"
    output += "-" * 50 + "\n"
    for row in rows:
        output += f"{row[0]:<30} | {row[1]}\n"
    return output


def process_instance_parallel(
    shape,
    oci_config,
    image_id,
    compartment_id,
    subnet_id,
    ssh_public_key_path,
    ssh_private_key_path,
    availability_domain,
    username,
    ocpus=None,
    memory_gb=None,
    override=False,
):
    """
    Process a single instance (for parallel execution)
    Ensures proper instance cleanup even when errors occur
    """
    instance_id = None
    start_time = time.time()

    try:
        print(f"Starting processing of {shape}...")

        # For Flex shapes, append OCPU count to display name
        display_name = f"io_properties_setup_{shape}"
        if ocpus:
            display_name += f"-{ocpus}"
            shape_key = f"{shape}-{ocpus}"
        else:
            shape_key = shape

        instance = launch_instance(
            oci_config,
            image_id,
            shape,
            compartment_id,
            subnet_id,
            ssh_public_key_path,
            availability_domain,
            ocpus,
            memory_gb,
            display_name,
        )

        instance_id = instance.id
        print(f"Launched instance {instance_id} for {shape}")

        public_ip = wait_for_instance(oci_config, instance_id, shape)
        properties = get_io_properties(public_ip, ssh_private_key_path, username, override, shape)

        # Terminate instance
        terminate_instance(oci_config, instance_id)
        print(f"Completed processing of {shape_key} (instance {instance_id})")

        elapsed_time = time.time() - start_time
        print(f"Total processing time for {shape_key}: {elapsed_time:.1f} seconds")

        return shape_key, properties
    except Exception as e:
        elapsed_time = time.time() - start_time
        shape_key = f"{shape}-{ocpus}" if ocpus else shape

        print(
            f'Error processing {shape_key} (instance {instance_id or "unknown"}) after {elapsed_time:.1f} seconds: {e}'
        )

        # Ensure instance is terminated if it exists
        try:
            if instance_id:
                print(f"Terminating instance {instance_id} due to error in parallel processing")
                terminate_instance(oci_config, instance_id)
                print(f"Instance {instance_id} terminated after error")
        except Exception as term_err:
            print(f'Warning: Error while terminating instance {instance_id or "unknown"} after failure: {term_err}')

        return shape_key, {"error": str(e)}


def load_config(config_file="config_oci.yaml"):
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


def save_config(config, config_file="config_oci.yaml"):
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


def update_oci_params_yaml(all_properties, params_file_path):
    """
    Update oci_io_params.yaml with IO properties from all processed instances

    Args:
        all_properties (dict): Dictionary of shape names to IO properties
        params_file_path (str): Path to oci_io_params.yaml

    Returns:
        int: Number of shapes updated
    """
    update_count = 0

    # Get absolute path to oci_io_params.yaml
    params_file = get_absolute_params_path(params_file_path)
    print(f"Updating OCI IO parameters in {params_file}...")

    for shape_name, properties in all_properties.items():
        # Skip instances that had errors during processing
        if "error" in properties:
            print(f"Skipping {shape_name} due to processing error")
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

            try:
                # Read existing YAML file (if exists)
                if os.path.exists(params_file):
                    with open(params_file) as f:
                        try:
                            params_data = yaml.safe_load(f) or {}
                        except Exception:
                            params_data = {}
                else:
                    params_data = {}

                # Update or insert the shape's IO properties
                shape_changed = False
                if shape_name not in params_data or params_data[shape_name] != io_params:
                    params_data[shape_name] = io_params
                    shape_changed = True

                # Write back only if changed
                if shape_changed:
                    with open(params_file, "w") as f:
                        yaml.safe_dump(params_data, f, default_flow_style=False, sort_keys=True)
                    update_count += 1
                    print(f"Updated parameters for {shape_name}")
                else:
                    print(f"No changes needed for {shape_name}")

            except Exception as e:
                print(f"Error updating {shape_name}: {e}")
        else:
            print(f"No disk properties found for {shape_name}, skipping")

    if update_count > 0:
        print(f"Successfully updated {update_count} shapes in {params_file}")
    else:
        print("No updates needed")

    return update_count


def show_progress(current, total, shape=""):
    """
    Show progress indicator
    """
    percentage = int((current / total) * 100)
    bar_length = 40
    filled_length = int(bar_length * current / total)
    bar = "█" * filled_length + "-" * (bar_length - filled_length)
    print(f"\rProgress: [{bar}] {percentage}% ({current}/{total}) {shape}", end="", flush=True)
    if current == total:
        print()  # New line at completion


class SetProvidedAction(argparse.Action):
    """Custom action to track which arguments were provided"""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        if not hasattr(namespace, "_provided_args"):
            namespace._provided_args = set()
        namespace._provided_args.add(self.dest)


def main():
    parser = argparse.ArgumentParser(description="Launch OCI instance and get Scylla IO properties")
    parser.add_argument("--image-id", action=SetProvidedAction, help="Image OCID")
    parser.add_argument(
        "--shape",
        action=SetProvidedAction,
        help="Instance shape (e.g., VM.Standard.E4.Flex, BM.Standard.E5.192) or family prefix (e.g., VM.Standard.E4)",
    )
    parser.add_argument("--compartment-id", action=SetProvidedAction, help="Compartment OCID")
    parser.add_argument("--subnet-id", action=SetProvidedAction, help="Subnet OCID")
    parser.add_argument(
        "--availability-domain", action=SetProvidedAction, help="Availability domain (e.g., ewbj:US-ASHBURN-AD-1)"
    )
    parser.add_argument("--ssh-public-key", action=SetProvidedAction, help="Path to SSH public key file")
    parser.add_argument("--ssh-private-key", action=SetProvidedAction, help="Path to SSH private key file")
    parser.add_argument("--username", default="scyllaadm", help="SSH username (default: scyllaadm for ScyllaDB)")
    parser.add_argument(
        "--ocpus",
        action=SetProvidedAction,
        help='Number of OCPUs for Flex shapes. Can be a single value or comma-separated list (e.g., "2,4,8,16"). '
        "If not specified for Flex shapes, will query available options and test all power-of-2 configurations.",
    )
    parser.add_argument(
        "--memory-gb",
        type=int,
        action=SetProvidedAction,
        help="Memory in GB for Flex shapes (optional, defaults to default per OCPU for the shape)",
    )
    parser.add_argument("--oci-config", help="Path to OCI config file (default: ~/.oci/config)")
    parser.add_argument("--oci-profile", default="DEFAULT", help="OCI config profile to use (default: DEFAULT)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without launching instances")
    parser.add_argument(
        "--list-shapes",
        action="store_true",
        help="List all available shapes in the availability domain and exit. Requires --compartment-id and --availability-domain.",
    )
    parser.add_argument(
        "--parallel", type=int, default=1, help="Number of instances to process in parallel (default: 1)"
    )
    parser.add_argument(
        "--config",
        default="config_oci.yaml",
        help="Configuration file to load/save settings (default: config_oci.yaml)",
    )
    parser.add_argument("--save-config", action="store_true", help="Save current settings to config file")
    parser.add_argument(
        "--update-oci-params", action="store_true", help="Update oci_io_params.yaml with collected IO properties"
    )
    parser.add_argument(
        "--oci-params-file",
        action=SetProvidedAction,
        default="../../common/oci_io_params.yaml",
        help="Path to oci_io_params.yaml file (default: ../../common/oci_io_params.yaml)",
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
        if key in ["config", "save_config", "_provided_args", "oci_config", "oci_profile"]:
            continue
        # Only override config if argument was provided on command line
        if key in provided_args:
            config[key] = value

    # Handle boolean flags separately
    if args.dry_run:
        config["dry_run"] = True
    if args.save_config:
        config["save_config"] = True
    if args.override:
        config["override"] = True

    # Set defaults from config or command line
    args.image_id = config.get("image_id")
    args.shape = config.get("shape")
    args.compartment_id = config.get("compartment_id")
    args.subnet_id = config.get("subnet_id")
    args.availability_domain = config.get("availability_domain")
    args.ssh_public_key = config.get("ssh_public_key")
    args.ssh_private_key = config.get("ssh_private_key")
    args.username = config.get("username", "scyllaadm")
    args.parallel = config.get("parallel", 1)

    # Handle boolean flags
    if not args.dry_run:
        args.dry_run = config.get("dry_run", False)
    if not args.save_config:
        args.save_config = config.get("save_config", False)
    if not args.override:
        args.override = config.get("override", False)

    # Handle --list-shapes option early (only requires compartment-id and availability-domain)
    if args.list_shapes:
        if not args.compartment_id:
            parser.error("--compartment-id is required for --list-shapes")
        if not args.availability_domain:
            parser.error("--availability-domain is required for --list-shapes")

        oci_config = get_oci_config(args.oci_config, args.oci_profile)
        print(f"Querying available shapes in {args.availability_domain}...\n")

        shapes = list_available_shapes(oci_config, args.compartment_id, args.availability_domain)

        if not shapes:
            print("No shapes found or error querying shapes.")
            return

        # Group shapes by type
        flex_shapes = [s for s in shapes if s["is_flexible"]]
        fixed_shapes = [s for s in shapes if not s["is_flexible"]]

        print(f"=== Fixed Shapes ({len(fixed_shapes)}) ===")
        if HAS_TABULATE:
            fixed_rows = [[s["name"], s["ocpus"], s["memory_in_gbs"]] for s in fixed_shapes]
            print(tabulate(fixed_rows, headers=["Shape", "OCPUs", "Memory (GB)"], tablefmt="grid"))
        else:
            print(f'{"Shape":<40} {"OCPUs":<10} {"Memory (GB)":<15}')
            print("-" * 65)
            for s in fixed_shapes:
                print(f'{s["name"]:<40} {s["ocpus"]:<10} {s["memory_in_gbs"]:<15}')

        print(f"\n=== Flex Shapes ({len(flex_shapes)}) ===")
        if HAS_TABULATE:
            flex_rows = [
                [
                    s["name"],
                    f'{s["ocpu_options"]["min"]}-{s["ocpu_options"]["max"]}',
                    f'{s["memory_options"]["min_in_gbs"]}-{s["memory_options"]["max_in_gbs"]}',
                    s["memory_options"]["default_per_ocpu_in_gbs"],
                ]
                for s in flex_shapes
            ]
            print(
                tabulate(
                    flex_rows, headers=["Shape", "OCPU Range", "Memory Range (GB)", "Default Mem/OCPU"], tablefmt="grid"
                )
            )
        else:
            print(f'{"Shape":<35} {"OCPU Range":<15} {"Memory Range (GB)":<20} {"Default Mem/OCPU":<15}')
            print("-" * 85)
            for s in flex_shapes:
                ocpu_range = f'{s["ocpu_options"]["min"]}-{s["ocpu_options"]["max"]}'
                mem_range = f'{s["memory_options"]["min_in_gbs"]}-{s["memory_options"]["max_in_gbs"]}'
                print(
                    f'{s["name"]:<35} {ocpu_range:<15} {mem_range:<20} {s["memory_options"]["default_per_ocpu_in_gbs"]:<15}'
                )

        print(f"\nTotal: {len(shapes)} shapes available")
        return

    # Validate required arguments
    if not args.image_id and not args.dry_run:
        parser.error("--image-id is required")
    if not args.shape:
        parser.error("--shape is required")
    if not args.compartment_id and not args.dry_run:
        parser.error("--compartment-id is required")
    if not args.subnet_id and not args.dry_run:
        parser.error("--subnet-id is required")
    if not args.availability_domain and not args.dry_run:
        parser.error("--availability-domain is required")
    if not args.ssh_public_key and not args.dry_run:
        parser.error("--ssh-public-key is required")
    if not args.ssh_private_key and not args.dry_run:
        parser.error("--ssh-private-key is required")

    # Expand user paths
    if args.ssh_public_key:
        args.ssh_public_key = os.path.expanduser(args.ssh_public_key)
    if args.ssh_private_key:
        args.ssh_private_key = os.path.expanduser(args.ssh_private_key)

    # Get OCI configuration
    oci_config = get_oci_config(args.oci_config, args.oci_profile) if not args.dry_run else {}

    # Save config if requested
    if args.save_config:
        current_config = {
            "image_id": args.image_id,
            "shape": args.shape,
            "compartment_id": args.compartment_id,
            "subnet_id": args.subnet_id,
            "availability_domain": args.availability_domain,
            "ssh_public_key": args.ssh_public_key,
            "ssh_private_key": args.ssh_private_key,
            "username": args.username,
            "parallel": args.parallel,
            "ocpus": args.ocpus,
            "memory_gb": args.memory_gb,
            "dry_run": args.dry_run,
            "override": args.override,
        }
        save_config(current_config, args.config)

    # Check if it's a family prefix or specific shape
    # OCI shapes typically have format: VM.Standard.E4.Flex or BM.Standard.E5.192
    # A family would be like: VM.Standard.E4
    is_family = False
    if not args.shape.endswith(".Flex") and "." in args.shape:
        # Check if it's a complete shape or just a family prefix
        # Count dots: VM.Standard.E4 = 2 dots (family), VM.Standard.E4.Flex = 3 dots (shape)
        dot_count = args.shape.count(".")
        if dot_count < 3:
            is_family = True

    # Determine if we need to process multiple configurations
    # This happens when:
    # 1. It's a family prefix (is_family=True)
    # 2. It's a Flex shape with comma-separated OCPUs
    # 3. It's a Flex shape with no OCPUs specified (will query available options)
    is_flex_without_ocpus = args.shape.endswith(".Flex") and not args.ocpus
    is_flex_with_multi_ocpus = args.shape.endswith(".Flex") and args.ocpus and "," in str(args.ocpus)

    if is_family or is_flex_with_multi_ocpus or is_flex_without_ocpus:
        # It's a family or Flex shape with multiple OCPU configs
        if is_family:
            print(f"Detecting shape family: {args.shape}")
            shapes = get_shapes_in_family(oci_config, args.compartment_id, args.availability_domain, args.shape)

            if not shapes:
                raise Exception(f"No shapes found for family {args.shape}")

            print(f"Found {len(shapes)} shapes in {args.shape} family:")
            for s in shapes:
                print(f"  - {s}")
        else:
            # Flex shape with multiple OCPU configs or without OCPUs specified
            shapes = [args.shape]

        # Prepare instance configurations (needed for dry-run as well to show what would be tested)
        instance_configs = []
        for shape in shapes:
            if shape.endswith(".Flex"):
                # For Flex shapes, create configs for each OCPU count
                if args.ocpus:
                    if "," in str(args.ocpus):
                        ocpu_list = [int(x.strip()) for x in str(args.ocpus).split(",")]
                    else:
                        ocpu_list = [int(args.ocpus)]
                    # User-specified OCPUs - use default memory
                    for ocpu_count in ocpu_list:
                        instance_configs.append((shape, ocpu_count, args.memory_gb))
                else:
                    # Query available OCPU options for the Flex shape
                    print(f"Querying available OCPU options for {shape}...")
                    shape_options = get_flex_shape_options(
                        oci_config, args.compartment_id, args.availability_domain, shape
                    )
                    if shape_options:
                        configs = generate_flex_ocpu_configurations(shape_options)
                        print(
                            f'  OCPU range: {shape_options["ocpu_options"]["min"]} - {shape_options["ocpu_options"]["max"]}'
                        )
                        print(
                            f'  Memory per OCPU: {shape_options["memory_options"]["min_per_ocpu_in_gbs"]} - {shape_options["memory_options"]["max_per_ocpu_in_gbs"]} GB'
                        )

                        # Check if configs are tuples (discrete configs) or integers (standard flex)
                        if configs and isinstance(configs[0], tuple):
                            # Discrete configurations (DenseIO, Optimized shapes)
                            print("  Shape has discrete configurations:")
                            for ocpus, mem_gb in configs:
                                print(f"    - {ocpus} OCPUs, {mem_gb} GB memory")
                                instance_configs.append((shape, ocpus, mem_gb))
                        else:
                            # Standard Flex shape with power-of-2 OCPUs
                            print(f"  Will test OCPU configurations: {configs}")
                            for ocpu_count in configs:
                                instance_configs.append((shape, ocpu_count, args.memory_gb))
                    else:
                        # Fallback to default OCPU configurations if query fails
                        print("  Could not query shape options, using default configurations")
                        ocpu_list = [1, 2, 4, 8, 16, 32, 64]
                        for ocpu_count in ocpu_list:
                            instance_configs.append((shape, ocpu_count, args.memory_gb))
            else:
                # Fixed shape
                instance_configs.append((shape, None, None))

        # Dry-run mode
        if args.dry_run:
            print(f"\nDry-run: Would process {len(instance_configs)} instance configurations:")
            for shape, ocpus, mem_gb in instance_configs:
                if ocpus:
                    print(f"  - {shape} with {ocpus} OCPUs" + (f", {mem_gb} GB memory" if mem_gb else ""))
                else:
                    print(f"  - {shape}")
            return

        # Confirmation for multiple instances
        if len(instance_configs) > 1:
            try:
                response = input(
                    f"About to launch {len(instance_configs)} instances. This may incur costs. Continue? (y/N): "
                )
                if response.lower() != "y":
                    print("Operation cancelled by user.")
                    return
            except EOFError:
                print("Non-interactive environment detected. Skipping confirmation.")

        # Process instances
        all_properties = {}
        total_instances = len(instance_configs)
        completed = 0

        if args.parallel and args.parallel > 1:
            print(f"Processing {total_instances} instances in parallel (max {args.parallel} concurrent)...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as executor:
                futures = {}
                for shape, ocpus, mem_gb in instance_configs:
                    future = executor.submit(
                        process_instance_parallel,
                        shape,
                        oci_config,
                        args.image_id,
                        args.compartment_id,
                        args.subnet_id,
                        args.ssh_public_key,
                        args.ssh_private_key,
                        args.availability_domain,
                        args.username,
                        ocpus,
                        mem_gb,
                        args.override,
                    )
                    futures[future] = shape

                for future in concurrent.futures.as_completed(futures):
                    shape_key, properties = future.result()
                    all_properties[shape_key] = properties

                    completed += 1
                    show_progress(completed, total_instances, f"Completed {shape_key}")
        else:
            print(f"Processing {total_instances} instances sequentially...")
            for idx, (shape, ocpus, mem_gb) in enumerate(instance_configs):
                shape_display = f"{shape}-{ocpus}" if ocpus else shape
                print(f"\n=== Processing {shape_display} ({idx+1}/{total_instances}) ===")

                shape_key, properties = process_instance_parallel(
                    shape,
                    oci_config,
                    args.image_id,
                    args.compartment_id,
                    args.subnet_id,
                    args.ssh_public_key,
                    args.ssh_private_key,
                    args.availability_domain,
                    args.username,
                    ocpus,
                    mem_gb,
                    args.override,
                )
                all_properties[shape_key] = properties

                completed += 1
                show_progress(completed, total_instances, f"Completed {shape_key}")

        # Display summary
        print("\n=== IO Properties Summary ===")
        for shape_name, props in all_properties.items():
            print(f"\n{shape_name}:")
            if "error" in props:
                print(f'  Error: {props["error"]}')
            else:
                print(format_output(props, shape_name))

        # Update oci_io_params.yaml if requested
        if args.update_oci_params:
            print("\n=== Updating OCI IO Parameters ===")
            updates = update_oci_params_yaml(all_properties, args.oci_params_file)
            print(f"Updated {updates} shapes in OCI IO parameters file")

    else:
        # Single shape processing
        instance_id = None
        start_time = time.time()

        try:
            if args.dry_run:
                print(f"Dry run: would launch {args.shape}")
            else:
                print("Processing single instance...")
                show_progress(0, 1, f"Starting {args.shape}")

                # For Flex shapes, require OCPU count
                if args.shape.endswith(".Flex") and not args.ocpus:
                    parser.error("--ocpus is required for Flex shapes")

                # Convert ocpus to int if provided as string
                ocpus_value = int(args.ocpus) if args.ocpus else None

                # Launch instance
                instance = launch_instance(
                    oci_config,
                    args.image_id,
                    args.shape,
                    args.compartment_id,
                    args.subnet_id,
                    args.ssh_public_key,
                    args.availability_domain,
                    ocpus_value,
                    args.memory_gb,
                )
                instance_id = instance.id
                print(f"Launched instance {instance_id} for {args.shape}")
                show_progress(1, 4, "Instance launched")

                # Wait for instance
                public_ip = wait_for_instance(oci_config, instance_id, args.shape)
                show_progress(2, 4, "Instance ready")

                # Get IO properties
                properties = get_io_properties(
                    public_ip, args.ssh_private_key, args.username, args.override, args.shape
                )
                show_progress(3, 4, "IO properties retrieved")

                # Display results
                shape_key = f"{args.shape}-{ocpus_value}" if ocpus_value else args.shape
                print(f"\nIO Properties for {shape_key}:")
                print(format_output(properties, shape_key))

                # Terminate instance
                terminate_instance(oci_config, instance_id)
                show_progress(4, 4, "Instance terminated")

                elapsed_time = time.time() - start_time
                print(f"Total processing time: {elapsed_time:.1f} seconds")

                # Update oci_io_params.yaml if requested
                if args.update_oci_params:
                    print("\n=== Updating OCI IO Parameters ===")
                    all_properties = {shape_key: properties}
                    updates = update_oci_params_yaml(all_properties, args.oci_params_file)
                    if updates > 0:
                        print(f"Updated parameters for {shape_key}")
                    else:
                        print(f"No changes needed for {shape_key}")

        except Exception as e:
            elapsed_time = time.time() - start_time
            print(f"Error after {elapsed_time:.1f} seconds: {e}")

            # Ensure instance is terminated if it exists
            if instance_id:
                try:
                    print(f"Terminating instance {instance_id} due to error")
                    terminate_instance(oci_config, instance_id)
                    print(f"Instance {instance_id} terminated after error")
                except Exception as term_err:
                    print(f"Warning: Error while terminating instance after failure: {term_err}")
            raise


if __name__ == "__main__":
    main()
