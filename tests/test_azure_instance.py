import base64
import importlib.util
import logging
import re
import sys
import unittest.mock
from collections import namedtuple
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase

import httpretty
import pytest
import yaml


sys.path.append(str(Path(__file__).parent.parent))
import lib.scylla_cloud
from lib.scylla_cloud import AzureInstance


# Load scylla_cloud_io_setup module (file without .py extension)
_io_setup_path = Path(__file__).parent.parent / "common" / "scylla_cloud_io_setup"
_spec = importlib.util.spec_from_loader("scylla_cloud_io_setup", loader=None, origin=str(_io_setup_path))
scylla_cloud_io_setup = importlib.util.module_from_spec(_spec)
with open(_io_setup_path) as _f:
    exec(_f.read(), scylla_cloud_io_setup.__dict__)
AzureIoSetup = scylla_cloud_io_setup.AzureIoSetup
UnsupportedInstanceClassError = scylla_cloud_io_setup.UnsupportedInstanceClassError


LOGGER = logging.getLogger(__name__)

svmem = namedtuple("svmem", ["total"])

sdiskpart = namedtuple("sdiskpart", ["device", "mountpoint"])
mock_disk_partitions = [
    sdiskpart("/dev/root", "/"),
    sdiskpart("/dev/sda15", "/boot/efi"),
    sdiskpart("/dev/md0", "/var/lib/scylla"),
    sdiskpart("/dev/md0", "/var/lib/systemd/coredump"),
    sdiskpart("/dev/sdb1", "/mnt"),
]
mock_disk_partitions_noswap = [
    sdiskpart("/dev/root", "/"),
    sdiskpart("/dev/sda15", "/boot/efi"),
    sdiskpart("/dev/md0", "/var/lib/scylla"),
    sdiskpart("/dev/md0", "/var/lib/systemd/coredump"),
]

mock_listdevdir_standard_l16s_v2 = [
    "sdb1",
    "root",
    "ng1n1",
    "nvme1n1",
    "ng0n1",
    "nvme0n1",
    "sdb",
    "sg1",
    "sda15",
    "sda14",
    "sda1",
    "sda",
    "sg0",
    "nvme1",
    "nvme0",
    "zero",
    "null",
]
mock_listdevdir_standard_l16s_v2_noswap = [
    "root",
    "ng1n1",
    "nvme1n1",
    "ng0n1",
    "nvme0n1",
    "sda15",
    "sda14",
    "sda1",
    "sda",
    "sg0",
    "nvme1",
    "nvme0",
    "zero",
    "null",
]
mock_listdevdir_standard_l32s_v2 = [
    "sdb1",
    "root",
    "ng2n1",
    "nvme2n1",
    "ng3n1",
    "nvme3n1",
    "ng0n1",
    "nvme0n1",
    "ng1n1",
    "nvme1n1",
    "sdb",
    "sda15",
    "sda14",
    "sda1",
    "sda",
    "sg1",
    "sg0",
    "nvme3",
    "nvme2",
    "nvme1",
    "nvme0",
    "zero",
    "null",
]
mock_listdevdir_standard_l16s_v2_2persistent = [
    "sdb1",
    "root",
    "ng1n1",
    "ng0n1",
    "nvme0n1",
    "nvme1n1",
    "sdd",
    "sg4",
    "sdc",
    "sg3",
    "sdb",
    "sda15",
    "sda14",
    "sda1",
    "sda",
    "sg1",
    "sg0",
    "nvme1",
    "nvme0",
    "zero",
    "null",
]
mock_listdevdir_standard_l16s_v2_2persistent_noswap = [
    "root",
    "ng1n1",
    "ng0n1",
    "nvme0n1",
    "nvme1n1",
    "sdc",
    "sg3",
    "sdb",
    "sda15",
    "sda14",
    "sda1",
    "sda",
    "sg1",
    "sg0",
    "nvme1",
    "nvme0",
    "zero",
    "null",
]
mock_glob_glob_dev_standard_l16s_v2 = ["/dev/sdb1", "/dev/sdb", "/dev/sda15", "/dev/sda14", "/dev/sda1", "/dev/sda"]
mock_glob_glob_dev_standard_l16s_v2_noswap = ["/dev/sda15", "/dev/sda14", "/dev/sda1", "/dev/sda"]
mock_glob_glob_dev_standard_l16s_v2_2persistent = [
    "/dev/sdb1",
    "/dev/sdd",
    "/dev/sdc",
    "/dev/sdb",
    "/dev/sda15",
    "/dev/sda14",
    "/dev/sda1",
    "/dev/sda",
]
mock_glob_glob_dev_standard_l16s_v2_2persistent_noswap = [
    "/dev/sdc",
    "/dev/sdb",
    "/dev/sda15",
    "/dev/sda14",
    "/dev/sda1",
    "/dev/sda",
]
mock_glob_glob_dev_standard_l32s_v2 = mock_glob_glob_dev_standard_l16s_v2


def _mock_multi_open(files, filename, *args, **kwargs):
    if filename in files:
        return unittest.mock.mock_open(read_data=files[filename]).return_value
    raise FileNotFoundError(f"Unable to open {filename}")


def mock_multi_open_l(filename, *args, **kwargs):
    files = {"/sys/class/dmi/id/sys_vendor": "Microsoft Corporation", "/etc/waagent.conf": ""}
    return _mock_multi_open(files, filename, *args, **kwargs)


class AzureMetadata:
    def httpretty_azure_metadata(self, instance_type="Standard_L16s_v2", with_userdata=False):
        httpretty.register_uri(
            httpretty.GET,
            "http://169.254.169.254/metadata/instance?api-version=2021-01-01&format=text",
            """
compute/
network/
"""[1:-1],
        )
        httpretty.register_uri(
            httpretty.GET,
            "http://169.254.169.254/metadata/instance/compute/vmSize?api-version=2021-01-01&format=text",
            instance_type,
        )
        httpretty.register_uri(
            httpretty.GET,
            "http://169.254.169.254/metadata/instance/compute/zone?api-version=2021-01-01&format=text",
            "",
        )
        httpretty.register_uri(
            httpretty.GET,
            "http://169.254.169.254/metadata/instance/compute/location?api-version=2021-01-01&format=text",
            "eastus",
        )
        httpretty.register_uri(
            httpretty.GET,
            "http://169.254.169.254/metadata/instance/network/interface/0/ipv4/ipAddress/0/privateIpAddress?api-version=2021-01-01&format=text",
            "172.16.0.1",
        )
        httpretty.register_uri(
            httpretty.GET,
            "http://169.254.169.254/metadata/instance/compute/userData?api-version=2021-01-01&format=text",
            base64.b64encode(b'{"scylla_yaml": {"cluster_name": "test-cluster"}}') if with_userdata else "",
        )

    def httpretty_no_azure_metadata(self):
        httpretty.register_uri(httpretty.GET, re.compile("http://.*"), "", status=404)


class TestAsyncAzureInstance(IsolatedAsyncioTestCase, AzureMetadata):
    def setUp(self):
        httpretty.enable(verbose=True, allow_net_connect=False)

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    async def test_identify_metadata(self):
        self.httpretty_azure_metadata()
        assert await AzureInstance.identify_metadata()

    # XXX: Seems like Github Actions is running in Azure, we cannot disable
    # httpretty here (it suceeded to connect metadata server even we disabled
    # httpretty)
    async def test_not_identify_metadata(self):
        self.httpretty_no_azure_metadata()
        real_curl = lib.scylla_cloud.aiocurl

        async def mocked_curl(*args, **kwargs):
            kwargs["timeout"] = 0.001
            kwargs["retry_interval"] = 0.0001
            return await real_curl(*args, **kwargs)

        with unittest.mock.patch("lib.scylla_cloud.aiocurl", new=mocked_curl):
            assert not await AzureInstance.identify_metadata()


class TestAzureInstance(TestCase, AzureMetadata):
    def setUp(self):
        httpretty.enable(verbose=True, allow_net_connect=False)

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    def test_identify_dmi(self):
        with (
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_l)),
            unittest.mock.patch("os.path.exists", unittest.mock.MagicMock(side_effect=mock_multi_open_l)),
        ):
            assert AzureInstance.identify_dmi()

    def test_endpoint_snitch(self):
        self.httpretty_azure_metadata()
        ins = AzureInstance()
        assert ins.endpoint_snitch == "AzureSnitch"

    def test_instancelocation_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = AzureInstance()
        assert ins.instancelocation == "eastus"

    def test_instancezone_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = AzureInstance()
        assert ins.instancezone == ""

    def test_instancetype_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = AzureInstance()
        assert ins.instancetype == "Standard_L16s_v2"

    def test_instancetype_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type="Standard_L32s_v2")
        ins = AzureInstance()
        assert ins.instancetype == "Standard_L32s_v2"

    def test_cpu_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = AzureInstance()
        with unittest.mock.patch("psutil.cpu_count", return_value=16):
            assert ins.cpu == 16

    def test_cpu_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type="Standard_L32s_v2")
        ins = AzureInstance()
        with unittest.mock.patch("psutil.cpu_count", return_value=32):
            assert ins.cpu == 32

    def test_memoryGB_standard_l16s_v2(self):  # noqa: N802
        self.httpretty_azure_metadata()
        ins = AzureInstance()
        # XXX: the value is little bit less than 128GB
        with unittest.mock.patch("psutil.virtual_memory", return_value=svmem(135185641472)):
            assert ins.memory_gb > 125
            assert ins.memory_gb <= 128

    def test_memoryGB_standard_l32s_v2(self):  # noqa: N802
        self.httpretty_azure_metadata(instance_type="Standard_L32s_v2")
        ins = AzureInstance()
        # XXX: the value is little bit less than 256GB
        with unittest.mock.patch("psutil.virtual_memory", return_value=svmem(270471569408)):
            assert ins.memory_gb > 251
            assert ins.memory_gb <= 254

    def test_instance_purpose_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = AzureInstance()
        assert ins.instance_purpose() == "Standard"

    def test_instance_purpose_basic_a0(self):
        self.httpretty_azure_metadata(instance_type="Basic_A0")
        ins = AzureInstance()
        assert ins.instance_purpose() == "Basic"

    def test_instance_class_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = AzureInstance()
        assert ins.instance_class() == "L16s_v2"

    def test_instance_class_standard_ds1_v2(self):
        self.httpretty_azure_metadata(instance_type="Standard_DS1_v2")
        ins = AzureInstance()
        assert ins.instance_class() == "DS1_v2"

    def test_is_supported_instance_class_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = AzureInstance()
        assert ins.is_supported_instance_class()

    def test_is_not_supported_instance_class_standard_ds1_v2(self):
        self.httpretty_azure_metadata(instance_type="Standard_DS1_v2")
        ins = AzureInstance()
        assert not ins.is_supported_instance_class()

    def test_is_recommended_instance_size_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = AzureInstance()
        with unittest.mock.patch("psutil.cpu_count", return_value=16):
            assert ins.is_recommended_instance_size()

    def test_is_recommended_instance_size_standard_ds1_v2(self):
        self.httpretty_azure_metadata(instance_type="Standard_DS1_v2")
        ins = AzureInstance()
        with unittest.mock.patch("psutil.cpu_count", return_value=1):
            assert not ins.is_recommended_instance_size()

    def test_is_recommended_instance_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = AzureInstance()
        with unittest.mock.patch("psutil.cpu_count", return_value=16):
            assert ins.is_recommended_instance()

    def test_is_not_recommended_instance_standard_ds1_v2(self):
        self.httpretty_azure_metadata(instance_type="Standard_DS1_v2")
        ins = AzureInstance()
        with unittest.mock.patch("psutil.cpu_count", return_value=1):
            assert not ins.is_recommended_instance()

    def test_private_ipv4(self):
        self.httpretty_azure_metadata()
        ins = AzureInstance()
        assert ins.private_ipv4() == "172.16.0.1"

    def test_user_data(self):
        self.httpretty_azure_metadata(with_userdata=True)
        ins = AzureInstance()
        assert ins.user_data == '{"scylla_yaml": {"cluster_name": "test-cluster"}}'

    def test_no_user_data(self):
        self.httpretty_azure_metadata()
        ins = AzureInstance()
        assert ins.user_data == ""

    def test_non_root_nvmes_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l16s_v2),
        ):
            ins = AzureInstance()
            assert ins._non_root_nvmes() == {"root": ["/dev/root"], "ephemeral": ["nvme1n1", "nvme0n1"]}

    def test_non_root_nvmes_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type="Standard_L32s_v2")
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l32s_v2),
        ):
            ins = AzureInstance()
            assert ins._non_root_nvmes() == {
                "root": ["/dev/root"],
                "ephemeral": ["nvme2n1", "nvme3n1", "nvme0n1", "nvme1n1"],
            }

    def test_non_root_disks_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2),
            unittest.mock.patch("os.path.exists", return_value=True),
            unittest.mock.patch("os.path.realpath", return_value="/dev/sdb"),
        ):
            ins = AzureInstance()
            assert ins._non_root_disks() == {"persistent": [], "swap": ["sdb"]}

    def test_non_root_disks_standard_l16s_v2_noswap(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions_noswap),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2_noswap),
            unittest.mock.patch("os.path.exists", return_value=False),
        ):
            ins = AzureInstance()
            assert ins._non_root_disks() == {"persistent": [], "swap": []}

    def test_non_root_disks_standard_l16s_v2_2persistent(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent),
            unittest.mock.patch("os.path.exists", return_value=True),
            unittest.mock.patch("os.path.realpath", return_value="/dev/sdb"),
        ):
            ins = AzureInstance()
            assert ins._non_root_disks() == {"persistent": ["sdd", "sdc"], "swap": ["sdb"]}

    def test_non_root_disks_standard_l16s_v2_2persistent_noswap(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions_noswap),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent_noswap),
            unittest.mock.patch("os.path.exists", return_value=False),
        ):
            ins = AzureInstance()
            assert ins._non_root_disks() == {"persistent": ["sdc", "sdb"], "swap": []}

    def test_non_root_disks_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type="Standard_L32s_v2")
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l32s_v2),
            unittest.mock.patch("os.path.exists", return_value=True),
            unittest.mock.patch("os.path.realpath", return_value="/dev/sdb"),
        ):
            ins = AzureInstance()
            assert ins._non_root_disks() == {"persistent": [], "swap": ["sdb"]}

    def test_os_disks_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l16s_v2),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2),
            unittest.mock.patch("os.path.exists", return_value=True),
            unittest.mock.patch("os.path.realpath", return_value="/dev/sdb"),
        ):
            ins = AzureInstance()
            assert ins.os_disks == {
                "root": ["/dev/root"],
                "ephemeral": ["nvme1n1", "nvme0n1"],
                "persistent": [],
                "swap": ["sdb"],
            }

    def test_os_disks_standard_l16s_v2_noswap(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions_noswap),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l16s_v2_noswap),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2_noswap),
            unittest.mock.patch("os.path.exists", return_value=False),
        ):
            ins = AzureInstance()
            assert ins.os_disks == {
                "root": ["/dev/root"],
                "ephemeral": ["nvme1n1", "nvme0n1"],
                "persistent": [],
                "swap": [],
            }

    def test_os_disks_standard_l16s_v2_2persistent(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l16s_v2_2persistent),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent),
            unittest.mock.patch("os.path.exists", return_value=True),
            unittest.mock.patch("os.path.realpath", return_value="/dev/sdb"),
        ):
            ins = AzureInstance()
            assert ins.os_disks == {
                "root": ["/dev/root"],
                "ephemeral": ["nvme0n1", "nvme1n1"],
                "persistent": ["sdd", "sdc"],
                "swap": ["sdb"],
            }

    def test_os_disks_standard_l16s_v2_2persistent_noswap(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions_noswap),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l16s_v2_2persistent_noswap),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent_noswap),
            unittest.mock.patch("os.path.exists", return_value=False),
        ):
            ins = AzureInstance()
            assert ins.os_disks == {
                "root": ["/dev/root"],
                "ephemeral": ["nvme0n1", "nvme1n1"],
                "persistent": ["sdc", "sdb"],
                "swap": [],
            }

    def test_os_disks_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type="Standard_L32s_v2")
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l32s_v2),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l32s_v2),
            unittest.mock.patch("os.path.exists", return_value=True),
            unittest.mock.patch("os.path.realpath", return_value="/dev/sdb"),
        ):
            ins = AzureInstance()
            assert ins.os_disks == {
                "root": ["/dev/root"],
                "ephemeral": ["nvme2n1", "nvme3n1", "nvme0n1", "nvme1n1"],
                "persistent": [],
                "swap": ["sdb"],
            }

    def test_get_local_disks_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l16s_v2),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2),
            unittest.mock.patch("os.path.exists", return_value=True),
            unittest.mock.patch("os.path.realpath", return_value="/dev/sdb"),
        ):
            ins = AzureInstance()
            assert ins.get_local_disks() == ["nvme1n1", "nvme0n1"]

    def test_get_local_disks_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type="Standard_L32s_v2")
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l32s_v2),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l32s_v2),
            unittest.mock.patch("os.path.exists", return_value=True),
            unittest.mock.patch("os.path.realpath", return_value="/dev/sdb"),
        ):
            AzureInstance()
            assert ["nvme2n1", "nvme3n1", "nvme0n1", "nvme1n1"]

    def test_get_remote_disks_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l16s_v2),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2),
            unittest.mock.patch("os.path.exists", return_value=True),
            unittest.mock.patch("os.path.realpath", return_value="/dev/sdb"),
        ):
            ins = AzureInstance()
            assert ins.get_remote_disks() == []

    def test_get_remote_disks_standard_l16s_v2_noswap(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions_noswap),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l16s_v2_noswap),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2_noswap),
            unittest.mock.patch("os.path.exists", return_value=False),
        ):
            ins = AzureInstance()
            assert ins.get_remote_disks() == []

    def test_get_remote_disks_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type="Standard_L32s_v2")
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l32s_v2),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l32s_v2),
            unittest.mock.patch("os.path.exists", return_value=True),
            unittest.mock.patch("os.path.realpath", return_value="/dev/sdb"),
        ):
            ins = AzureInstance()
            assert ins.get_remote_disks() == []

    def test_get_remote_disks_standard_l16s_v2_2persistent(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l16s_v2_2persistent),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent),
            unittest.mock.patch("os.path.exists", return_value=True),
            unittest.mock.patch("os.path.realpath", return_value="/dev/sdb"),
        ):
            ins = AzureInstance()
            assert ins.get_remote_disks() == ["sdd", "sdc"]

    def test_get_remote_disks_standard_l16s_v2_2persistent_noswap(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions_noswap),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l16s_v2_2persistent_noswap),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent_noswap),
            unittest.mock.patch("os.path.exists", return_value=False),
        ):
            ins = AzureInstance()
            assert ins.get_remote_disks() == ["sdc", "sdb"]

    def test_get_swap_disks_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l16s_v2),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2),
            unittest.mock.patch("os.path.exists", return_value=True),
            unittest.mock.patch("os.path.realpath", return_value="/dev/sdb"),
        ):
            ins = AzureInstance()
            assert ins.get_swap_disks() == ["sdb"]

    def test_get_swap_disks_standard_l16s_v2_noswap(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions_noswap),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l16s_v2_noswap),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2_noswap),
            unittest.mock.patch("os.path.exists", return_value=False),
        ):
            ins = AzureInstance()
            assert ins.get_swap_disks() == []

    def test_get_swap_disks_standard_l16s_v2_2persistent(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l16s_v2_2persistent),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent),
            unittest.mock.patch("os.path.exists", return_value=True),
            unittest.mock.patch("os.path.realpath", return_value="/dev/sdb"),
        ):
            ins = AzureInstance()
            assert ins.get_swap_disks() == ["sdb"]

    def test_get_swap_disks_standard_l16s_v2_2persistent_noswap(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions_noswap),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l16s_v2_2persistent_noswap),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent_noswap),
            unittest.mock.patch("os.path.exists", return_value=False),
        ):
            ins = AzureInstance()
            assert ins.get_swap_disks() == []

    def test_nvme_disk_count_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l16s_v2),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l16s_v2),
        ):
            ins = AzureInstance()
            assert ins.nvme_disk_count == 2

    def test_nvme_disk_count_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type="Standard_L32s_v2")
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_standard_l32s_v2),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_standard_l32s_v2),
        ):
            ins = AzureInstance()
            assert ins.nvme_disk_count == 4


# Sample Azure IO params for testing
MOCK_AZURE_IO_PARAMS = {
    "Standard_L16s_v2": {
        "disks": [
            {
                "read_iops": 1098789,
                "read_bandwidth": 5994220544,
                "write_iops": 439356,
                "write_bandwidth": 2996842496,
            }
        ]
    },
    "Standard_L8s_v3": {
        "disks": [
            {
                "read_iops": 14245,
                "read_bandwidth": 3734856960,
                "write_iops": 10017,
                "write_bandwidth": 2612137984,
            }
        ]
    },
}


class MockAzureInstance:
    """Mock Azure instance for testing AzureIoSetup."""

    def __init__(self, instance_type="Standard_L16s_v2", supported=True):
        self.instancetype = instance_type
        self._supported = supported

    def is_supported_instance_class(self):
        return self._supported


class TestAzureIoSetup(TestCase, AzureMetadata):
    """Tests for AzureIoSetup class."""

    def setUp(self):
        httpretty.enable(verbose=True, allow_net_connect=False)

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    def test_azure_io_setup_with_known_instance_type(self):
        """Test that AzureIoSetup correctly loads IO params from YAML file."""
        mock_instance = MockAzureInstance(instance_type="Standard_L16s_v2")
        io_setup = AzureIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_AZURE_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch.object(io_setup, "save") as mock_save,
        ):
            io_setup.generate()

            # Verify disk properties were set correctly
            assert io_setup.disk_properties["mountpoint"] == "/var/lib/scylla"
            assert io_setup.disk_properties["read_iops"] == 1098789
            assert io_setup.disk_properties["read_bandwidth"] == 5994220544
            assert io_setup.disk_properties["write_iops"] == 439356
            assert io_setup.disk_properties["write_bandwidth"] == 2996842496
            mock_save.assert_called_once()

    def test_azure_io_setup_with_different_instance_type(self):
        """Test AzureIoSetup with Standard_L8s_v3 instance type."""
        mock_instance = MockAzureInstance(instance_type="Standard_L8s_v3")
        io_setup = AzureIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_AZURE_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch.object(io_setup, "save") as mock_save,
        ):
            io_setup.generate()

            assert io_setup.disk_properties["mountpoint"] == "/var/lib/scylla"
            assert io_setup.disk_properties["read_iops"] == 14245
            assert io_setup.disk_properties["read_bandwidth"] == 3734856960
            assert io_setup.disk_properties["write_iops"] == 10017
            assert io_setup.disk_properties["write_bandwidth"] == 2612137984
            mock_save.assert_called_once()

    def test_azure_io_setup_fallback_when_file_not_found(self):
        """Test that AzureIoSetup falls back to scylla_io_setup when YAML file is not found."""
        mock_instance = MockAzureInstance(instance_type="Standard_L16s_v2")
        io_setup = AzureIoSetup(mock_instance)

        with (
            unittest.mock.patch("builtins.open", side_effect=FileNotFoundError("File not found")),
            unittest.mock.patch("subprocess.run") as mock_run,
        ):
            io_setup.generate()
            mock_run.assert_called_once_with(
                "scylla_io_setup", shell=True, check=True, capture_output=True, timeout=300
            )

    def test_azure_io_setup_fallback_when_instance_not_in_yaml(self):
        """Test that AzureIoSetup falls back to scylla_io_setup when instance type is not in YAML."""
        mock_instance = MockAzureInstance(instance_type="Standard_L999s_v99")
        io_setup = AzureIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_AZURE_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch("subprocess.run") as mock_run,
        ):
            io_setup.generate()
            mock_run.assert_called_once_with(
                "scylla_io_setup", shell=True, check=True, capture_output=True, timeout=300
            )

    def test_azure_io_setup_raises_for_unsupported_instance_class(self):
        """Test that AzureIoSetup raises UnsupportedInstanceClassError for unsupported instances."""
        mock_instance = MockAzureInstance(instance_type="Standard_L16s_v2", supported=False)
        io_setup = AzureIoSetup(mock_instance)

        with pytest.raises(UnsupportedInstanceClassError):
            io_setup.generate()
