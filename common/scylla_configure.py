#!/usr/bin/env python3
#
# Copyright 2020 ScyllaDB
#
# SPDX-License-Identifier: Apache-2.0

import base64
import subprocess
import yaml
import time
import logging
import sys
from textwrap import dedent
from datetime import datetime
from lib.scylla_cloud import scylla_excepthook
from lib.log import setup_logging
from lib.user_data import UserData
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class ScyllaMachineImageConfigurator(UserData):
    CONF_DEFAULTS = {
        'scylla_yaml': {
            'cluster_name': "scylladb-cluster-%s" % int(time.time()),
            'auto_bootstrap': True,
            'listen_address': "",  # will be configured as a private IP when instance meta data is read
            'broadcast_rpc_address': "",  # will be configured as a private IP when instance meta data is read
            'rpc_address': "0.0.0.0",
            'seed_provider': [{'class_name': 'org.apache.cassandra.locator.SimpleSeedProvider',
                               'parameters': [{'seeds': ""}]}],  # will be configured as a private IP when
                                                                 # instance meta data is read
        },
        'scylla_startup_args': [],  # TODO: implement
        'developer_mode': False,
        'post_configuration_script': '',
        'post_configuration_script_timeout': 600,  # seconds
        'start_scylla_on_first_boot': True,
        'data_device': 'auto',  # Supported options:
                                #   instance_store - find all ephemeral devices (only for AWS)
                                #   attached - find all attached devices and use them
                                #   auto - automatically select devices using following strategy:
                                #       GCE: select attached NVMe.
                                #       AWS:
                                #           if ephemeral found - use them
                                #           else if attached EBS found use them
                                #           else: fail create_devices
        'raid_level': 0  # Default raid level is 0, supported raid 0, 5
    }

    def __init__(self, scylla_yaml_path="/etc/scylla/scylla.yaml"):
        self.scylla_yaml_path = Path(scylla_yaml_path)
        self.scylla_yaml_example_path = Path(scylla_yaml_path + ".example")
        self._scylla_yaml = {}
        super().__init__()

    @property
    def scylla_yaml(self):
        if not self._scylla_yaml:
            with self.scylla_yaml_path.open() as scylla_yaml_file:
                self._scylla_yaml = yaml.load(scylla_yaml_file, Loader=yaml.SafeLoader)
        return self._scylla_yaml

    def save_scylla_yaml(self):
        LOGGER.info("Saving %s", self.scylla_yaml_path)
        with self.scylla_yaml_path.open("w") as scylla_yaml_file:
            now = datetime.now()
            scylla_yaml_file.write(dedent(f"""
                # Generated by Scylla Machine Image at {now}
                # See '/etc/scylla/scylla.yaml.example' with the full list of supported configuration
                # options and their descriptions.\n"""[1:]))
            return yaml.dump(data=self.scylla_yaml, stream=scylla_yaml_file)

    def updated_ami_conf_defaults(self):
        private_ip = self.cloud_instance.private_ipv4()
        self.CONF_DEFAULTS["scylla_yaml"]["listen_address"] = private_ip
        self.CONF_DEFAULTS["scylla_yaml"]["broadcast_rpc_address"] = private_ip
        self.CONF_DEFAULTS["scylla_yaml"]["seed_provider"][0]['parameters'][0]['seeds'] = private_ip
        self.CONF_DEFAULTS["scylla_yaml"]["endpoint_snitch"] = self.cloud_instance.endpoint_snitch

    def configure_scylla_yaml(self):
        self.updated_ami_conf_defaults()
        LOGGER.info("Going to create scylla.yaml...")
        new_scylla_yaml_config = self.instance_user_data.get("scylla_yaml", {})
        if new_scylla_yaml_config:
            LOGGER.info("Setting params from user-data...")
            for param in new_scylla_yaml_config:
                param_value = new_scylla_yaml_config[param]
                LOGGER.info("Setting {param}={param_value}".format(**locals()))
                self.scylla_yaml[param] = param_value

        for param in self.CONF_DEFAULTS["scylla_yaml"]:
            if param not in new_scylla_yaml_config:
                default_param_value = self.CONF_DEFAULTS["scylla_yaml"][param]
                LOGGER.info("Setting default {param}={default_param_value}".format(**locals()))
                self.scylla_yaml[param] = default_param_value
        self.scylla_yaml_path.rename(str(self.scylla_yaml_example_path))
        self.save_scylla_yaml()

    def configure_scylla_startup_args(self):
        default_scylla_startup_args = self.CONF_DEFAULTS["scylla_startup_args"]
        if self.instance_user_data.get("scylla_startup_args", default_scylla_startup_args):
            LOGGER.warning("Setting of Scylla startup args currently unsupported")

    def set_developer_mode(self):
        default_developer_mode = self.CONF_DEFAULTS["developer_mode"]
        if self.instance_user_data.get("developer_mode", default_developer_mode) or self.cloud_instance.is_dev_instance_type():
            LOGGER.info("Setting up developer mode")
            subprocess.run(['/usr/sbin/scylla_dev_mode_setup', '--developer-mode', '1'], timeout=60, check=True)

    def run_post_configuration_script(self):
        post_configuration_script = self.instance_user_data.get("post_configuration_script")
        if post_configuration_script:
            try:
                default_timeout = self.CONF_DEFAULTS["post_configuration_script_timeout"]
                script_timeout = self.instance_user_data.get("post_configuration_script_timeout", default_timeout)
                try:
                    decoded_script = base64.b64decode(post_configuration_script)
                except binascii.Error:
                    decoded_script = post_configuration_script
                LOGGER.info("Running post configuration script:\n%s", decoded_script)
                subprocess.run(decoded_script, check=True, shell=True, timeout=int(script_timeout))
            except Exception as e:
                scylla_excepthook(*sys.exc_info())
                LOGGER.error("Post configuration script failed: %s", e)
                sys.exit(1)

    def start_scylla_on_first_boot(self):
        default_start_scylla_on_first_boot = self.CONF_DEFAULTS["start_scylla_on_first_boot"]
        if not self.instance_user_data.get("start_scylla_on_first_boot", default_start_scylla_on_first_boot):
            LOGGER.info("Disabling Scylla start on first boot")
            subprocess.run("/usr/bin/systemctl stop scylla-server.service", shell=True, check=True)

    def create_devices(self):
        device_type = self.instance_user_data.get("data_device", self.CONF_DEFAULTS['data_device'])
        raid_level = self.instance_user_data.get("raid_level", self.CONF_DEFAULTS['raid_level'])
        cmd_create_devices = f"/opt/scylladb/scylla-machine-image/scylla_create_devices --data-device {device_type} --raid-level {raid_level}"
        try:
            LOGGER.info(f"Create scylla data devices as {device_type}")
            subprocess.run(cmd_create_devices, shell=True, check=True)
        except Exception as e:
            scylla_excepthook(*sys.exc_info())
            LOGGER.error("Failed to create devices: %s", e)
            sys.exit(1)

    def configure(self):
        self.configure_scylla_yaml()
        self.configure_scylla_startup_args()
        self.set_developer_mode()
        self.run_post_configuration_script()
        self.start_scylla_on_first_boot()
        self.create_devices()


if __name__ == "__main__":
    setup_logging()
    smi_configurator = ScyllaMachineImageConfigurator()
    smi_configurator.configure()
