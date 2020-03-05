#!/usr/bin/env python3
#
# Copyright 2020 ScyllaDB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import json
import subprocess
import yaml
import time
import logging
from lib.log import setup_logging
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urljoin


LOGGER = logging.getLogger(__name__)


class ScyllaAmiConfigurator:
    AMI_CONF_DEFAULTS = {
        'scylla_yaml': {
            'cluster_name': "scylladb-cluster-%s" % int(time.time()),
            'experimental': False,
            'auto_bootstrap': True,
            'listen_address': "",  # will be configured as a private IP when instance meta data is read
            'broadcast_rpc_address': "",  # will be configured as a private IP when instance meta data is read
            'endpoint_snitch': "org.apache.cassandra.locator.Ec2Snitch",
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
    }

    INSTANCE_METADATA_URL = "http://169.254.169.254/latest/"
    DISABLE_START_FILE_PATH = Path("/etc/scylla/ami_disabled")

    def __init__(self, scylla_yaml_path="/etc/scylla/scylla.yaml"):
        self.scylla_yaml_path = Path(scylla_yaml_path)
        self.scylla_yaml_example_path = Path(scylla_yaml_path + ".example")
        self._scylla_yaml = {}
        self._instance_user_data = None

    @property
    def scylla_yaml(self):
        if not self._scylla_yaml:
            with self.scylla_yaml_path.open() as scylla_yaml_file:
                self._scylla_yaml = yaml.load(scylla_yaml_file, Loader=yaml.SafeLoader)
        return self._scylla_yaml

    def save_scylla_yaml(self):
        LOGGER.info("Saving %s", self.scylla_yaml_path)
        with self.scylla_yaml_path.open("w") as scylla_yaml_file:
            return yaml.dump(data=self.scylla_yaml, stream=scylla_yaml_file)

    def get_instance_metadata(self, path, fail=False):
        meta_data_url = urljoin(self.INSTANCE_METADATA_URL, path)
        LOGGER.info("Getting '%s'...", meta_data_url)
        with urlopen(meta_data_url) as url:
            try:
                meta_data = url.read().decode("utf-8")
                return meta_data
            except Exception as error:
                err_msg = "Unable to get instance metadata '{path}': {error}".format(**locals())
                if fail:
                    LOGGER.critical(err_msg)
                else:
                    LOGGER.warning(err_msg)
                    return ""

    @property
    def instance_user_data(self):
        if self._instance_user_data is None:
            try:
                raw_user_data = self.get_instance_metadata("user-data")
                LOGGER.debug("Got user-data: %s", raw_user_data)
                self._instance_user_data = json.loads(raw_user_data) if raw_user_data.strip() else {}
                LOGGER.debug("JSON parsed user-data: %s", self._instance_user_data)
            except Exception as e:
                LOGGER.warning("Error getting user data: %s. Will use defaults!", e)
                self._instance_user_data = {}
        return self._instance_user_data

    def updated_ami_conf_defaults(self):
        private_ip = self.get_instance_metadata("meta-data/local-ipv4", fail=True)
        self.AMI_CONF_DEFAULTS["scylla_yaml"]["listen_address"] = private_ip
        self.AMI_CONF_DEFAULTS["scylla_yaml"]["broadcast_rpc_address"] = private_ip
        self.AMI_CONF_DEFAULTS["scylla_yaml"]["seed_provider"][0]['parameters'][0]['seeds'] = private_ip

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

        for param in self.AMI_CONF_DEFAULTS["scylla_yaml"]:
            if param not in new_scylla_yaml_config:
                default_param_value = self.AMI_CONF_DEFAULTS["scylla_yaml"][param]
                LOGGER.info("Setting default {param}={default_param_value}".format(**locals()))
                self.scylla_yaml[param] = default_param_value
        self.scylla_yaml_path.rename(str(self.scylla_yaml_example_path))
        self.save_scylla_yaml()

    def configure_scylla_startup_args(self):
        default_scylla_startup_args = self.AMI_CONF_DEFAULTS["scylla_startup_args"]
        if self.instance_user_data.get("scylla_startup_args", default_scylla_startup_args):
            LOGGER.warning("Setting of Scylla startup args currently unsupported")

    def set_developer_mode(self):
        default_developer_mode = self.AMI_CONF_DEFAULTS["developer_mode"]
        if self.instance_user_data.get("developer_mode", default_developer_mode):
            LOGGER.info("Setting up developer mode")
            subprocess.run(['/usr/sbin/scylla_dev_mode_setup', '--developer-mode', '1'], timeout=60, check=True)

    def run_post_configuration_script(self):
        post_configuration_script = self.instance_user_data.get("post_configuration_script")
        if post_configuration_script:
            try:
                default_timeout = self.AMI_CONF_DEFAULTS["post_configuration_script_timeout"]
                script_timeout = self.instance_user_data.get("post_configuration_script_timeout", default_timeout)
                decoded_script = base64.b64decode(post_configuration_script)
                LOGGER.info("Running post configuration script:\n%s", decoded_script)
                subprocess.run(decoded_script, check=True, shell=True, timeout=int(script_timeout))
            except Exception as e:
                LOGGER.error("Post configuration script failed: %s", e)

    def start_scylla_on_first_boot(self):
        default_start_scylla_on_first_boot = self.AMI_CONF_DEFAULTS["start_scylla_on_first_boot"]
        if not self.instance_user_data.get("start_scylla_on_first_boot", default_start_scylla_on_first_boot):
            LOGGER.info("Disabling Scylla start on first boot")
            self.DISABLE_START_FILE_PATH.touch()

    def configure(self):
        self.configure_scylla_yaml()
        self.configure_scylla_startup_args()
        self.set_developer_mode()
        self.run_post_configuration_script()
        self.start_scylla_on_first_boot()


if __name__ == "__main__":
    setup_logging()
    sac = ScyllaAmiConfigurator()
    sac.configure()
