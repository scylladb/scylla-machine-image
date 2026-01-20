#!/usr/bin/env python3
"""
Script to launch an Azure VM with a given image and VM size,
then retrieve and parse values from /etc/scylla.d/io_properties.yaml.

Features:
- Supports single VM sizes or entire VM families.
- Uses the Paramiko SSH client for connection.
- Automatic resource group management for easy cleanup.
- Robust error handling with automatic resource deletion.
- Configurable timeout values for different VM sizes.
"""

import argparse
import concurrent.futures
import fcntl
import os
import re
import signal
import stat
import threading
import time
import uuid

import paramiko
import yaml
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.resource import ResourceManagementClient


try:
    from tabulate import tabulate

    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False


def get_wait_times(vm_size):
    """
    Get appropriate wait times based on VM size.
    Returns (initial_wait_seconds, io_properties_wait_minutes)
    """
    # Simplified multiplier logic for Azure VM sizes.
    # This can be expanded based on observed setup times.
    size_part = vm_size.split("_")[-1].lower()
    multiplier = 1.0

    if "v3" in size_part or "v4" in size_part or "v5" in size_part:
        multiplier = 1.5
    if "8" in size_part:
        multiplier = 2.0
    if "16" in size_part:
        multiplier = 3.0
    if "32" in size_part:
        multiplier = 4.0
    if "64" in size_part:
        multiplier = 5.0

    base_initial_wait = 60  # seconds
    base_io_wait = 30  # minutes

    initial_wait = int(base_initial_wait * multiplier)
    io_wait_minutes = int(base_io_wait * multiplier)

    return initial_wait, io_wait_minutes


def launch_vm(subscription_id, location, vm_size, image_id, username, ssh_public_key_path, run_id):
    """
    Launch an Azure VM and all necessary resources.
    A new resource group is created for each VM and must be deleted later.
    """
    credential = DefaultAzureCredential()
    resource_client = ResourceManagementClient(credential, subscription_id)
    network_client = NetworkManagementClient(credential, subscription_id)
    compute_client = ComputeManagementClient(credential, subscription_id)

    resource_group_name = f"io-test-rg-{vm_size.replace('_', '-')}-{run_id}"

    # Register RG for cleanup
    register_resource_group(resource_group_name)

    vnet_name = "scylla-io-vnet"
    subnet_name = "default"
    nsg_name = "scylla-io-nsg"
    public_ip_name = f"pip-{vm_size}"
    nic_name = f"nic-{vm_size}"
    # computer_name cannot contain underscores
    vm_name = f"vm-{vm_size.replace('_', '-')}"

    print(f"[{vm_size}] Creating resource group '{resource_group_name}'...")
    if STOP_EVENT.is_set():
        raise Exception("Operation cancelled")
    resource_client.resource_groups.create_or_update(resource_group_name, {"location": location})

    print(f"[{vm_size}] Creating VNet '{vnet_name}' and subnet '{subnet_name}'...")
    if STOP_EVENT.is_set():
        raise Exception("Operation cancelled")
    vnet_poller = network_client.virtual_networks.begin_create_or_update(
        resource_group_name,
        vnet_name,
        {
            "location": location,
            "address_space": {"address_prefixes": ["10.0.0.0/16"]},
        },
    )
    vnet_poller.result()

    subnet_poller = network_client.subnets.begin_create_or_update(
        resource_group_name,
        vnet_name,
        subnet_name,
        {"address_prefix": "10.0.0.0/24"},
    )
    subnet_result = subnet_poller.result()

    print(f"[{vm_size}] Creating Public IP '{public_ip_name}'...")
    if STOP_EVENT.is_set():
        raise Exception("Operation cancelled")
    pip_poller = network_client.public_ip_addresses.begin_create_or_update(
        resource_group_name,
        public_ip_name,
        {
            "location": location,
            "sku": {"name": "Standard"},
            "public_ip_allocation_method": "Static",
            "public_ip_address_version": "IPv4",
        },
    )
    pip_result = pip_poller.result()

    print(f"[{vm_size}] Creating Network Security Group '{nsg_name}' and allowing SSH...")
    if STOP_EVENT.is_set():
        raise Exception("Operation cancelled")
    nsg_poller = network_client.network_security_groups.begin_create_or_update(
        resource_group_name, nsg_name, {"location": location}
    )
    nsg_result = nsg_poller.result()

    rule_poller = network_client.security_rules.begin_create_or_update(
        resource_group_name,
        nsg_name,
        "SSH",
        {
            "protocol": "Tcp",
            "source_address_prefix": "*",
            "destination_address_prefix": "*",
            "access": "Allow",
            "destination_port_range": "22",
            "source_port_range": "*",
            "priority": 1000,
            "direction": "Inbound",
        },
    )
    rule_poller.result()

    print(f"[{vm_size}] Creating Network Interface '{nic_name}'...")
    if STOP_EVENT.is_set():
        raise Exception("Operation cancelled")
    nic_poller = network_client.network_interfaces.begin_create_or_update(
        resource_group_name,
        nic_name,
        {
            "location": location,
            "ip_configurations": [
                {
                    "name": "ipconfig1",
                    "subnet": {"id": subnet_result.id},
                    "public_ip_address": {"id": pip_result.id},
                }
            ],
            "network_security_group": {"id": nsg_result.id},
        },
    )
    nic_result = nic_poller.result()

    print(f"[{vm_size}] Reading SSH public key from '{ssh_public_key_path}'...")
    with open(ssh_public_key_path) as f:
        ssh_public_key = f.read()

    print(f"[{vm_size}] Creating Virtual Machine '{vm_name}'...")
    if STOP_EVENT.is_set():
        raise Exception("Operation cancelled")
    vm_poller = compute_client.virtual_machines.begin_create_or_update(
        resource_group_name,
        vm_name,
        {
            "location": location,
            "hardware_profile": {"vm_size": vm_size},
            "storage_profile": {
                "image_reference": {
                    "id": image_id,
                }
            },
            "os_profile": {
                "computer_name": vm_name,
                "admin_username": username,
                "linux_configuration": {
                    "disable_password_authentication": True,
                    "ssh": {
                        "public_keys": [
                            {
                                "path": f"/home/{username}/.ssh/authorized_keys",
                                "key_data": ssh_public_key,
                            }
                        ]
                    },
                },
            },
            "network_profile": {"network_interfaces": [{"id": nic_result.id}]},
        },
    )
    vm_result = vm_poller.result()

    register_resource_group(resource_group_name)  # Register the created resource group

    return {
        "vm": vm_result,
        "public_ip": pip_result.ip_address,
        "resource_group": resource_group_name,
    }


def delete_resource_group(subscription_id, resource_group_name):
    """Deletes a resource group and all its resources."""
    print(f"Deleting resource group '{resource_group_name}'...")
    try:
        credential = DefaultAzureCredential()
        resource_client = ResourceManagementClient(credential, subscription_id)
        delete_poller = resource_client.resource_groups.begin_delete(resource_group_name)
        delete_poller.result()
        print(f"Resource group '{resource_group_name}' deleted successfully.")
        unregister_resource_group(resource_group_name)
    except HttpResponseError as e:
        print(f"Error deleting resource group '{resource_group_name}': {e.message}")
    except Exception as e:
        print(f"An unexpected error occurred during resource group deletion: {e}")


def wait_for_vm(subscription_id, resource_group_name, vm_name):
    """
    Wait for VM to be in a running state.
    """
    print(f"[{vm_name}] Waiting for VM to be running...")
    credential = DefaultAzureCredential()
    compute_client = ComputeManagementClient(credential, subscription_id)

    wait_timeout = time.time() + 600  # 10-minute timeout for VM to start
    while time.time() < wait_timeout:
        if STOP_EVENT.is_set():
            raise Exception("Operation cancelled")
        try:
            vm_details = compute_client.virtual_machines.get(resource_group_name, vm_name, expand="instanceView")

            provisioning_state = vm_details.provisioning_state
            power_state = vm_details.instance_view.statuses[1].code

            print(f"[{vm_name}] Provisioning state: {provisioning_state}, Power state: {power_state}")

            if provisioning_state == "Succeeded" and "running" in power_state.lower():
                print(f"[{vm_name}] VM is running.")

                # Get appropriate wait time based on instance size
                initial_wait, _ = get_wait_times(vm_details.hardware_profile.vm_size)
                print(f"[{vm_name}] Waiting {initial_wait} seconds for services to start...")
                time.sleep(initial_wait)
                return True

            if provisioning_state in ["Failed", "Canceled"]:
                raise Exception(f"VM provisioning failed with state: {provisioning_state}")

            time.sleep(20)

        except HttpResponseError as e:
            print(f"Warning: a recoverable error occurred while waiting for VM: {e.message}")
            time.sleep(20)

    raise Exception(f"VM '{vm_name}' did not start within the 10-minute timeout.")


def get_io_properties(public_ip, vm_size, private_key_path, username="scyllaadm", override=False):
    """
    SSH into instance using Paramiko SSH client and get IO properties.
    (This function is mostly platform-agnostic and can be reused with minor changes)
    """
    if not os.path.exists(private_key_path):
        raise Exception(f"SSH private key file not found: {private_key_path}")

    if os.name != "nt":
        key_permissions = os.stat(private_key_path).st_mode
        if key_permissions & stat.S_IRWXG or key_permissions & stat.S_IRWXO:
            print("Warning: SSH key file has group/other permissions. Fixing permissions to 0600.")
            os.chmod(private_key_path, stat.S_IRUSR | stat.S_IWUSR)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    last_error = None
    for attempt in range(5):
        if STOP_EVENT.is_set():
            raise Exception("Operation cancelled")
        try:
            print(f"Attempting SSH connection (attempt {attempt + 1}/5) to {public_ip} as {username}...")
            ssh.connect(
                hostname=public_ip,
                username=username,
                key_filename=private_key_path,
                timeout=60,
                allow_agent=False,
                look_for_keys=False,
            )
            print("SSH connection established successfully")
            break
        except Exception as e:
            last_error = str(e)
            print(f"SSH connection failed: {last_error}")
            if attempt < 4:
                print("Retrying in 30 seconds...")
                time.sleep(30)
            else:
                raise Exception(f"Failed to connect via SSH after 5 attempts: {last_error}")

    if override:
        print("Override flag detected - removing existing IO properties file and running scylla_io_setup...")
        try:
            # Remove existing io_properties.yaml file if it exists
            _, stdout, _ = ssh.exec_command("sudo rm -f /etc/scylla.d/io_properties.yaml")
            stdout.read()  # Wait for command to complete

            # Run scylla_io_setup to regenerate the file
            print("Running scylla_io_setup to generate new IO properties...")
            _, stdout, stderr = ssh.exec_command("sudo scylla_io_setup")
            output = stdout.read().decode()
            error_output = stderr.read().decode()

            if error_output:
                print(f"scylla_io_setup stderr: {error_output}")
            if output:
                print(f"scylla_io_setup output: {output}")

            print("scylla_io_setup completed successfully")
        except Exception as e:
            print(f"Warning: Error during override setup: {e}")

    _, io_wait_minutes = get_wait_times(vm_size)
    max_attempts = io_wait_minutes * 6

    print(f"Waiting for IO properties file (up to {io_wait_minutes} minutes for {vm_size})...")

    try:
        for i in range(max_attempts):
            if STOP_EVENT.is_set():
                raise Exception("Operation cancelled")
            _, stdout, _ = ssh.exec_command('test -f /etc/scylla.d/io_properties.yaml && echo "exists"')
            if stdout.read().decode().strip() == "exists":
                break
            if i % 6 == 0:
                minutes_elapsed = i // 6
                print(f"  Still waiting... ({minutes_elapsed}/{io_wait_minutes} minutes)")
            time.sleep(10)
        else:
            raise Exception(f"IO properties file not found after {io_wait_minutes} minutes for {vm_size}")

        print("IO properties file found!")
        _, stdout, _ = ssh.exec_command("cat /etc/scylla.d/io_properties.yaml")
        content = stdout.read().decode()
        return yaml.safe_load(content)
    finally:
        ssh.close()


def get_vm_sizes_in_family(subscription_id, location, family_prefix):
    """
    Get all available VM sizes for a given family in a location.
    """
    print(f"Querying available VM sizes for family '{family_prefix}' in {location}...")
    try:
        credential = DefaultAzureCredential()
        compute_client = ComputeManagementClient(credential, subscription_id)

        vm_sizes = compute_client.virtual_machine_sizes.list(location=location)

        family_vm_sizes = [size.name for size in vm_sizes if size.name.startswith(family_prefix)]

        # Sort the VM sizes naturally (e.g., L8s, L16s, L32s)
        family_vm_sizes.sort(key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split("([0-9]+)", s)])

        return family_vm_sizes
    except HttpResponseError as e:
        print(f"Error querying VM sizes: {e.message}")
        return []
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return []


def format_output(properties, vm_size=""):
    """
    Format IO properties output as human-readable table
    """
    return format_as_table(properties, vm_size)


def format_as_table(properties, vm_size):
    """
    Format properties as a table
    """
    if not properties:
        return f"No properties found for {vm_size}"

    rows = []

    # ... (rest of the function is the same)
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
    output = f"IO Properties for {vm_size}:\n"
    output += "-" * 50 + "\n"
    for row in rows:
        output += f"{row[0]:<30} | {row[1]}\n"
    return output


def process_vm_parallel(
    vm_size, subscription_id, location, image_id, ssh_public_key_path, private_key_path, username, override=False
):
    """
    Process a single VM (for parallel execution).
    Ensures proper resource cleanup even when errors occur.
    """
    start_time = time.time()
    run_id = str(uuid.uuid4())[:8]
    vm_info = None
    resource_group_name = f"io-test-rg-{vm_size.replace('_', '-')}-{run_id}"

    try:
        if STOP_EVENT.is_set():
            raise Exception("Operation cancelled")
        print(f"[{vm_size}] Starting processing...")

        vm_info = launch_vm(
            subscription_id=subscription_id,
            location=location,
            vm_size=vm_size,
            image_id=image_id,
            username=username,
            ssh_public_key_path=ssh_public_key_path,
            run_id=run_id,
        )

        wait_for_vm(
            subscription_id=subscription_id,
            resource_group_name=resource_group_name,
            vm_name=vm_info["vm"].name,
        )

        properties = get_io_properties(
            public_ip=vm_info["public_ip"],
            vm_size=vm_size,
            private_key_path=private_key_path,
            username=username,
            override=override,
        )

        elapsed_time = time.time() - start_time
        print(f"[{vm_size}] Completed processing in {elapsed_time:.1f} seconds.")

        return vm_size, properties

    except Exception as e:
        elapsed_time = time.time() - start_time
        print(f"[{vm_size}] Error after {elapsed_time:.1f} seconds: {e}")
        return vm_size, {"error": str(e)}

    finally:
        if vm_info:
            delete_resource_group(subscription_id, vm_info["resource_group"])


def load_config(config_file="config_azure.yaml"):
    """Load configuration from YAML file."""
    if not os.path.exists(config_file):
        return {}
    try:
        with open(config_file) as f:
            config = yaml.safe_load(f)
        return config if config else {}
    except Exception as e:
        print(f"Warning: Could not load config file {config_file}: {e}")
        return {}


def save_config(config, config_file="config_azure.yaml"):
    """Save configuration to YAML file."""
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


def sort_azure_vm_sizes(vm_sizes):
    """
    Sort Azure VM sizes logically.
    e.g., Standard_L8s_v2, Standard_L16s_v2, Standard_L32s_v2
    """

    def sort_key(vm_size):
        parts = vm_size.split("_")
        family = parts[0]
        sub_family = ""
        size_info = ""

        if len(parts) > 1:
            # Regex to find the numeric part and any suffix (like 's', 'ds', 'ms')
            match = re.match(r"([a-zA-Z]*)(\d+)([a-zA-Z]*)", parts[1])
            if match:
                sub_family = match.group(1)
                size_info = int(match.group(2))
            else:
                size_info = parts[1]

        return (family, sub_family, size_info, vm_size)

    return sorted(vm_sizes, key=sort_key)


def update_azure_params_yaml(all_properties, params_file_path):
    """
    Update azure_io_params.yaml with IO properties from all processed VMs.
    """
    update_count = 0
    params_file = get_absolute_params_path(params_file_path)
    lock_file = f"{params_file}.lock"
    print(f"Updating Azure IO parameters in {params_file}...")

    with open(lock_file, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            try:
                if os.path.exists(params_file):
                    with open(params_file) as f:
                        params_data = yaml.safe_load(f) or {}
                else:
                    params_data = {}
            except Exception as e:
                print(f"Warning: Could not load params file {params_file}, starting fresh. Error: {e}")
                params_data = {}

            sorted_vm_sizes = sort_azure_vm_sizes(all_properties.keys())

            for vm_size in sorted_vm_sizes:
                properties = all_properties[vm_size]
                if "error" in properties:
                    print(f"Skipping {vm_size} due to processing error: {properties['error']}")
                    continue

                if "disks" in properties and len(properties["disks"]) > 0:
                    disk_props = properties["disks"][0]
                    io_params = {
                        "read_iops": int(disk_props.get("read_iops", 0)),
                        "read_bandwidth": int(disk_props.get("read_bandwidth", 0)),
                        "write_iops": int(disk_props.get("write_iops", 0)),
                        "write_bandwidth": int(disk_props.get("write_bandwidth", 0)),
                    }

                    # Create a simplified structure for the YAML file
                    update_content = {"disks": [io_params]}

                    if vm_size not in params_data or params_data[vm_size] != update_content:
                        params_data[vm_size] = update_content
                        update_count += 1
                        print(f"Parameters for {vm_size} will be updated.")
                    else:
                        print(f"No changes needed for {vm_size}.")
                else:
                    print(f"No disk properties found for {vm_size}, skipping.")

            if update_count > 0:
                try:
                    # Sort the final dictionary before writing
                    sorted_params_data = {k: params_data[k] for k in sort_azure_vm_sizes(params_data.keys())}

                    with open(params_file, "w") as f:
                        yaml.safe_dump(sorted_params_data, f, default_flow_style=False, sort_keys=False)
                    print(f"Successfully updated {update_count} VM types in {params_file}")
                except Exception as e:
                    print(f"Error writing updates to {params_file}: {e}")
            else:
                print("No updates needed for the parameters file.")
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

    return update_count


class SetProvidedAction(argparse.Action):
    """Custom action to track which arguments were provided"""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        if not hasattr(namespace, "_provided_args"):
            namespace._provided_args = set()
        namespace._provided_args.add(self.dest)


# Global variable to track created resource groups for cleanup
CREATED_RESOURCE_GROUPS = set()
RG_LOCK = threading.Lock()
STOP_EVENT = threading.Event()


def register_resource_group(resource_group_name):
    with RG_LOCK:
        CREATED_RESOURCE_GROUPS.add(resource_group_name)


def unregister_resource_group(resource_group_name):
    with RG_LOCK:
        CREATED_RESOURCE_GROUPS.discard(resource_group_name)


def cleanup_all_tracked_resources():
    """Cleanup all tracked resource groups."""
    if not CREATED_RESOURCE_GROUPS:
        return

    print("\nCleaning up remaining resource groups...")
    # Create a copy to iterate while modifying
    rgs_to_delete = list(CREATED_RESOURCE_GROUPS)

    # We need a subscription ID. Since this function is called from a signal handler
    # or exit, we might not have it easily available unless we store it globally too.
    # However, create_resource_group calls rely o providing subscription_id.
    # We will assume that if we are here, we can try to delete using a default credential
    # and we need the subscription id.
    # To fix this, we will store subscription_id globally when it's first available.

    if not GLOBAL_SUBSCRIPTION_ID:
        print("Warning: Subscription ID not available, cannot perform cleanup.")
        return

    print(f"Starting parallel cleanup for {len(rgs_to_delete)} resource groups...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {
            executor.submit(delete_resource_group, GLOBAL_SUBSCRIPTION_ID, rg_name): rg_name
            for rg_name in rgs_to_delete
        }
        for future in concurrent.futures.as_completed(futures):
            rg_name = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"Error deleting {rg_name}: {e}")


GLOBAL_SUBSCRIPTION_ID = None


def signal_handler(sig, frame):
    print("\nProcess interrupted! Stopping threads and cleaning up resources...")
    STOP_EVENT.set()
    cleanup_all_tracked_resources()
    print("Cleanup complete. Exiting.")
    os._exit(1)


# Register signal handler
signal.signal(signal.SIGINT, signal_handler)


def cleanup_orphaned_resources(subscription_id, prefix="io-test-rg-"):
    """
    Find and delete all resource groups matching the prefix.
    """
    print(f"Checking for orphaned resource groups matching '{prefix}'...")
    try:
        credential = DefaultAzureCredential()
        resource_client = ResourceManagementClient(credential, subscription_id)

        # List all resource groups
        rgs = resource_client.resource_groups.list()

        orphaned_rgs = [rg for rg in rgs if rg.name and rg.name.startswith(prefix)]

        if not orphaned_rgs:
            print("No orphaned resource groups found.")
            return

        print(f"Found {len(orphaned_rgs)} orphaned resource groups: {[rg.name for rg in orphaned_rgs]}")
        confirm = input("Are you sure you want to delete all these resource groups? (y/N): ")

        if confirm.lower() == "y":
            print(f"Deleting {len(orphaned_rgs)} resource groups in parallel...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                futures = {
                    executor.submit(delete_resource_group, subscription_id, rg.name): rg.name for rg in orphaned_rgs
                }
                for future in concurrent.futures.as_completed(futures):
                    rg_name = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        print(f"Error deleting {rg_name}: {e}")
        else:
            print("Cleanup cancelled.")

    except Exception as e:
        print(f"Error during cleanup: {e}")


def main():
    parser = argparse.ArgumentParser(description="Launch Azure VM and get Scylla IO properties")
    # Azure-specific arguments
    parser.add_argument("--subscription-id", action=SetProvidedAction, help="Azure Subscription ID")
    parser.add_argument("--location", action=SetProvidedAction, help="Azure location (e.g., eastus)")
    parser.add_argument("--image-id", action=SetProvidedAction, help="Azure Image ID or URN")
    parser.add_argument(
        "--vm-size", action=SetProvidedAction, help="VM size (e.g., Standard_L8s_v2) or family (e.g., Standard_L_v2)"
    )
    parser.add_argument(
        "--private-key-path", action=SetProvidedAction, help="Path to private SSH key file for connection"
    )
    parser.add_argument(
        "--ssh-public-key-path", action=SetProvidedAction, help="Path to public SSH key file for VM creation"
    )

    # Common arguments
    parser.add_argument("--username", default="scyllaadm", help="SSH username (default: scyllaadm)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without launching VMs")
    parser.add_argument("--max-vms", type=int, help="Maximum number of VMs to process for a family")
    parser.add_argument("--parallel", type=int, default=1, help="Number of VMs to process in parallel")
    parser.add_argument("--config", default="config_azure.yaml", help="Configuration file")
    parser.add_argument("--save-config", action="store_true", help="Save current settings to config file")
    parser.add_argument("--update-azure-params", action="store_true", help="Update azure_io_params.yaml")
    parser.add_argument(
        "--azure-params-file", default="../../common/azure_io_params.yaml", help="Path to azure_io_params.yaml file"
    )
    parser.add_argument("--override", action="store_true", help="Force re-running scylla_io_setup")
    parser.add_argument("--cleanup-orphaned", action="store_true", help="Delete all orphaned resource groups and exit")

    args = parser.parse_args()

    config = load_config(args.config)
    provided_args = getattr(args, "_provided_args", set())

    for key, value in vars(args).items():
        if key in provided_args:
            config[key] = value

    # Set defaults from config or command line
    for key in [
        "subscription_id",
        "location",
        "image_id",
        "vm_size",
        "private_key_path",
        "ssh_public_key_path",
        "username",
        "parallel",
        "max_vms",
        "dry_run",
        "override",
        "azure_params_file",
        "update_azure_params",
    ]:
        setattr(args, key, config.get(key, getattr(args, key)))

    if args.save_config:
        save_config(config, args.config)

    # Set global subscription ID for signal handler
    global GLOBAL_SUBSCRIPTION_ID
    GLOBAL_SUBSCRIPTION_ID = args.subscription_id

    # Check for cleanup request
    if args.cleanup_orphaned:
        if not args.subscription_id:
            parser.error("--subscription-id is required for cleanup")
        cleanup_orphaned_resources(args.subscription_id)
        return

    # Validate required arguments
    required_args = ["subscription_id", "location", "image_id", "vm_size", "private_key_path", "ssh_public_key_path"]
    if not args.dry_run:
        for arg in required_args:
            if not getattr(args, arg):
                parser.error(f"--{arg.replace('_', '-')} is required")

    # Expand user paths
    if args.private_key_path:
        args.private_key_path = os.path.expanduser(args.private_key_path)
    if args.ssh_public_key_path:
        args.ssh_public_key_path = os.path.expanduser(args.ssh_public_key_path)

    # Heuristic replaced with availability check
    print(f"Querying available VM sizes in {args.location}...")
    try:
        credential = DefaultAzureCredential()
        compute_client = ComputeManagementClient(credential, args.subscription_id)
        # Ensure name is not None
        all_vm_sizes = [s.name for s in compute_client.virtual_machine_sizes.list(location=args.location) if s.name]
    except Exception as e:
        print(f"Error listing VM sizes: {e}")
        return

    target_vm_sizes = []

    # Check for exact match first
    if args.vm_size in all_vm_sizes:
        target_vm_sizes = [args.vm_size]
        print(f"Found exact match for '{args.vm_size}'.")
    elif "*" in args.vm_size:
        # Wildcard match
        pattern = re.escape(args.vm_size).replace(r"\*", ".*")
        regex = re.compile(f"^{pattern}$")
        target_vm_sizes = [s for s in all_vm_sizes if regex.match(s)]

        if target_vm_sizes:
            # Sort naturally
            target_vm_sizes.sort(key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split("([0-9]+)", s)])
            print(f"Found {len(target_vm_sizes)} VM sizes matching wildcard pattern '{args.vm_size}'")
        else:
            print(f"Error: No VM sizes match pattern '{args.vm_size}' in {args.location}.")
            return
    else:
        # Filter by prefix or suffix
        target_vm_sizes = [s for s in all_vm_sizes if s.startswith(args.vm_size) or s.endswith(args.vm_size)]
        if target_vm_sizes:
            # Sort naturally
            target_vm_sizes.sort(key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split("([0-9]+)", s)])
            print(f"Found {len(target_vm_sizes)} VM sizes matching '{args.vm_size}'")
        else:
            print(f"Error: '{args.vm_size}' is not a valid VM size, prefix, or suffix in {args.location}.")
            return

    all_properties = {}

    if len(target_vm_sizes) > 1:
        print(f"Detected list of VMs to process: {target_vm_sizes}")
        if args.max_vms and len(target_vm_sizes) > args.max_vms:
            target_vm_sizes = target_vm_sizes[: args.max_vms]
            print(f"Limiting to first {args.max_vms} VM sizes: {target_vm_sizes}")

        if args.dry_run:
            print(f"Dry-run: Would process {len(target_vm_sizes)} VMs.")
            return

        try:
            response = input(f"About to launch {len(target_vm_sizes)} VMs. This may incur costs. Continue? (y/N): ")
            if response.lower() != "y":
                print("Operation cancelled by user.")
                return
        except EOFError:
            print("Non-interactive environment detected. Skipping confirmation.")

    elif len(target_vm_sizes) == 1:
        if args.dry_run:
            print(f"Dry-run: Would process VM {target_vm_sizes[0]}.")
            return

    # Process VMs (single or multiple)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = {
            executor.submit(
                process_vm_parallel,
                vm_size,
                args.subscription_id,
                args.location,
                args.image_id,
                args.ssh_public_key_path,
                args.private_key_path,
                args.username,
                args.override,
            ): vm_size
            for vm_size in target_vm_sizes
        }
        for future in concurrent.futures.as_completed(futures):
            vm_size, properties = future.result()
            all_properties[vm_size] = properties

            if args.update_azure_params and not args.dry_run:
                update_azure_params_yaml({vm_size: properties}, args.azure_params_file)

    print("\n=== IO Properties Summary ===")
    for vm_size, props in sorted(all_properties.items()):
        print(f"\n{vm_size}:")
        if "error" in props:
            print(f"  Error: {props['error']}")
        else:
            print(format_output(props, vm_size))


if __name__ == "__main__":
    main()
