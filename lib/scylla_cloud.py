#!/usr/bin/env python3
#
# Copyright 2022 ScyllaDB
#
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import psutil
import socket
import glob
import distro
import base64
import datetime
from subprocess import run, CalledProcessError, CompletedProcess
from abc import ABCMeta, abstractmethod
from typing import cast, Type, Final, IO, TypeGuard
from types import TracebackType


def out(cmd: str, shell: bool=True, timeout: int | None=None, encoding: str='utf-8', ignore_error: bool=False, user: str | None=None, group: str | None=None) -> str:
    try:
        res: CompletedProcess = run(cmd, capture_output=True, shell=shell, timeout=timeout, check=not ignore_error, encoding=encoding, user=user, group=group)
    except CalledProcessError as e:
        print(f'''
Command '{cmd}' returned non-zero exit status: {e.returncode}
----------  stdout  ----------
{e.stdout.strip()}
------------------------------
----------  stderr  ----------
{e.stderr.strip()}
------------------------------
'''[1:-1])
        raise
    return res.stdout.strip()


import sys
import traceback
import traceback_with_variables
import logging


def scylla_excepthook(etype: Type[BaseException], value: BaseException, tb: TracebackType | None) -> None:
    os.makedirs('/var/tmp/scylla', mode=0o755, exist_ok=True)
    traceback.print_exception(etype, value, tb)
    exc_logger: logging.Logger = logging.getLogger(__name__)
    exc_logger.setLevel(logging.DEBUG)
    exc_logger_file: str = f'/var/tmp/scylla/{os.path.basename(sys.argv[0])}-{os.getpid()}-debug.log'
    exc_logger.addHandler(logging.FileHandler(exc_logger_file))
    traceback_with_variables.print_exc(e=cast(Exception, value), file_=traceback_with_variables.LoggerAsFile(exc_logger))
    print(f'Debug log created: {exc_logger_file}')

sys.excepthook = scylla_excepthook


# @param headers dict of k:v
def curl(url: str, headers: dict[str, str] | None=None, method: str | None=None, byte: bool=False, timeout: int=3, max_retries: int=5, retry_interval: int=5) -> str:
    retries: int = 0
    while True:
        try:
            req: urllib.request.Request = urllib.request.Request(url, headers=headers or {}, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as res:
                if byte:
                    return res.read()
                else:
                    return res.read().decode('utf-8')
        except (urllib.error.URLError, socket.timeout):
            time.sleep(retry_interval)
            retries += 1
            if retries >= max_retries:
                raise


class cloud_instance(metaclass=ABCMeta):
    @abstractmethod
    def get_local_disks(self) -> list[str]:
        pass

    @abstractmethod
    def get_remote_disks(self) -> list[str]:
        pass

    @abstractmethod
    def private_ipv4(self) -> str:
        pass

    @abstractmethod
    def is_supported_instance_class(self) -> bool:
        pass

    @property
    @abstractmethod
    def instancetype(self) -> str:
        pass

    @abstractmethod
    def io_setup(self) -> None:
        pass

    @staticmethod
    @abstractmethod
    def check() -> None:
        pass

    @property
    @abstractmethod
    def user_data(self) -> str:
        pass

    @property
    @abstractmethod
    def nvme_disk_count(self) -> int:
        pass

    @property
    @abstractmethod
    def endpoint_snitch(self) -> str:
        pass

    @property
    @abstractmethod
    def getting_started_url(self) -> str:
        pass


class gcp_instance(cloud_instance):
    """Describe several aspects of the current GCP instance"""

    EPHEMERAL: Final[str] = "ephemeral"
    PERSISTENT: Final[str] = "persistent"
    ROOT: Final[str] = "root"
    GETTING_STARTED_URL: Final[str] = "http://www.scylladb.com/doc/getting-started-google/"
    META_DATA_BASE_URL: Final[str] = "http://metadata.google.internal/computeMetadata/v1/instance/"
    ENDPOINT_SNITCH: Final[str] = "GoogleCloudSnitch"

    def __init__(self) -> None:
        self.__type: str | None = None
        self.__cpu: int | None = None
        self.__memoryGB: float | None = None
        self.__nvmeDiskCount: int | None = None
        self.__firstNvmeSize: float | None = None
        self.__osDisks: dict[str, list[str]] = {}

    @property
    def endpoint_snitch(self) -> str:
        return self.ENDPOINT_SNITCH

    @property
    def getting_started_url(self) -> str:
        return self.GETTING_STARTED_URL

    @staticmethod
    def is_gce_instance() -> bool:
        """Check if it's GCE instance via DNS lookup to metadata server."""
        try:
            addrlist: list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int] | tuple[str, int, int, int]]] = socket.getaddrinfo('metadata.google.internal', 80)
        except socket.gaierror:
            return False
        res: tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int] | tuple[str, int, int, int]]
        for res in addrlist:
            af: socket.AddressFamily
            socktype: socket.SocketKind
            proto: int
            canonname: str
            sa: tuple[str, int] | tuple[str, int, int, int]
            af, socktype, proto, canonname, sa = res
            if af == socket.AF_INET:
                addr: str
                port: int
                addr, port, *_ = sa
                if addr == "169.254.169.254":
                    # Make sure it is not on GKE
                    try:
                        gcp_instance().__instance_metadata("machine-type")
                    except urllib.error.HTTPError:
                        return False
                    return True
        return False

    def __instance_metadata(self, path: str, recursive: bool=False) -> str:
        return curl(self.META_DATA_BASE_URL + path + "?recursive=%s" % str(recursive).lower(),
                    headers={"Metadata-Flavor": "Google"})

    def is_in_root_devs(self, x: str, root_devs: list[str]) -> bool:
        root_dev: str
        for root_dev in root_devs:
            if root_dev.startswith(os.path.join("/dev/", x)):
                return True
        return False

    def _non_root_nvmes(self) -> dict[str, list[str]]:
        """get list of nvme disks from os, filter away if one of them is root"""
        nvme_re: re.Pattern = re.compile(r"nvme\d+n\d+$")

        root_dev_candidates: list[psutil._common.sdiskpart] = [x for x in psutil.disk_partitions() if x.mountpoint == "/"]

        root_devs: list[str] = [x.device for x in root_dev_candidates]

        nvmes_present: list[str] = list(filter(nvme_re.match, os.listdir("/dev")))
        return {self.ROOT: root_devs, self.EPHEMERAL: [x for x in nvmes_present if not self.is_in_root_devs(x, root_devs)]}

    def _non_root_disks(self) -> dict[str, list[str]]:
        """get list of disks from os, filter away if one of them is root"""
        disk_re: re.Pattern = re.compile(r"/dev/sd[b-z]+$")

        root_dev_candidates: list[psutil._common.sdiskpart] = [x for x in psutil.disk_partitions() if x.mountpoint == "/"]

        root_devs: list[str] = [x.device for x in root_dev_candidates]

        disks_present: list[str] = list(filter(disk_re.match, glob.glob("/dev/sd*")))
        return {self.PERSISTENT: [x.lstrip('/dev/') for x in disks_present if not self.is_in_root_devs(x.lstrip('/dev/'), root_devs)]}

    @property
    def os_disks(self) -> dict[str, list[str]]:
        """populate disks from /dev/ and root mountpoint"""
        if not any(self.__osDisks):
            __osDisks: dict[str, list[str]] = {}
            nvmes_present: dict[str, list[str]] = self._non_root_nvmes()
            for k, v in nvmes_present.items():
                __osDisks[k] = v
            disks_present: dict[str, list[str]] = self._non_root_disks()
            for k, v in disks_present.items():
                __osDisks[k] = v
            self.__osDisks = __osDisks
        return self.__osDisks

    def get_local_disks(self) -> list[str]:
        """return just transient disks"""
        return self.os_disks[self.EPHEMERAL]

    def get_remote_disks(self) -> list[str]:
        """return just persistent disks"""
        return self.os_disks[self.PERSISTENT]

    @staticmethod
    def isNVME(gcpdiskobj: dict[str, str]) -> TypeGuard[bool]:
        """check if disk from GCP metadata is a NVME disk"""
        if gcpdiskobj["interface"]=="NVME":
            return True
        return False

    def __get_nvme_disks_from_metadata(self):
        """get list of nvme disks from metadata server"""
        try:
            disksREST: str=self.__instance_metadata("disks", True)
            disksobj: dict[str, str]=json.loads(disksREST)
            nvmedisks: list[bool]=list(filter(self.isNVME, disksobj))
        except Exception as e:
            print ("Problem when parsing disks from metadata:")
            print (e)
            nvmedisks=[]
        return nvmedisks

    @property
    def nvme_disk_count(self) -> int:
        """get # of nvme disks available for scylla raid"""
        if self.__nvmeDiskCount is None:
            try:
                ephemeral_disks: list[str] = self.get_local_disks()
                count_os_disks: int=len(ephemeral_disks)
            except Exception as e:
                print ("Problem when parsing disks from OS:")
                print (e)
                count_os_disks=0
            nvme_metadata_disks: list[str] = self.__get_nvme_disks_from_metadata()
            count_metadata_nvme_disks: int=len(nvme_metadata_disks)
            self.__nvmeDiskCount = count_os_disks if count_os_disks<count_metadata_nvme_disks else count_metadata_nvme_disks
        return self.__nvmeDiskCount

    @property
    def instancetype(self) -> str:
        """return the type of this instance, e.g. n2-standard-2"""
        if self.__type is None:
            self.__type = self.__instance_metadata("machine-type").split("/")[-1]
        return self.__type

    @property
    def cpu(self) -> int:
        """return the # of cpus of this instance"""
        if self.__cpu is None:
            self.__cpu = psutil.cpu_count()
        return self.__cpu

    @property
    def memoryGB(self) -> float:
        """return the size of memory in GB of this instance"""
        if self.__memoryGB is None:
            self.__memoryGB = psutil.virtual_memory().total/1024/1024/1024
        return self.__memoryGB

    def instance_size(self) -> str:
        """Returns the size of the instance we are running in. i.e.: 2"""
        instancetypesplit: list[str] = self.instancetype.split("-")
        return instancetypesplit[2] if len(instancetypesplit)>2 else '0'

    def instance_class(self) -> str:
        """Returns the class of the instance we are running in. i.e.: n2"""
        return self.instancetype.split("-")[0]

    def instance_purpose(self) -> str:
        """Returns the purpose of the instance we are running in. i.e.: standard"""
        return self.instancetype.split("-")[1]

    m1supported: Final[str]="m1-megamem-96" #this is the only exception of supported m1 as per https://cloud.google.com/compute/docs/machine-types#m1_machine_types

    def is_unsupported_instance_class(self) -> bool:
        """Returns if this instance type belongs to unsupported ones for nvmes"""
        if self.instancetype == self.m1supported:
            return False
        if self.instance_class() in ['e2', 'f1', 'g1', 'm2', 'm1']:
            return True
        return False

    def is_supported_instance_class(self) -> bool:
        """Returns if this instance type belongs to supported ones for nvmes"""
        if self.instancetype == self.m1supported:
            return True
        if self.instance_class() in ['n1', 'n2', 'n2d' ,'c2']:
            return True
        return False

    def is_recommended_instance_size(self) -> bool:
        """if this instance has at least 2 cpus, it has a recommended size"""
        if int(self.instance_size()) > 1:
            return True
        return False

    @staticmethod
    def get_file_size_by_seek(filename: str) -> int:
        "Get the file size by seeking at end"
        fd: int= os.open(filename, os.O_RDONLY)
        try:
            return os.lseek(fd, 0, os.SEEK_END)
        finally:
            os.close(fd)

    # note that GCP has 3TB physical devices actually, which they break into smaller 375GB disks and share the same mem with multiple machines
    # this is a reference value, disk size shouldn't be lower than that
    GCP_NVME_DISK_SIZE_2020: Final[int]=375

    @property
    def firstNvmeSize(self) -> float:
        """return the size of first non root NVME disk in GB"""
        if self.__firstNvmeSize is None:
            ephemeral_disks: list[str] = self.get_local_disks()
            if len(ephemeral_disks) > 0:
                firstDisk: str = ephemeral_disks[0]
                firstDiskSize: int = self.get_file_size_by_seek(os.path.join("/dev/", firstDisk))
                firstDiskSizeGB: float = firstDiskSize/1024/1024/1024
                if firstDiskSizeGB >= self.GCP_NVME_DISK_SIZE_2020:
                    self.__firstNvmeSize = firstDiskSizeGB
                else:
                    self.__firstNvmeSize = 0
                    logging.warning("First nvme is smaller than lowest expected size: {}".format(firstDisk))
            else:
                self.__firstNvmeSize = 0
        return self.__firstNvmeSize

    def is_recommended_instance(self) -> bool:
        if not self.is_unsupported_instance_class() and self.is_supported_instance_class() and self.is_recommended_instance_size():
            # at least 1:2GB cpu:ram ratio , GCP is at 1:4, so this should be fine
            if self.cpu/self.memoryGB < 0.5:
                diskCount: int = self.nvme_disk_count
                # to reach max performance for > 16 disks we mandate 32 or more vcpus
                # https://cloud.google.com/compute/docs/disks/local-ssd#performance
                if diskCount >= 16 and self.cpu < 32:
                    logging.warning(
                        "This machine doesn't have enough CPUs for allocated number of NVMEs (at least 32 cpus for >=16 disks). Performance will suffer.")
                if diskCount < 1:
                    logging.warning("No ephemeral disks were found.")
                    return False
                diskSize: float = self.firstNvmeSize
                max_disktoramratio: int = 105
                # 30:1 Disk/RAM ratio must be kept at least(AWS), we relax this a little bit
                # on GCP we are OK with {max_disktoramratio}:1 , n1-standard-2 can cope with 1 disk, not more
                disktoramratio: float = (diskCount * diskSize) / self.memoryGB
                if (disktoramratio > max_disktoramratio):
                    logging.warning(
                        f"Instance disk-to-RAM ratio is {disktoramratio}, which is higher than the recommended ratio {max_disktoramratio}. Performance may suffer.")
                    return False
                return True
            else:
                logging.warning("At least 2G of RAM per CPU is needed. Performance will suffer.")
        return False

    def private_ipv4(self) -> str:
        return self.__instance_metadata("network-interfaces/0/ip")

    @staticmethod
    def check() -> None:
        pass

    def io_setup(self) -> None:
        run('/opt/scylladb/scylla-machine-image/scylla_cloud_io_setup', check=True, shell=True)

    @property
    def user_data(self) -> str:
        try:
            return self.__instance_metadata("attributes/user-data")
        except urllib.error.HTTPError:  # empty user-data
            return ""


class azure_instance(cloud_instance):
    """Describe several aspects of the current Azure instance"""

    EPHEMERAL: Final[str] = "ephemeral"
    PERSISTENT: Final[str] = "persistent"
    SWAP: Final[str] = "swap"
    ROOT: Final[str] = "root"
    GETTING_STARTED_URL: Final[str] = "http://www.scylladb.com/doc/getting-started-azure/"
    ENDPOINT_SNITCH: Final[str] = "AzureSnitch"
    META_DATA_BASE_URL: Final[str] = "http://169.254.169.254/metadata/instance"

    def __init__(self):
        self.__type: str | None = None
        self.__cpu: int | None = None
        self.__location: str | None = None
        self.__zone: str | None = None
        self.__memoryGB: float | None = None
        self.__nvmeDiskCount: int | None = None
        self.__firstNvmeSize: float | None = None
        self.__osDisks: dict[str, list[str]] = {}

    @property
    def endpoint_snitch(self) -> str:
        return self.ENDPOINT_SNITCH

    @property
    def getting_started_url(self) -> str:
        return self.GETTING_STARTED_URL

    @classmethod
    def is_azure_instance(cls) -> bool:
        """Check if it's Azure instance via query to metadata server."""
        try:
            curl(cls.META_DATA_BASE_URL + cls.API_VERSION + "&format=text", headers = { "Metadata": "True" }, max_retries=2, retry_interval=1)
            return True
        except (urllib.error.URLError, urllib.error.HTTPError):
            return False

# as per https://docs.microsoft.com/en-us/azure/virtual-machines/windows/instance-metadata-service?tabs=windows#supported-api-versions
    API_VERSION: Final[str] = "?api-version=2021-01-01"

    def __instance_metadata(self, path: str) -> str:
        """query Azure metadata server"""
        return curl(self.META_DATA_BASE_URL + path + self.API_VERSION + "&format=text", headers = { "Metadata": "True" })

    def is_in_root_devs(self, x: str, root_devs: list[str]) -> bool:
        root_dev: str
        for root_dev in root_devs:
            if root_dev.startswith(os.path.join("/dev/", x)):
                return True
        return False

    def _non_root_nvmes(self) -> dict[str, list[str]]:
        """get list of nvme disks from os, filter away if one of them is root"""
        nvme_re: re.Pattern = re.compile(r"nvme\d+n\d+$")

        root_dev_candidates: list[psutil._common.sdiskpart] = [x for x in psutil.disk_partitions() if x.mountpoint == "/"]
        if len(root_dev_candidates) != 1:
            raise Exception("found more than one disk mounted at root: {}".format(root_dev_candidates))

        root_devs: list[str] = [x.device for x in root_dev_candidates]

        nvmes_present: list[str] = list(filter(nvme_re.match, os.listdir("/dev")))
        return {self.ROOT: root_devs, self.EPHEMERAL: [x for x in nvmes_present if not self.is_in_root_devs(x, root_devs)]}

    def _get_swap_dev(self) -> str | None:
        if os.path.exists('/dev/disk/cloud/azure_resource'):
            return os.path.realpath('/dev/disk/cloud/azure_resource')
        else:
            return None

    def _non_root_disks(self) -> dict[str, list[str]]:
        """get list of disks from os, filter away if one of them is root"""
        disk_re: re.Pattern = re.compile(r"/dev/sd[b-z]+$")

        root_dev_candidates: list[psutil._common.sdiskpart] = [x for x in psutil.disk_partitions() if x.mountpoint == "/"]

        root_devs: list[str] = [x.device for x in root_dev_candidates]

        disks_present = list(filter(disk_re.match, glob.glob("/dev/sd*")))
        swap_dev: str | None = self._get_swap_dev()
        swap: list[str] = []
        if swap_dev:
            swap.append(swap_dev.lstrip('/dev/'))
        persistent: list[str] = [x.lstrip('/dev/') for x in disks_present if not self.is_in_root_devs(x.lstrip('/dev/'), root_devs) and not x == swap_dev]
        return {self.PERSISTENT: persistent, self.SWAP: swap}

    @property
    def os_disks(self) -> dict[str, list[str]]:
        """populate disks from /dev/ and root mountpoint"""
        if not any(self.__osDisks):
            __osDisks: dict[str, list[str]] = {}
            nvmes_present: dict[str, list[str]] = self._non_root_nvmes()
            for k, v in nvmes_present.items():
                __osDisks[k] = v
            disks_present: dict[str, list[str]] = self._non_root_disks()
            for k, v in disks_present.items():
                __osDisks[k] = v
            self.__osDisks = __osDisks
        return self.__osDisks

    def get_local_disks(self) -> list[str]:
        """return just transient disks"""
        return self.os_disks[self.EPHEMERAL]

    def get_remote_disks(self) -> list[str]:
        """return just persistent disks"""
        return self.os_disks[self.PERSISTENT]

    def get_swap_disks(self) -> list[str]:
        return self.os_disks[self.SWAP]

    @property
    def nvme_disk_count(self) -> int:
        """get # of nvme disks available for scylla raid"""
        if self.__nvmeDiskCount is None:
            try:
                ephemeral_disks: list[str] = self.get_local_disks()
                count_os_disks: int = len(ephemeral_disks)
            except Exception as e:
                print("Problem when parsing disks from OS:")
                print(e)
                count_os_disks = 0
            count_metadata_nvme_disks: int = self.__get_nvme_disks_count_from_metadata()
            self.__nvmeDiskCount = count_os_disks if count_os_disks < count_metadata_nvme_disks else count_metadata_nvme_disks
        return self.__nvmeDiskCount

    instanceToDiskCount: Final[dict[str, int]] = {
        "L8s": 1,
        "L16s": 2,
        "L32s": 4,
        "L48s": 6,
        "L64s": 8,
        "L80s": 10,
        "L8as": 1,
        "L16as": 2,
        "L32as": 4,
        "L48as": 6,
        "L64as": 8,
        "L80as": 10
    }

    def __get_nvme_disks_count_from_metadata(self) -> int:
        #storageProfile in VM metadata lacks the number of NVMEs, it's hardcoded based on VM type
        return self.instanceToDiskCount.get(self.instance_class(), 0)

    @property
    def instancelocation(self) -> str:
        """return the location of this instance, e.g. eastus"""
        if self.__location is None:
            self.__location = self.__instance_metadata("/compute/location")
        return self.__location

    @property
    def instancezone(self) -> str:
        """return the zone of this instance, e.g. 1"""
        if self.__zone is None:
            self.__zone = self.__instance_metadata("/compute/zone")
        return self.__zone

    @property
    def instancetype(self) -> str:
        """return the type of this instance, e.g. Standard_L8s_v2"""
        if self.__type is None:
            self.__type = self.__instance_metadata("/compute/vmSize")
        return self.__type

    @property
    def cpu(self) -> int:
        """return the # of cpus of this instance"""
        if self.__cpu is None:
            self.__cpu = psutil.cpu_count()
        return self.__cpu

    @property
    def memoryGB(self) -> float:
        """return the size of memory in GB of this instance"""
        if self.__memoryGB is None:
            self.__memoryGB = psutil.virtual_memory().total/1024/1024/1024
        return self.__memoryGB

    def instance_purpose(self) -> str:
        """Returns the class of the instance we are running in. i.e.: Standard"""
        return self.instancetype.split("_")[0]

    def instance_class(self) -> str:
        """Returns the purpose of the instance we are running in. i.e.: L8s"""
        return self.instancetype.split("_")[1]

    def is_supported_instance_class(self) -> bool:
        """Returns if this instance type belongs to supported ones for nvmes"""
        if self.instance_class() in list(self.instanceToDiskCount.keys()):
            return True
        return False

    def is_recommended_instance_size(self) -> bool:
        """if this instance has at least 2 cpus, it has a recommended size"""
        if self.cpu > 1:
            return True
        return False

    def is_recommended_instance(self) -> bool:
        if self.is_supported_instance_class():
            return True
        return False

    def private_ipv4(self) -> str:
        return self.__instance_metadata("/network/interface/0/ipv4/ipAddress/0/privateIpAddress")

    @staticmethod
    def check() -> None:
        pass

    def io_setup(self) -> None:
        run('/opt/scylladb/scylla-machine-image/scylla_cloud_io_setup', check=True, shell=True)

    @property
    def user_data(self) -> str:
        encoded_user_data: str | None = self.__instance_metadata("/compute/userData")
        if not encoded_user_data:
            return ''
        return base64.b64decode(encoded_user_data).decode()


class aws_instance(cloud_instance):
    """Describe several aspects of the current AWS instance"""
    GETTING_STARTED_URL: Final[str] = "http://www.scylladb.com/doc/getting-started-amazon/"
    META_DATA_BASE_URL: Final[str] = "http://169.254.169.254/latest/"
    ENDPOINT_SNITCH: Final[str] = "Ec2Snitch"
    METADATA_TOKEN_TTL: Final[int] = 21600

    def __disk_name(self, dev: str) -> str:
        name: re.Pattern = re.compile(r"(?:/dev/)?(?P<devname>[a-zA-Z]+)\d*")
        match: re.Match[str] | None = name.search(dev)
        assert match is not None
        return match.group("devname")

    def __refresh_metadata_token(self) -> None:
        self._metadata_token_time = datetime.datetime.now()
        self._metadata_token = curl(self.META_DATA_BASE_URL + "api/token", headers={"X-aws-ec2-metadata-token-ttl-seconds": str(self.METADATA_TOKEN_TTL)}, method="PUT")

    def __instance_metadata(self, path: str) -> str:
        if not self._metadata_token:
            self.__refresh_metadata_token()
        else:
            assert self._metadata_token_time is not None
            time_diff: datetime.timedelta = datetime.datetime.now() - self._metadata_token_time
            time_diff_sec: int = int(time_diff.total_seconds())
            if time_diff_sec >= self.METADATA_TOKEN_TTL - 120:
                self.__refresh_metadata_token()
        return curl(self.META_DATA_BASE_URL + "meta-data/" + path, headers={"X-aws-ec2-metadata-token": str(self._metadata_token)})

    def __device_exists(self, dev: str) -> bool:
        if dev[0:4] != "/dev":
            dev = "/dev/%s" % dev
        return os.path.exists(dev)

    def __xenify(self, devname: str) -> str:
        dev = self.__instance_metadata('block-device-mapping/' + devname)
        return dev.replace("sd", "xvd")

    def __filter_nvmes(self, dev: str, dev_type: str) -> bool:
        nvme_re: re.Pattern = re.compile(r"(nvme\d+)n\d+$")
        match: re.Match[str] | None = nvme_re.match(dev)
        if not match:
            return False
        nvme_name: str = match.group(1)
        f: IO
        with open(f'/sys/class/nvme/{nvme_name}/model') as f:
            model: str = f.read().strip()
        if dev_type == 'ephemeral':
            return model != 'Amazon Elastic Block Store'
        else:
            return model == 'Amazon Elastic Block Store'

    def _non_root_nvmes(self) -> dict[str, list[str]]:
        nvme_re: re.Pattern = re.compile(r"nvme\d+n\d+$")

        root_dev_candidates: list[psutil._common.sdiskpart] = [ x for x in psutil.disk_partitions() if x.mountpoint == "/" ]
        if len(root_dev_candidates) != 1:
            raise Exception("found more than one disk mounted at root: {}".format(root_dev_candidates))

        root_dev: str = root_dev_candidates[0].device
        if root_dev == '/dev/root':
            root_dev = out('findmnt -n -o SOURCE /')
        ephemeral_present: list[str] = list(filter(lambda x: self.__filter_nvmes(x, 'ephemeral'), os.listdir("/dev")))
        ebs_present: list[str] = list(filter(lambda x: self.__filter_nvmes(x, 'ebs'), os.listdir("/dev")))
        return {"root": [ root_dev ], "ephemeral": ephemeral_present, "ebs": [ x for x in ebs_present if not root_dev.startswith(os.path.join("/dev/", x))] }

    def __populate_disks(self) -> None:
        devmap: str = self.__instance_metadata("block-device-mapping")
        self._disks: dict[str, list[str]] = {}
        devname: re.Pattern = re.compile("^\D+")
        nvmes_present: dict[str, list[str]] = self._non_root_nvmes()
        k: str
        v: list[str]
        for k,v in nvmes_present.items():
            self._disks[k] = v

        dev: str
        for dev in devmap.splitlines():
            match: re.Match[str] | None = devname.match(dev)
            assert match is not None
            t: str = match.group()
            if t == "ephemeral" and nvmes_present:
                continue
            if t not in self._disks:
                self._disks[t] = []
            if not self.__device_exists(self.__xenify(dev)):
                continue
            self._disks[t] += [self.__xenify(dev)]
        if not 'ebs' in self._disks:
            self._disks['ebs'] = []

    def __mac_address(self, nic: str='eth0') -> str:
        f: IO
        with open('/sys/class/net/{}/address'.format(nic)) as f:
            return f.read().strip()

    def __init__(self):
        self._metadata_token: str | None = None
        self._metadata_token_time: datetime.datetime  | None = None
        self._type: str = self.__instance_metadata("instance-type")
        self.__populate_disks()

    @property
    def endpoint_snitch(self) -> str:
        return self.ENDPOINT_SNITCH

    @property
    def getting_started_url(self) -> str:
        return self.GETTING_STARTED_URL

    @classmethod
    def is_aws_instance(cls) -> bool:
        """Check if it's AWS instance via query to metadata server."""
        try:
            curl(cls.META_DATA_BASE_URL + "api/token", headers={"X-aws-ec2-metadata-token-ttl-seconds": str(cls.METADATA_TOKEN_TTL)}, method="PUT")
            return True
        except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout):
            return False

    @property
    def instancetype(self) -> str:
        """Returns which instance we are running in. i.e.: i3.16xlarge"""
        return self._type

    def instance_size(self) -> str:
        """Returns the size of the instance we are running in. i.e.: 16xlarge"""
        return self._type.split(".")[1]

    def instance_class(self) -> str:
        """Returns the class of the instance we are running in. i.e.: i3"""
        return self._type.split(".")[0]

    def is_supported_instance_class(self):
        if self.instance_class() in ['i2', 'i3', 'i3en', 'c5d', 'm5d', 'm5ad', 'r5d', 'z1d', 'c6gd', 'm6gd', 'r6gd', 'x2gd', 'im4gn', 'is4gen', 'i4i']:
            return True
        return False

    def get_en_interface_type(self) -> str | None:
        instance_class: str = self.instance_class()
        instance_size: str = self.instance_size()
        if instance_class in ['c3', 'c4', 'd2', 'i2', 'r3']:
            return 'ixgbevf'
        if instance_class in ['a1', 'c5', 'c5a', 'c5d', 'c5n', 'c6g', 'c6gd', 'f1', 'g3', 'g4', 'h1', 'i3', 'i3en', 'inf1', 'm5', 'm5a', 'm5ad', 'm5d', 'm5dn', 'm5n', 'm6g', 'm6gd', 'p2', 'p3', 'r4', 'r5', 'r5a', 'r5ad', 'r5b', 'r5d', 'r5dn', 'r5n', 't3', 't3a', 'u-6tb1', 'u-9tb1', 'u-12tb1', 'u-18tn1', 'u-24tb1', 'x1', 'x1e', 'z1d', 'c6g', 'c6gd', 'm6g', 'm6gd', 't4g', 'r6g', 'r6gd', 'x2gd', 'im4gn', 'is4gen', 'i4i']:
            return 'ena'
        if instance_class == 'm4':
            if instance_size == '16xlarge':
                return 'ena'
            else:
                return 'ixgbevf'
        return None

    def disks(self) -> set[str]:
        """Returns all disks in the system, as visible from the AWS registry"""
        disks: set[str] = set()
        for v in list(self._disks.values()):
            disks = disks.union([self.__disk_name(x) for x in v])
        return disks

    def root_device(self) -> set[str]:
        """Returns the device being used for root data. Unlike root_disk(),
           which will return a device name (i.e. xvda), this function will return
           the full path to the root partition as returned by the AWS instance
           metadata registry"""
        return set(self._disks["root"])

    def root_disk(self) -> str:
        """Returns the disk used for the root partition"""
        return self.__disk_name(self._disks["root"][0])

    def non_root_disks(self) -> set[str]:
        """Returns all attached disks but root. Include ephemeral and EBS devices"""
        return set(self._disks["ephemeral"] + self._disks["ebs"])

    @property
    def nvme_disk_count(self) -> int:
        return len(self.non_root_disks())

    def get_local_disks(self) -> list[str]:
        """Returns all ephemeral disks. Include standard SSDs and NVMe"""
        return self._disks["ephemeral"]

    def get_remote_disks(self) -> list[str]:
        """Returns all EBS disks"""
        return self._disks["ebs"]

    def public_ipv4(self) -> str:
        """Returns the public IPv4 address of this instance"""
        return self.__instance_metadata("public-ipv4")

    def private_ipv4(self) -> str:
        """Returns the private IPv4 address of this instance"""
        return self.__instance_metadata("local-ipv4")

    def is_vpc_enabled(self, nic: str='eth0') -> bool:
        mac: str = self.__mac_address(nic)
        mac_stat: str = self.__instance_metadata('network/interfaces/macs/{}'.format(mac))
        return True if re.search(r'^vpc-id$', mac_stat, flags=re.MULTILINE) else False

    @staticmethod
    def check() -> None:
        run('/opt/scylladb/scylla-machine-image/scylla_ec2_check --nic eth0', shell=True)

    def io_setup(self) -> None:
        run('/opt/scylladb/scylla-machine-image/scylla_cloud_io_setup', check=True, shell=True)

    @property
    def user_data(self) -> str:
        base_contents: list[str] = curl(self.META_DATA_BASE_URL).splitlines()
        if 'user-data' in base_contents:
            return curl(self.META_DATA_BASE_URL + 'user-data')
        else:
            return ''



def is_ec2() -> bool:
    return aws_instance.is_aws_instance()

def is_gce() -> bool:
    return gcp_instance.is_gce_instance()

def is_azure() -> bool:
    return azure_instance.is_azure_instance()

def get_cloud_instance() -> cloud_instance:
    if is_ec2():
        return aws_instance()
    elif is_gce():
        return gcp_instance()
    elif is_azure():
        return azure_instance()
    else:
        raise Exception("Unknown cloud provider! Only AWS/GCP/Azure supported.")


CONCOLORS: Final[dict[str, str]] = {'green': '\033[1;32m', 'red': '\033[1;31m', 'nocolor': '\033[0m'}


def colorprint(msg: str, **kwargs: str) -> None:
    fmt: dict[str, str] = dict(CONCOLORS)
    fmt.update(kwargs)
    print(msg.format(**fmt))

def is_redhat_variant() -> bool:
    return 'rhel' in distro.like().split()
