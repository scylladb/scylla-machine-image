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

import sys
import logging
import pathlib


class ExitOnExceptionHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        # exit the application when logged message is above error logging level
        if record.levelno >= logging.ERROR:
            sys.exit(1)


def setup_logging(log_level=logging.INFO, log_dir_path="/var/lib/scylla/logs"):
    log_dir = pathlib.Path(log_dir_path)
    ami_log_path = log_dir / "ami.log"

    log_dir.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    formatter = logging.Formatter("%(asctime)s - [%(module)s] - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler(str(ami_log_path))
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(ExitOnExceptionHandler())
    root_logger.setLevel(log_level)
