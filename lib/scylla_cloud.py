#!/usr/bin/env python3
#
# Copyright 2022 ScyllaDB
#
# SPDX-License-Identifier: Apache-2.0

import asyncio
import base64
import datetime
import functools
import glob
import json
import logging
import os
import re
import socket
import sys
import time
import traceback
import urllib.error
import urllib.request
from abc import ABCMeta, abstractmethod
from datetime import timezone
from subprocess import CalledProcessError, run

import psutil
import traceback_with_variables


def out(cmd, shell=True, timeout=None, encoding="utf-8", ignore_error=False, user=None, group=None):
    try:
        res = run(
            cmd,
            capture_output=True,
            shell=shell,
            timeout=timeout,
            check=not ignore_error,
            encoding=encoding,
            user=user,
            group=group,
        )
    except CalledProcessError as e:
        print(
            f"""
Command '{cmd}' returned non-zero exit status: {e.returncode}
----------  stdout  ----------
{e.stdout.strip()}
------------------------------
----------  stderr  ----------
{e.stderr.strip()}
------------------------------
"""[1:-1]
        )
        raise
    return res.stdout.strip()


def scylla_excepthook(etype, value, tb):
    os.makedirs("/var/tmp/scylla", mode=0o755, exist_ok=True)
    traceback.print_exception(etype, value, tb)
    exc_logger = logging.getLogger(__name__)
    exc_logger.setLevel(logging.DEBUG)
    exc_logger_file = f"/var/tmp/scylla/{os.path.basename(sys.argv[0])}-{os.getpid()}-debug.log"
    exc_logger.addHandler(logging.FileHandler(exc_logger_file))
    traceback_with_variables.print_exc(e=value, file_=traceback_with_variables.LoggerAsFile(exc_logger))
    print(f"Debug log created: {exc_logger_file}")


sys.excepthook = scylla_excepthook


def _curl_one(url, headers=None, method=None, byte=False, timeout=3):
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        if byte:
            return res.read()
        return res.read().decode("utf-8")


# @param headers dict of k:v
def curl(url, headers=None, method=None, byte=False, timeout=3, max_retries=5, retry_interval=5):
    retries = 0
    while True:
        try:
            return _curl_one(url, headers, method, byte, timeout)
        except (TimeoutError, urllib.error.URLError) as e:
            # if HTTP error code is client error (400-499), skip retrying
            if isinstance(e, urllib.error.HTTPError) and e.code in range(400, 500):
                raise
            time.sleep(retry_interval)
            retries += 1
            if retries >= max_retries:
                raise


async def aiocurl(url, headers=None, method=None, byte=False, timeout=3, max_retries=5, retry_interval=5):
    retries = 0
    while True:
        try:
            return _curl_one(url, headers, method, byte, timeout)
        except (TimeoutError, urllib.error.URLError) as e:
            # if HTTP error code is client error (400-499), skip retrying
            if isinstance(e, urllib.error.HTTPError) and e.code in range(400, 500):
                raise
            await asyncio.sleep(retry_interval)
            retries += 1
            if retries >= max_retries:
                raise


def read_one_line(filename):
    try:
        with open(filename) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


class CloudInstance(metaclass=ABCMeta):
    @abstractmethod
    def get_local_disks(self):
        pass

    @abstractmethod
    def get_remote_disks(self):
        pass

    @abstractmethod
    def private_ipv4(self):
        pass

    @abstractmethod
    def is_supported_instance_class(self):
        pass

    @abstractmethod
    def is_dev_instance_type(self) -> bool:
        pass

    @property
    @abstractmethod
    def instancetype(self) -> str:
        pass

    @abstractmethod
    def instance_class(self) -> str:
        pass

    @abstractmethod
    def io_setup(self):
        pass

    @staticmethod
    @abstractmethod
    def check():
        pass

    @property
    @abstractmethod
    def user_data(self):
        pass

    @property
    @abstractmethod
    def nvme_disk_count(self) -> int:
        pass

    @property
    @abstractmethod
    def endpoint_snitch(self):
        pass

    @classmethod
    @abstractmethod
    def identify_dmi(cls):
        pass

    @classmethod
    @abstractmethod
    async def identify_metadata(cls):
        pass

    @classmethod
    async def identify(cls):
        return cls.identify_dmi() or await cls.identify_metadata()


class GcpInstance(CloudInstance):
    """Describe several aspects of the current GCP instance"""

    EPHEMERAL = "ephemeral"
    PERSISTENT = "persistent"
    ROOT = "root"
    META_DATA_BASE_URL = "http://metadata.google.internal/computeMetadata/v1/instance/"
    ENDPOINT_SNITCH = "GoogleCloudSnitch"

    @property
    def endpoint_snitch(self):
        return self.ENDPOINT_SNITCH

    @classmethod
    def identify_dmi(cls):
        product_name = read_one_line("/sys/class/dmi/id/product_name")
        if product_name == "Google Compute Engine":
            return cls
        return None

    @classmethod
    async def identify_metadata(cls):
        """Check if it's GCE instance via DNS lookup to metadata server."""
        try:
            addrlist = socket.getaddrinfo("metadata.google.internal", 80)
        except socket.gaierror:
            return None
        for res in addrlist:
            af, socktype, proto, canonname, sa = res
            if af == socket.AF_INET:
                addr, _port = sa
                if addr == "169.254.169.254":
                    # Make sure it is not on GKE
                    try:
                        await aiocurl(
                            cls.META_DATA_BASE_URL + "machine-type?recursive=false",
                            headers={"Metadata-Flavor": "Google"},
                        )
                    except urllib.error.HTTPError:
                        return None
                    return cls
        return None

    def __instance_metadata(self, path, recursive=False):
        return curl(
            self.META_DATA_BASE_URL + path + f"?recursive={str(recursive).lower()}",
            headers={"Metadata-Flavor": "Google"},
        )

    def is_in_root_devs(self, x, root_devs):
        return any(root_dev.startswith(os.path.join("/dev/", x)) for root_dev in root_devs)

    def _non_root_nvmes(self):
        """get list of nvme disks from os, filter away if one of them is root"""
        nvme_re = re.compile(r"nvme\d+n\d+$")

        root_dev_candidates = [x for x in psutil.disk_partitions() if x.mountpoint == "/"]

        root_devs = [x.device for x in root_dev_candidates]

        nvmes_present = list(filter(nvme_re.match, os.listdir("/dev")))
        return {
            self.ROOT: root_devs,
            self.EPHEMERAL: [x for x in nvmes_present if not self.is_in_root_devs(x, root_devs)],
        }

    def _non_root_disks(self):
        """get list of disks from os, filter away if one of them is root"""
        disk_re = re.compile(r"/dev/sd[b-z]+$")

        root_dev_candidates = [x for x in psutil.disk_partitions() if x.mountpoint == "/"]

        root_devs = [x.device for x in root_dev_candidates]

        disks_present = list(filter(disk_re.match, glob.glob("/dev/sd*")))
        return {
            self.PERSISTENT: [
                x.removeprefix("/dev/")
                for x in disks_present
                if not self.is_in_root_devs(x.removeprefix("/dev/"), root_devs)
            ]
        }

    @property
    def os_disks(self):
        """populate disks from /dev/ and root mountpoint"""
        __os_disks = {}
        nvmes_present = self._non_root_nvmes()
        for k, v in nvmes_present.items():
            __os_disks[k] = v
        disks_present = self._non_root_disks()
        for k, v in disks_present.items():
            __os_disks[k] = v
        return __os_disks

    def get_local_disks(self):
        """return just transient disks"""
        return self.os_disks[self.EPHEMERAL]

    def get_remote_disks(self):
        """return just persistent disks"""
        return self.os_disks[self.PERSISTENT]

    @staticmethod
    def is_nvme(gcpdiskobj):
        """check if disk from GCP metadata is a NVME disk"""
        return gcpdiskobj["interface"] == "NVME"

    def __get_nvme_disks_from_metadata(self):
        """get list of nvme disks from metadata server"""
        try:
            disks_rest = self.__instance_metadata("disks", True)
            disks_obj = json.loads(disks_rest)
            nvme_disks = list(filter(self.is_nvme, disks_obj))
        except Exception as e:
            print("Problem when parsing disks from metadata:")
            print(e)
            nvme_disks = {}
        return nvme_disks

    @functools.cached_property
    def nvme_disk_count(self) -> int:
        """get # of nvme disks available for scylla raid"""
        try:
            ephemeral_disks = self.get_local_disks()
            count_os_disks = len(ephemeral_disks)
        except Exception as e:
            print("Problem when parsing disks from OS:")
            print(e)
            count_os_disks = 0
        nvme_metadata_disks = self.__get_nvme_disks_from_metadata()
        count_metadata_nvme_disks = len(nvme_metadata_disks)
        return count_os_disks if count_os_disks < count_metadata_nvme_disks else count_metadata_nvme_disks

    @functools.cached_property
    def instancetype(self):
        """return the type of this instance, e.g. n2-standard-2"""
        return self.__instance_metadata("machine-type").split("/")[-1]

    @functools.cached_property
    def cpu(self):
        """return the # of cpus of this instance"""
        return psutil.cpu_count()

    @functools.cached_property
    def memory_gb(self):
        """return the size of memory in GB of this instance"""
        return psutil.virtual_memory().total / 1024 / 1024 / 1024

    def instance_size(self):
        """Returns the size of the instance we are running in. i.e.: 2"""
        instance_type_split = self.instancetype.split("-")
        return instance_type_split[2] if len(instance_type_split) > 2 else 0

    def instance_class(self):
        """Returns the class of the instance we are running in. i.e.: n2"""
        return self.instancetype.split("-")[0]

    def instance_purpose(self):
        """Returns the purpose of the instance we are running in. i.e.: standard"""
        return self.instancetype.split("-")[1]

    m1supported = "m1-megamem-96"  # this is the only exception of supported m1 as per https://cloud.google.com/compute/docs/machine-types#m1_machine_types

    def is_unsupported_instance_class(self):
        """Returns if this instance type belongs to unsupported ones for nvmes"""
        if self.instancetype == self.m1supported:
            return False
        return self.instance_class() in ["e2", "f1", "g1", "m2", "m1"]

    def is_supported_instance_class(self):
        """Returns if this instance type belongs to supported ones for nvmes"""
        if self.instancetype == self.m1supported:
            return True
        return self.instance_class() in ["n1", "n2", "n2d", "c2", "z3"]

    def is_recommended_instance_size(self):
        """if this instance has at least 2 cpus, it has a recommended size"""
        return int(self.instance_size()) > 1

    def is_dev_instance_type(self):
        return self.instancetype in ["e2-micro", "e2-small", "e2-medium"]

    @staticmethod
    def get_file_size_by_seek(filename):
        "Get the file size by seeking at end"
        fd = os.open(filename, os.O_RDONLY)
        try:
            return os.lseek(fd, 0, os.SEEK_END)
        finally:
            os.close(fd)

    # note that GCP has 3TB physical devices actually, which they break into smaller 375GB disks and share the same mem with multiple machines
    # this is a reference value, disk size shouldn't be lower than that
    GCP_NVME_DISK_SIZE_2020 = 375

    @functools.cached_property
    def first_nvme_size(self):
        """return the size of first non root NVME disk in GB"""
        ephemeral_disks = self.get_local_disks()
        if len(ephemeral_disks) > 0:
            first_disk = ephemeral_disks[0]
            first_disk_size = self.get_file_size_by_seek(os.path.join("/dev/", first_disk))
            first_disk_size_gb = first_disk_size / 1024 / 1024 / 1024
            if first_disk_size_gb >= self.GCP_NVME_DISK_SIZE_2020:
                return first_disk_size_gb
            logging.warning("First nvme is smaller than lowest expected size. ".format())
            return 0
        return 0

    def is_recommended_instance(self):
        if (
            not self.is_unsupported_instance_class()
            and self.is_supported_instance_class()
            and self.is_recommended_instance_size()
        ):
            # at least 1:2GB cpu:ram ratio , GCP is at 1:4, so this should be fine
            if self.cpu and (self.cpu / self.memory_gb < 0.5):
                disk_count = self.nvme_disk_count
                # to reach max performance for > 16 disks we mandate 32 or more vcpus
                # https://cloud.google.com/compute/docs/disks/local-ssd#performance
                if disk_count >= 16 and self.cpu < 32:
                    logging.warning(
                        "This machine doesn't have enough CPUs for allocated number of NVMEs (at least 32 cpus for >=16 disks). Performance will suffer."
                    )

                if disk_count < 1:
                    logging.warning("No ephemeral disks were found.")
                    return False
                disk_size = self.first_nvme_size
                max_disk_to_ram_ratio = 105
                # 30:1 Disk/RAM ratio must be kept at least(AWS), we relax this a little bit
                # on GCP we are OK with {max_disktoramratio}:1 , n1-standard-2 can cope with 1 disk, not more
                disk_to_ram_ratio = (disk_count * disk_size) / self.memory_gb
                if disk_to_ram_ratio > max_disk_to_ram_ratio:
                    logging.warning(
                        f"Instance disk-to-RAM ratio is {disk_to_ram_ratio}, which is higher than the recommended ratio {max_disk_to_ram_ratio}. Performance may suffer."
                    )
                    return False
                return True
            logging.warning("At least 2G of RAM per CPU is needed. Performance will suffer.")
        return False

    def private_ipv4(self):
        return self.__instance_metadata("network-interfaces/0/ip")

    @staticmethod
    def check():
        pass

    def io_setup(self):
        run("/opt/scylladb/scylla-machine-image/scylla_cloud_io_setup", check=True, shell=True)

    @property
    def user_data(self):
        try:
            return self.__instance_metadata("attributes/user-data")
        except urllib.error.HTTPError:  # empty user-data
            return ""


class AzureInstance(CloudInstance):
    """Describe several aspects of the current Azure instance"""

    EPHEMERAL = "ephemeral"
    PERSISTENT = "persistent"
    SWAP = "swap"
    ROOT = "root"
    ENDPOINT_SNITCH = "AzureSnitch"
    META_DATA_BASE_URL = "http://169.254.169.254/metadata/instance"

    @property
    def endpoint_snitch(self):
        return self.ENDPOINT_SNITCH

    @classmethod
    def identify_dmi(cls):
        # On Azure, we cannot discriminate between Azure and baremetal Hyper-V
        # from DMI.
        # But only Azure has waagent, so we can use it for Azure detection.
        sys_vendor = read_one_line("/sys/class/dmi/id/sys_vendor")
        if sys_vendor == "Microsoft Corporation" and os.path.exists("/etc/waagent.conf"):
            return cls
        return None

    @classmethod
    async def identify_metadata(cls):
        """Check if it's Azure instance via query to metadata server."""
        try:
            await aiocurl(cls.META_DATA_BASE_URL + cls.API_VERSION + "&format=text", headers={"Metadata": "True"})
            return cls
        except (urllib.error.URLError, urllib.error.HTTPError):
            return None

    # as per https://docs.microsoft.com/en-us/azure/virtual-machines/windows/instance-metadata-service?tabs=windows#supported-api-versions
    API_VERSION = "?api-version=2021-01-01"

    def __instance_metadata(self, path):
        """query Azure metadata server"""
        return curl(self.META_DATA_BASE_URL + path + self.API_VERSION + "&format=text", headers={"Metadata": "True"})

    def is_in_root_devs(self, x, root_devs):
        return any(root_dev.startswith(os.path.join("/dev/", x)) for root_dev in root_devs)

    def _non_root_nvmes(self):
        """get list of nvme disks from os, filter away if one of them is root"""
        nvme_re = re.compile(r"nvme\d+n\d+$")

        root_dev_candidates = [x for x in psutil.disk_partitions() if x.mountpoint == "/"]
        if len(root_dev_candidates) != 1:
            raise Exception("found more than one disk mounted at root ".format())

        root_devs = [x.device for x in root_dev_candidates]

        nvmes_present = list(filter(nvme_re.match, os.listdir("/dev")))
        return {
            self.ROOT: root_devs,
            self.EPHEMERAL: [x for x in nvmes_present if not self.is_in_root_devs(x, root_devs)],
        }

    def _get_swap_dev(self):
        if os.path.exists("/dev/disk/cloud/azure_resource"):
            return os.path.realpath("/dev/disk/cloud/azure_resource")
        return None

    def _non_root_disks(self):
        """get list of disks from os, filter away if one of them is root"""
        disk_re = re.compile(r"/dev/sd[b-z]+$")

        root_dev_candidates = [x for x in psutil.disk_partitions() if x.mountpoint == "/"]

        root_devs = [x.device for x in root_dev_candidates]

        disks_present = list(filter(disk_re.match, glob.glob("/dev/sd*")))
        swap_dev = self._get_swap_dev()
        swap = []
        if swap_dev:
            swap.append(swap_dev.removeprefix("/dev/"))
        persistent = [
            x.removeprefix("/dev/")
            for x in disks_present
            if not self.is_in_root_devs(x.removeprefix("/dev/"), root_devs) and x != swap_dev
        ]
        return {self.PERSISTENT: persistent, self.SWAP: swap}

    @functools.cached_property
    def os_disks(self):
        """populate disks from /dev/ and root mountpoint"""
        __os_disks = {}
        nvmes_present = self._non_root_nvmes()
        for k, v in nvmes_present.items():
            __os_disks[k] = v
        disks_present = self._non_root_disks()
        for k, v in disks_present.items():
            __os_disks[k] = v
        return __os_disks

    def get_local_disks(self):
        """return just transient disks"""
        return self.os_disks[self.EPHEMERAL]

    def get_remote_disks(self):
        """return just persistent disks"""
        return self.os_disks[self.PERSISTENT]

    def get_swap_disks(self):
        return self.os_disks[self.SWAP]

    @functools.cached_property
    def nvme_disk_count(self) -> int:
        """get # of nvme disks available for scylla raid"""
        try:
            ephemeral_disks = self.get_local_disks()
            count_os_disks = len(ephemeral_disks)
        except Exception as e:
            print("Problem when parsing disks from OS:")
            print(e)
            count_os_disks = 0
        return count_os_disks

    supported_classes = [
        # storage optimized
        r"L\d+s_v2",  # https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/storage-optimized/lsv2-series
        r"L\d+s_v3",  # https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/storage-optimized/lsv3-series
        r"L\d+as_v3",  # https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/storage-optimized/lasv3-series
        r"L\d+s_v4",  # https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/storage-optimized/lsv4-series
        r"L\d+as_v4",  # https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/storage-optimized/lasv4-series
        r"L\d+aos_v4",  # https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/storage-optimized/laosv4-series
        # general purpose
        r"D\d+pds_v5",  # https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/dpdsv5-series
        r"D\d+pds_v6",  # https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/dpdsv6-series
    ]

    @functools.cached_property
    def instancelocation(self):
        """return the location of this instance, e.g. eastus"""
        return self.__instance_metadata("/compute/location")

    @functools.cached_property
    def instancezone(self):
        """return the zone of this instance, e.g. 1"""
        return self.__instance_metadata("/compute/zone")

    @functools.cached_property
    def instancetype(self):
        """return the type of this instance, e.g. Standard_L8s_v2"""
        return self.__instance_metadata("/compute/vmSize")

    @functools.cached_property
    def cpu(self):
        """return the # of cpus of this instance"""
        return psutil.cpu_count()

    @functools.cached_property
    def memory_gb(self):
        """return the size of memory in GB of this instance"""
        return psutil.virtual_memory().total / 1024 / 1024 / 1024

    def instance_purpose(self):
        """Returns the class of the instance we are running in. i.e.: Standard"""
        return self.instancetype.split("_", 1)[0]

    def instance_class(self):
        """Returns the purpose of the instance we are running in. i.e.: L8s"""
        return self.instancetype.split("_", 1)[1]

    def is_supported_instance_class(self):
        """Returns if this instance type belongs to supported ones for nvmes"""
        instance_class = self.instance_class()
        return any(re.match(pattern, instance_class) for pattern in self.supported_classes)

    def is_recommended_instance_size(self):
        """if this instance has at least 2 cpus, it has a recommended size"""
        return self.cpu and self.cpu > 1

    def is_recommended_instance(self):
        return bool(self.is_supported_instance_class())

    def is_dev_instance_type(self):
        return False

    def private_ipv4(self):
        return self.__instance_metadata("/network/interface/0/ipv4/ipAddress/0/privateIpAddress")

    @staticmethod
    def check():
        pass

    def io_setup(self):
        run("/opt/scylladb/scylla-machine-image/scylla_cloud_io_setup", check=True, shell=True)

    @property
    def user_data(self):
        encoded_user_data = self.__instance_metadata("/compute/userData")
        if not encoded_user_data:
            return ""
        return base64.b64decode(encoded_user_data).decode()


class OciInstance(CloudInstance):
    """Describe several aspects of the current OCI instance"""

    EPHEMERAL = "ephemeral"
    PERSISTENT = "persistent"
    ROOT = "root"
    META_DATA_BASE_URL = "http://169.254.169.254/opc/v2/instance/"
    META_DATA_VNICS_BASE_URL = "http://169.254.169.254/opc/v2/vnics/"
    ENDPOINT_SNITCH = "GossipingPropertyFileSnitch"

    def __init__(self):
        self.__type = None
        self.__cpu = None
        self.__memoryGB = None
        self.__nvmeDiskCount = None
        self.__firstNvmeSize = None
        self.__osDisks = None
        self.__instance_data = None

    def __get_instance_data(self):
        """Get and cache instance metadata."""
        if self.__instance_data is None:
            try:
                self.__instance_data = json.loads(self.__instance_metadata(""))
            except Exception:
                self.__instance_data = {}
        return self.__instance_data

    @property
    def ocpus(self) -> int:
        """Get the number of OCPUs for the instance."""
        return int(self.__get_instance_data().get("shapeConfig", {}).get("ocpus", 0))

    @property
    def region(self):
        """Get the region of the instance."""
        return self.__get_instance_data().get("canonicalRegionName")

    @property
    def availability_zone(self):
        """Get the availability domain of the instance."""
        return self.__get_instance_data().get("ociAdName")

    @property
    def endpoint_snitch(self):
        return self.ENDPOINT_SNITCH

    @classmethod
    def identify_dmi(cls):
        """Check if this is an OCI instance via DMI info."""
        try:
            product_name = read_one_line("/sys/class/dmi/id/chassis_asset_tag")
            if product_name == "OracleCloud.com":
                return cls
        except Exception as exc:
            logging.debug("Failed to read DMI chassis_asset_tag for OCI detection: %s", exc)
        return None

    @classmethod
    async def identify_metadata(cls):
        """Check if it's an OCI instance via metadata server."""
        try:
            # OCI metadata server requires specific headers
            await aiocurl(cls.META_DATA_BASE_URL, headers={"Authorization": "Bearer Oracle"})
            return cls
        except Exception:
            return None

    def __instance_metadata(self, path):
        """Get instance metadata from OCI metadata service."""
        url = self.META_DATA_BASE_URL.rstrip("/") + "/" + path.lstrip("/")
        return curl(url, headers={"Authorization": "Bearer Oracle"})

    def __vnics_metadata(self, path):
        """Get vnic metadata from OCI metadata service."""
        url = self.META_DATA_VNICS_BASE_URL.rstrip("/") + "/" + path.lstrip("/")
        return curl(url, headers={"Authorization": "Bearer Oracle"})

    def is_in_root_devs(self, x, root_devs):
        return any(root_dev.startswith(os.path.join("/dev/", x)) for root_dev in root_devs)

    def _non_root_nvmes(self):
        """get list of nvme disks from os, filter away if one of them is root"""
        nvme_re = re.compile(r"nvme\d+n\d+$")

        root_dev_candidates = [x for x in psutil.disk_partitions() if x.mountpoint == "/"]

        root_devs = [x.device for x in root_dev_candidates]

        nvmes_present = list(filter(nvme_re.match, os.listdir("/dev")))
        return {
            self.ROOT: root_devs,
            self.EPHEMERAL: [x for x in nvmes_present if not self.is_in_root_devs(x, root_devs)],
        }

    def _non_root_disks(self):
        """get list of disks from os, filter away if one of them is root"""
        disk_re = re.compile(r"/dev/sd[b-z]+$")

        root_dev_candidates = [x for x in psutil.disk_partitions() if x.mountpoint == "/"]

        root_devs = [x.device for x in root_dev_candidates]

        disks_present = list(filter(disk_re.match, glob.glob("/dev/sd*")))
        return {
            self.PERSISTENT: [
                x.removeprefix("/dev/")
                for x in disks_present
                if not self.is_in_root_devs(x.removeprefix("/dev/"), root_devs)
            ]
        }

    def get_en_interface_type(self):
        """Get ethernet interface type for OCI."""
        # OCI typically uses virtio-based network interfaces
        return "virtio"

    @property
    def nvme_disk_count(self):
        """Get the count of NVMe disks."""
        return len(self._non_root_nvmes()[self.EPHEMERAL])

    def get_local_disks(self):
        """Returns all ephemeral/local NVMe disks."""
        non_root_nvmes = self._non_root_nvmes()[self.EPHEMERAL]
        return [f"/dev/{x}" for x in non_root_nvmes]

    def get_remote_disks(self):
        """Returns all persistent/remote disks."""
        return self._non_root_disks()[self.PERSISTENT]

    def get_disk_config(self):
        """Get disk configuration for OCI instance."""
        non_root_nvmes = self._non_root_nvmes()[self.EPHEMERAL]
        non_root_disks = self._non_root_disks()[self.PERSISTENT]

        return {"nvme": len(non_root_nvmes) > 0, "disks": [f"/dev/{x}" for x in non_root_nvmes] + non_root_disks}

    @property
    def instancetype(self):
        """Returns which instance shape we are running in. i.e.: VM.Standard3.Flex"""
        try:
            shape_data = json.loads(self.__instance_metadata(""))
            return shape_data.get("shape", "unknown")
        except Exception:
            return "unknown"

    def instance_size(self):
        """Get the instance size/shape."""
        return self.instancetype

    def instance_class(self):
        """Returns the class of the instance (e.g., VM.Standard)."""
        size = self.instance_size()
        # Extract the base shape class (e.g., VM.Standard from VM.Standard3.Flex)
        # OCI shapes format: VM.Family#.Size or BM.Family#.Size
        parts = size.split(".")
        if len(parts) >= 2:
            # Remove version numbers from family name (e.g., Standard3 -> Standard)
            family = parts[1]
            # Remove trailing digits from family name

            family_base = re.sub(r"\d+$", "", family)
            return f"{parts[0]}.{family_base}"
        return size

    def is_supported_instance_class(self):
        """Returns if this instance type belongs to supported ones for NVMe."""
        # OCI VM.Standard, VM.DenseIO, and BM.DenseIO shapes support NVMe
        instance_class = self.instance_class()
        # Check if the base family matches supported types
        if instance_class in ["VM.Standard", "VM.DenseIO", "BM.DenseIO", "VM.Optimized"]:
            return True
        # Also check the full shape name for patterns
        shape = self.instance_size()
        return bool("DenseIO" in shape or "Standard" in shape or "Optimized" in shape)

    def is_dev_instance_type(self):
        """Returns if this is a development/small instance type."""
        # Consider smaller shapes as dev instances
        shape = self.instancetype
        return bool("Micro" in shape or shape.startswith("VM.Standard.E2.1"))

    @staticmethod
    def check():
        """Perform instance check."""
        return run("/opt/scylladb/scylla-machine-image/scylla_ec2_check --nic eth0", shell=True)

    def io_setup(self):
        """Setup I/O configuration."""
        run("/opt/scylladb/scylla-machine-image/scylla_cloud_io_setup", check=True, shell=True)

    @property
    def user_data(self):
        """Get user data for the instance."""
        try:
            encoded_user_data = self.__instance_metadata("metadata/user_data")
            return base64.b64decode(encoded_user_data).decode()
        except Exception:
            return ""

    def public_ipv4(self):
        """Return the public IPv4 address for this instance.

        On Oracle Cloud Infrastructure (OCI) this method currently always returns
        None because the public IP address is not exposed in the instance
        metadata service by default. Exposing the public IP in metadata requires
        additional configuration, as described in:
        https://www.ateam-oracle.com/how-to-include-the-public-ip-address-information-in-the-oci-vm-metadata
        """

        # OCI does not provide public IP in instance metadata by default.
        # See the docstring and:
        # https://www.ateam-oracle.com/how-to-include-the-public-ip-address-information-in-the-oci-vm-metadata

        return

    def private_ipv4(self):
        """Get the private IPv4 address."""
        try:
            return self.__vnics_metadata("0/privateIp")
        except Exception:
            return None


class AwsInstance(CloudInstance):
    """Describe several aspects of the current AWS instance"""

    META_DATA_BASE_URL = "http://169.254.169.254/latest/"
    ENDPOINT_SNITCH = "Ec2Snitch"
    METADATA_TOKEN_TTL = 21600

    def __disk_name(self, dev):
        name = re.compile(r"(?:/dev/)?(?P<devname>[a-zA-Z]+)\d*")
        match = name.search(dev)
        return match.group("devname") if match else ""

    def __refresh_metadata_token(self):
        self._metadata_token_time = datetime.datetime.now(tz=timezone.utc)
        self._metadata_token = curl(
            self.META_DATA_BASE_URL + "api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": self.METADATA_TOKEN_TTL},
            method="PUT",
        )

    def __instance_metadata(self, path):
        if not self._metadata_token:
            self.__refresh_metadata_token()
        else:
            time_diff = datetime.datetime.now(tz=timezone.utc) - self._metadata_token_time
            time_diff_sec = int(time_diff.total_seconds())
            if time_diff_sec >= self.METADATA_TOKEN_TTL - 120:
                self.__refresh_metadata_token()
        return curl(self.META_DATA_BASE_URL + path, headers={"X-aws-ec2-metadata-token": self._metadata_token})

    def __device_exists(self, dev):
        if dev[0:4] != "/dev":
            dev = f"/dev/{dev}"
        return os.path.exists(dev)

    def __xenify(self, devname):
        dev = self.__instance_metadata("meta-data/block-device-mapping/" + devname)
        return dev.replace("sd", "xvd")

    def __filter_nvmes(self, dev, dev_type):
        nvme_re = re.compile(r"(nvme\d+)n\d+$")
        match = nvme_re.match(dev)
        if not match:
            return False
        nvme_name = match.group(1)
        with open(f"/sys/class/nvme/{nvme_name}/model") as f:
            model = f.read().strip()
        if dev_type == "ephemeral":
            return model != "Amazon Elastic Block Store"
        return model == "Amazon Elastic Block Store"

    def _non_root_nvmes(self):
        re.compile(r"nvme\d+n\d+$")

        root_dev_candidates = [x for x in psutil.disk_partitions() if x.mountpoint == "/"]
        if len(root_dev_candidates) != 1:
            raise Exception("found more than one disk mounted at root'".format())

        root_dev = root_dev_candidates[0].device
        if root_dev == "/dev/root":
            root_dev = out("findmnt -n -o SOURCE /")
        ephemeral_present = list(filter(lambda x: self.__filter_nvmes(x, "ephemeral"), os.listdir("/dev")))
        ebs_present = list(filter(lambda x: self.__filter_nvmes(x, "ebs"), os.listdir("/dev")))
        return {
            "root": [root_dev],
            "ephemeral": ephemeral_present,
            "ebs": [x for x in ebs_present if not root_dev.startswith(os.path.join("/dev/", x))],
        }

    def __populate_disks(self):
        devmap = self.__instance_metadata("meta-data/block-device-mapping")
        self._disks = {}
        devname = re.compile(r"^\D+")
        nvmes_present = self._non_root_nvmes()
        for k, v in nvmes_present.items():
            self._disks[k] = v

        for dev in devmap.splitlines():
            if match := devname.match(dev):
                t = match.group()
            else:
                continue
            if t == "ephemeral" and nvmes_present:
                continue
            if t not in self._disks:
                self._disks[t] = []
            if not self.__device_exists(self.__xenify(dev)):
                continue
            self._disks[t] += [self.__xenify(dev)]
        if "ebs" not in self._disks:
            self._disks["ebs"] = []

    def __mac_address(self, nic="eth0"):
        with open(f"/sys/class/net/{nic}/address") as f:
            return f.read().strip()

    def __init__(self):
        self._metadata_token = None
        self._metadata_token_time = datetime.datetime.now(tz=timezone.utc)
        self.__populate_disks()

    @property
    def endpoint_snitch(self):
        return self.ENDPOINT_SNITCH

    @classmethod
    def identify_dmi(cls):
        product_version = read_one_line("/sys/class/dmi/id/product_version")
        bios_vendor = read_one_line("/sys/class/dmi/id/bios_vendor")
        # On Xen instance, product_version is like "4.11.amazon"
        if product_version.endswith(".amazon"):
            return cls
        # On Nitro instance / Baremetal instance, bios_vendor is "Amazon EC2"
        if bios_vendor == "Amazon EC2":
            return cls
        return None

    @classmethod
    async def identify_metadata(cls):
        """Check if it's AWS instance via query to metadata server."""
        try:
            res = await aiocurl(
                cls.META_DATA_BASE_URL + "api/token",
                headers={"X-aws-ec2-metadata-token-ttl-seconds": cls.METADATA_TOKEN_TTL},
                method="PUT",
            )
            print(f"aws_instance: {res}")
            return cls
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError):
            return None

    @functools.cached_property
    def instancetype(self):
        """Returns which instance we are running in. i.e.: i3.16xlarge"""
        return self.__instance_metadata("meta-data/instance-type")

    def instance_size(self):
        """Returns the size of the instance we are running in. i.e.: 16xlarge"""
        return self.instancetype.split(".")[1]

    def instance_class(self):
        """Returns the class of the instance we are running in. i.e.: i3"""
        return self.instancetype.split(".")[0]

    def is_supported_instance_class(self):
        return self.instance_class() in [
            "i2",
            "i3",
            "i3en",
            "c5d",
            "m5d",
            "m5ad",
            "r5d",
            "z1d",
            "c6gd",
            "m6gd",
            "r6gd",
            "x2gd",
            "im4gn",
            "is4gen",
            "i4i",
            "i4g",
            "i7i",
            "i7ie",
            "i8g",
            "i8ge",
        ]

    def is_dev_instance_type(self):
        return self.instancetype in ["t3.micro"]

    def get_en_interface_type(self):
        instance_class = self.instance_class()
        instance_size = self.instance_size()
        if instance_class in ["c3", "c4", "d2", "i2", "r3"]:
            return "ixgbevf"
        if instance_class in [
            "a1",
            "c5",
            "c5a",
            "c5d",
            "c5n",
            "c6g",
            "c6gd",
            "f1",
            "g3",
            "g4",
            "h1",
            "i3",
            "i3en",
            "inf1",
            "m5",
            "m5a",
            "m5ad",
            "m5d",
            "m5dn",
            "m5n",
            "m6g",
            "m6gd",
            "p2",
            "p3",
            "r4",
            "r5",
            "r5a",
            "r5ad",
            "r5b",
            "r5d",
            "r5dn",
            "r5n",
            "t3",
            "t3a",
            "u-6tb1",
            "u-9tb1",
            "u-12tb1",
            "u-18tn1",
            "u-24tb1",
            "x1",
            "x1e",
            "z1d",
            "c6g",
            "c6gd",
            "m6g",
            "m6gd",
            "t4g",
            "r6g",
            "r6gd",
            "x2gd",
            "im4gn",
            "is4gen",
            "i4i",
            "i4g",
            "i7i",
            "i7ie",
            "i8g",
            "i8ge",
        ]:
            return "ena"
        if instance_class == "m4":
            if instance_size == "16xlarge":
                return "ena"
            return "ixgbevf"
        return None

    def disks(self):
        """Returns all disks in the system, as visible from the AWS registry"""
        disks = set()
        for v in list(self._disks.values()):
            disks = disks.union([self.__disk_name(x) for x in v])
        return disks

    def root_device(self):
        """Returns the device being used for root data. Unlike root_disk(),
        which will return a device name (i.e. xvda), this function will return
        the full path to the root partition as returned by the AWS instance
        metadata registry"""
        return set(self._disks["root"])

    def root_disk(self):
        """Returns the disk used for the root partition"""
        return self.__disk_name(self._disks["root"][0])

    def non_root_disks(self):
        """Returns all attached disks but root. Include ephemeral and EBS devices"""
        return set(self._disks["ephemeral"] + self._disks["ebs"])

    @functools.cached_property
    def nvme_disk_count(self) -> int:
        return len(self.non_root_disks())

    def get_local_disks(self):
        """Returns all ephemeral disks. Include standard SSDs and NVMe"""
        return self._disks["ephemeral"]

    def get_remote_disks(self):
        """Returns all EBS disks"""
        return self._disks["ebs"]

    def public_ipv4(self):
        """Returns the public IPv4 address of this instance"""
        return self.__instance_metadata("meta-data/public-ipv4")

    def private_ipv4(self):
        """Returns the private IPv4 address of this instance"""
        return self.__instance_metadata("meta-data/local-ipv4")

    def is_vpc_enabled(self, nic="eth0"):
        mac = self.__mac_address(nic)
        mac_stat = self.__instance_metadata(f"meta-data/network/interfaces/macs/{mac}")
        return bool(re.search(r"^vpc-id$", mac_stat, flags=re.MULTILINE))

    @staticmethod
    def check():
        return run("/opt/scylladb/scylla-machine-image/scylla_ec2_check --nic eth0", shell=True)

    def io_setup(self):
        run("/opt/scylladb/scylla-machine-image/scylla_cloud_io_setup", check=True, shell=True)

    @property
    def user_data(self):
        base_contents = self.__instance_metadata("").splitlines()
        if "user-data" in base_contents:
            return self.__instance_metadata("user-data")
        return ""

    def public_keys(self):
        keylist = self.__instance_metadata("meta-data/public-keys/")
        public_keys = []
        for line in keylist.splitlines():
            index, name = line.split("=")
            key = self.__instance_metadata(f"meta-data/public-keys/{index}/openssh-key")
            public_keys.append(key)
        return public_keys


async def identify_cloud_async():
    tasks = [
        asyncio.create_task(AwsInstance.identify()),
        asyncio.create_task(GcpInstance.identify()),
        asyncio.create_task(AzureInstance.identify()),
        asyncio.create_task(OciInstance.identify()),
    ]
    result = None
    for task in asyncio.as_completed(tasks):
        result = await task
        if result:
            for other_task in tasks:
                other_task.cancel()
            break
    return result


@functools.cache
def identify_cloud():
    datetime.datetime.now(tz=timezone.utc)
    result = asyncio.run(identify_cloud_async())
    datetime.datetime.now(tz=timezone.utc)
    return result


def is_ec2():
    return identify_cloud() == AwsInstance


def is_gce():
    return identify_cloud() == GcpInstance


def is_azure():
    return identify_cloud() == AzureInstance


def is_oci():
    return identify_cloud() == OciInstance


def get_cloud_instance() -> CloudInstance | None:
    if cls := identify_cloud():
        return cls()
    return None


CONCOLORS = {"green": "\033[1;32m", "red": "\033[1;31m", "yellow": "\033[1;33m", "nocolor": "\033[0m"}


def colorprint(msg, **kwargs):
    fmt = dict(CONCOLORS)
    fmt.update(kwargs)
    print(msg.format(**fmt))
