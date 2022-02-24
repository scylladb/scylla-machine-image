#!/usr/bin/env python3
#
# Copyright 2021 ScyllaDB
#
# SPDX-License-Identifier: Apache-2.0

import base64
import json
import logging
import pathlib
import subprocess
import sys

from lib.log import setup_logging

LOGGER = logging.getLogger(__name__)
DISABLE_FILE_PATH = '/etc/scylla/machine_image_post_start_configured'


class ScyllaMachineImagePostStart:

    def __init__(self):
        self._instance_user_data = None
        self._cloud_instance = None

    @property
    def cloud_instance(self):
        if not self._cloud_instance:
            from lib.scylla_cloud import get_cloud_instance
            self._cloud_instance = get_cloud_instance()
        return self._cloud_instance

    @property
    def instance_user_data(self):
        if self._instance_user_data is None:
            try:
                raw_user_data = self.cloud_instance.user_data
                LOGGER.info(f"Got user-data: {raw_user_data}")
                self._instance_user_data = json.loads(raw_user_data) if raw_user_data.strip() else {}
                LOGGER.debug(f"JSON parsed user-data: {self._instance_user_data}")
            except Exception as e:
                LOGGER.warning(f"Error getting user data: {e}")
        return self._instance_user_data

    def run_post_start_script(self):
        post_start_script = self.instance_user_data.get("post_start_script")
        if post_start_script:
            try:
                decoded_script = base64.b64decode(post_start_script)
                LOGGER.info(f"Running post start script:\n{decoded_script}")
                subprocess.run(decoded_script, check=True, shell=True, timeout=600)
                return True
            except Exception as e:
                LOGGER.error(f"Post start script failed: {e}")


if __name__ == "__main__":
    setup_logging()
    init = ScyllaMachineImagePostStart()
    if init.run_post_start_script():
        pathlib.Path(DISABLE_FILE_PATH).touch()
