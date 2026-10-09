import importlib.util
import json
import logging
import sys
import unittest.mock
from collections import namedtuple
from pathlib import Path
from subprocess import CalledProcessError
from unittest import IsolatedAsyncioTestCase, TestCase

import httpretty
import pytest
import yaml


sys.path.append(str(Path(__file__).parent.parent))
import lib.scylla_cloud
from lib.scylla_cloud import AwsInstance


# Load scylla_cloud_io_setup module (file without .py extension)
_io_setup_path = Path(__file__).parent.parent / "common" / "scylla_cloud_io_setup"
_spec = importlib.util.spec_from_loader("scylla_cloud_io_setup", loader=None, origin=str(_io_setup_path))
scylla_cloud_io_setup = importlib.util.module_from_spec(_spec)
with open(_io_setup_path) as _f:
    exec(_f.read(), scylla_cloud_io_setup.__dict__)
AwsIoSetup = scylla_cloud_io_setup.AwsIoSetup
UnsupportedInstanceClassError = scylla_cloud_io_setup.UnsupportedInstanceClassError


LOGGER = logging.getLogger(__name__)


def _mock_multi_open(files, filename, *args, **kwargs):
    if filename in files:
        return unittest.mock.mock_open(read_data=files[filename]).return_value
    raise FileNotFoundError(f"Unable to open {filename}")


proc_filesystems = "	xfs\n"


def mock_multi_open_i3en_2xlarge(filename, *args, **kwargs):
    files = {
        "/sys/class/dmi/id/product_version": "",
        "/sys/class/dmi/id/bios_vendor": "Amazon EC2",
        "/sys/class/net/eth0/address": "00:00:5e:00:53:00\n",
        "/sys/class/nvme/nvme0/model": "Amazon Elastic Block Store\n",
        "/sys/class/nvme/nvme1/model": "Amazon EC2 NVMe Instance Storage\n",
        "/sys/class/nvme/nvme2/model": "Amazon EC2 NVMe Instance Storage\n",
        "/proc/filesystems": proc_filesystems,
    }
    return _mock_multi_open(files, filename, *args, **kwargs)


def mock_multi_open_i3en_2xlarge_with_ebs(filename, *args, **kwargs):
    files = {
        "/sys/class/dmi/id/product_version": "",
        "/sys/class/dmi/id/bios_vendor": "Amazon EC2",
        "/sys/class/net/eth0/address": "00:00:5e:00:53:00\n",
        "/sys/class/nvme/nvme0/model": "Amazon Elastic Block Store\n",
        "/sys/class/nvme/nvme1/model": "Amazon Elastic Block Store\n",
        "/sys/class/nvme/nvme2/model": "Amazon Elastic Block Store\n",
        "/sys/class/nvme/nvme3/model": "Amazon EC2 NVMe Instance Storage\n",
        "/sys/class/nvme/nvme4/model": "Amazon EC2 NVMe Instance Storage\n",
        "/proc/filesystems": proc_filesystems,
    }
    return _mock_multi_open(files, filename, *args, **kwargs)


def mock_multi_open_i3_2xlarge(filename, *args, **kwargs):
    files = {
        "/sys/class/dmi/id/product_version": "4.11.amazon",
        "/sys/class/dmi/id/bios_vendor": "Xen",
        "/sys/class/net/eth0/address": "00:00:5e:00:53:00\n",
        "/sys/class/nvme/nvme0/model": "Amazon EC2 NVMe Instance Storage\n",
        "/sys/class/nvme/nvme1/model": "Amazon EC2 NVMe Instance Storage\n",
        "/proc/filesystems": proc_filesystems,
    }
    return _mock_multi_open(files, filename, *args, **kwargs)


CompletedProcess = namedtuple("CompletedProcess", ["stdout"])


def _mock_multi_run(programs, *popenargs, input=None, capture_output=False, timeout=None, check=False, **kwargs):
    cmd = popenargs[0]
    if cmd in programs:
        return CompletedProcess(programs[cmd])
    raise CalledProcessError(-1, cmd)


def mock_multi_run_i3en_2xlarge(*popenargs, input=None, capture_output=False, timeout=None, check=False, **kwargs):
    programs = {"findmnt -n -o SOURCE /": "/dev/nvme0n1p1\n"}
    return _mock_multi_run(programs, *popenargs, input, capture_output, timeout, check, **kwargs)


def mock_multi_run_i3_2xlarge(*popenargs, input=None, capture_output=False, timeout=None, check=False, **kwargs):
    programs = {"findmnt -n -o SOURCE /": "/dev/xvda1\n"}
    return _mock_multi_run(programs, *popenargs, input, capture_output, timeout, check, **kwargs)


sdiskpart = namedtuple("sdiskpart", ["device", "mountpoint"])
mock_disk_partitions = [
    sdiskpart("/dev/root", "/"),
    sdiskpart(device="/dev/md0", mountpoint="/var/lib/scylla"),
    sdiskpart(device="/dev/md0", mountpoint="/var/lib/systemd/coredump"),
]

mock_listdevdir_i3en_2xlarge = [
    "root",
    "nvme0n1p1",
    "nvme0n1",
    "nvme2n1",
    "nvme1n1",
    "nvme2",
    "nvme1",
    "nvme0",
    "zero",
    "null",
]
mock_listdevdir_i3en_2xlarge_with_ebs = [
    "md0",
    "root",
    "nvme0n1p1",
    "nvme0n1",
    "nvme3n1",
    "nvme4n1",
    "nvme2n1",
    "nvme4",
    "nvme1n1",
    "nvme3",
    "nvme2",
    "nvme1",
    "nvme0",
    "zero",
    "null",
]
mock_listdevdir_i3_2xlarge = ["md0", "root", "nvme0n1", "nvme1n1", "xvda1", "xvda", "nvme1", "nvme0", "zero", "null"]


class AwsMetadata:
    def httpretty_aws_metadata(self, instance_type="i3en.2xlarge", with_ebs=False, with_userdata=False):
        if not with_userdata:
            httpretty.register_uri(
                httpretty.GET,
                "http://169.254.169.254/latest/",
                """
dynamic
meta-data
"""[1:-1],
            )
        else:
            httpretty.register_uri(
                httpretty.GET,
                "http://169.254.169.254/latest/",
                """
dynamic
meta-data
user-data
"""[1:-1],
            )
        httpretty.register_uri(
            httpretty.PUT,
            "http://169.254.169.254/latest/api/token",
            "AQAAAONS_5Sm5ED3PboTRTN6YZXlUYrW441avHNVzV74vTtP2JL-vw==",
        )
        httpretty.register_uri(httpretty.GET, "http://169.254.169.254/latest/meta-data/instance-type", instance_type)
        if not with_ebs:
            httpretty.register_uri(
                httpretty.GET,
                "http://169.254.169.254/latest/meta-data/block-device-mapping",
                """
ami
root
"""[1:-1],
            )
        else:
            httpretty.register_uri(
                httpretty.GET,
                "http://169.254.169.254/latest/meta-data/block-device-mapping",
                """
ami
ebs2
ebs3
root
"""[1:-1],
            )
            httpretty.register_uri(
                httpretty.GET, "http://169.254.169.254/latest/meta-data/block-device-mapping/ebs2", "sdb"
            )
            httpretty.register_uri(
                httpretty.GET, "http://169.254.169.254/latest/meta-data/block-device-mapping/ebs3", "sdc"
            )
        httpretty.register_uri(
            httpretty.GET,
            "http://169.254.169.254/latest/meta-data/block-device-mapping/ami",
            "/dev/sda1" if instance_type == "i3.2xlarge" else "sda1",
        )
        httpretty.register_uri(
            httpretty.GET, "http://169.254.169.254/latest/meta-data/block-device-mapping/root", "/dev/sda1"
        )
        httpretty.register_uri(httpretty.GET, "http://169.254.169.254/latest/meta-data/public-ipv4", "10.0.0.1")
        httpretty.register_uri(httpretty.GET, "http://169.254.169.254/latest/meta-data/local-ipv4", "172.16.0.1")
        httpretty.register_uri(
            httpretty.GET,
            "http://169.254.169.254/latest/meta-data/network/interfaces/macs/00:00:5e:00:53:00",
            """
device-number
interface-id
ipv4-associations/
local-hostname
local-ipv4s
mac
owner-id
public-hostname
public-ipv4s
security-group-ids
security-groups
subnet-id
subnet-ipv4-cidr-block
vpc-id
vpc-ipv4-cidr-block
vpc-ipv4-cidr-blocks
vpc-ipv6-cidr-blocks
"""[1:-1],
        )
        if with_userdata:
            httpretty.register_uri(
                httpretty.GET,
                "http://169.254.169.254/latest/user-data",
                '{"scylla_yaml": {"cluster_name": "test-cluster"}}',
            )


class TestAsyncAwsInstance(IsolatedAsyncioTestCase, AwsMetadata):
    def setUp(self):
        httpretty.enable(verbose=True, allow_net_connect=False)

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    async def test_identify_metadata(self):
        self.httpretty_aws_metadata()
        assert await AwsInstance.identify_metadata()

    async def test_not_identify_metadata(self):
        httpretty.disable()
        real_curl = lib.scylla_cloud.aiocurl

        async def mocked_curl(*args, **kwargs):
            kwargs["timeout"] = 0.1
            kwargs["retry_interval"] = 0.001
            return await real_curl(*args, **kwargs)

        with unittest.mock.patch("lib.scylla_cloud.aiocurl", new=mocked_curl):
            assert not await AwsInstance.identify_metadata()


class TestAwsInstance(TestCase, AwsMetadata):
    def setUp(self):
        httpretty.enable(verbose=True, allow_net_connect=False)

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    def test_endpoint_snitch(self):
        self.httpretty_aws_metadata()
        ins = AwsInstance()
        assert ins.endpoint_snitch == "Ec2Snitch"

    def test_is_supported_instance_class_r8gd(self):
        self.httpretty_aws_metadata(instance_type="r8gd.8xlarge")
        ins = AwsInstance()
        assert ins.is_supported_instance_class()
        assert ins.get_en_interface_type() == "ena"

    def test_instancetype_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        ins = AwsInstance()
        assert ins.instancetype == "i3en.2xlarge"

    def test_instancetype_t3_nano(self):
        self.httpretty_aws_metadata(instance_type="t3.nano")
        ins = AwsInstance()
        assert ins.instancetype == "t3.nano"

    def test_instance_size_2xlarge(self):
        self.httpretty_aws_metadata()
        ins = AwsInstance()
        assert ins.instance_size() == "2xlarge"

    def test_instance_size_nano(self):
        self.httpretty_aws_metadata(instance_type="t3.nano")
        ins = AwsInstance()
        assert ins.instance_size() == "nano"

    def test_instance_class_i3en(self):
        self.httpretty_aws_metadata()
        ins = AwsInstance()
        assert ins.instance_class() == "i3en"

    def test_instance_class_t3(self):
        self.httpretty_aws_metadata(instance_type="t3.nano")
        ins = AwsInstance()
        assert ins.instance_class() == "t3"

    def test_is_supported_instance_class(self):
        self.httpretty_aws_metadata()
        ins = AwsInstance()
        assert ins.is_supported_instance_class()

    def test_is_not_supported_instance_class(self):
        self.httpretty_aws_metadata(instance_type="t3.nano")
        ins = AwsInstance()
        assert not ins.is_supported_instance_class()

    def test_is_supported_instance_class_m8gd(self):
        self.httpretty_aws_metadata(instance_type="m8gd.4xlarge")
        ins = AwsInstance()
        assert ins.is_supported_instance_class()
        assert ins.get_en_interface_type() == "ena"

    def test_is_supported_instance_class_m9gd(self):
        self.httpretty_aws_metadata(instance_type="m9gd.metal-48xl")
        ins = AwsInstance()
        assert ins.is_supported_instance_class()
        assert ins.get_en_interface_type() == "ena"

    def test_get_en_interface_type_ena(self):
        self.httpretty_aws_metadata()
        ins = AwsInstance()
        assert ins.get_en_interface_type() == "ena"

    def test_get_en_interface_type_ixgbevf(self):
        self.httpretty_aws_metadata(instance_type="c3.large")
        ins = AwsInstance()
        assert ins.get_en_interface_type() == "ixgbevf"

    def test_public_ipv4(self):
        self.httpretty_aws_metadata()
        ins = AwsInstance()
        assert ins.public_ipv4() == "10.0.0.1"

    def test_private_ipv4(self):
        self.httpretty_aws_metadata()
        ins = AwsInstance()
        assert ins.private_ipv4() == "172.16.0.1"

    def test_is_vpc_enabled(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins.is_vpc_enabled()

    def test_user_data(self):
        self.httpretty_aws_metadata(with_userdata=True)
        ins = AwsInstance()
        assert ins.user_data == '{"scylla_yaml": {"cluster_name": "test-cluster"}}'

    def test_no_user_data(self):
        self.httpretty_aws_metadata()
        ins = AwsInstance()
        assert not ins.user_data

    def test_identify_dmi_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)):
            assert AwsInstance.identify_dmi()

    def test_non_root_nvmes_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins._non_root_nvmes() == {"root": ["/dev/nvme0n1p1"], "ephemeral": ["nvme2n1", "nvme1n1"], "ebs": []}

    def test_populate_disks_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins._disks["root"] == ["/dev/nvme0n1p1"]
            assert ins._disks["ephemeral"] == ["nvme2n1", "nvme1n1"]
            assert ins._disks["ebs"] == []

    def test_disks_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins.disks() == {"nvme"}

    def test_root_device_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins.root_device() == {"/dev/nvme0n1p1"}

    def test_root_disk_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins.root_disk() == "nvme"

    def test_non_root_disks_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins.non_root_disks() == {"nvme2n1", "nvme1n1"}

    def test_nvme_disk_count_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins.nvme_disk_count == 2

    def test_get_local_disks_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins.get_local_disks() == ["nvme2n1", "nvme1n1"]

    def test_get_remote_disks_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins.get_remote_disks() == []

    def test_non_root_nvmes_i3en_2xlarge_with_ebs(self):
        self.httpretty_aws_metadata(with_ebs=True)
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge_with_ebs),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch(
                "builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge_with_ebs)
            ),
        ):
            ins = AwsInstance()
            assert ins._non_root_nvmes() == {
                "root": ["/dev/nvme0n1p1"],
                "ephemeral": ["nvme3n1", "nvme4n1"],
                "ebs": ["nvme2n1", "nvme1n1"],
            }

    def test_populate_disks_i3en_2xlarge_with_ebs(self):
        self.httpretty_aws_metadata(with_ebs=True)
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge_with_ebs),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch(
                "builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge_with_ebs)
            ),
        ):
            ins = AwsInstance()
            assert ins._disks["root"] == ["/dev/nvme0n1p1"]
            assert ins._disks["ephemeral"] == ["nvme3n1", "nvme4n1"]
            assert ins._disks["ebs"] == ["nvme2n1", "nvme1n1"]

    def test_non_root_disks_i3en_2xlarge_with_ebs(self):
        self.httpretty_aws_metadata(with_ebs=True)
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge_with_ebs),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch(
                "builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge_with_ebs)
            ),
        ):
            ins = AwsInstance()
            assert ins.non_root_disks() == {"nvme2n1", "nvme4n1", "nvme1n1", "nvme3n1"}

    def test_nvme_disk_count_i3en_2xlarge_with_ebs(self):
        self.httpretty_aws_metadata(with_ebs=True)
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge_with_ebs),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch(
                "builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge_with_ebs)
            ),
        ):
            ins = AwsInstance()
            assert ins.nvme_disk_count == 4

    def test_get_local_disks_i3en_2xlarge_with_ebs(self):
        self.httpretty_aws_metadata(with_ebs=True)
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge_with_ebs),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch(
                "builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge_with_ebs)
            ),
        ):
            ins = AwsInstance()
            assert ins.get_local_disks() == ["nvme3n1", "nvme4n1"]

    def test_get_remote_disks_i3en_2xlarge_with_ebs(self):
        self.httpretty_aws_metadata(with_ebs=True)
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3en_2xlarge_with_ebs),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3en_2xlarge),
            unittest.mock.patch(
                "builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge_with_ebs)
            ),
        ):
            ins = AwsInstance()
            assert ins.get_remote_disks() == ["nvme2n1", "nvme1n1"]

    def test_identify_dmi_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)):
            assert AwsInstance.identify_dmi()

    def test_non_root_nvmes_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins._non_root_nvmes() == {"root": ["/dev/xvda1"], "ephemeral": ["nvme0n1", "nvme1n1"], "ebs": []}

    def test_populate_disks_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins._disks["root"] == ["/dev/xvda1"]
            assert ins._disks["ephemeral"] == ["nvme0n1", "nvme1n1"]
            assert ins._disks["ebs"] == []

    def test_disks_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins.disks() == {"nvme", "xvda"}

    def test_root_device_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins.root_device() == {"/dev/xvda1"}

    def test_root_disk_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins.root_disk() == "xvda"

    def test_non_root_disks_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins.non_root_disks() == {"nvme0n1", "nvme1n1"}

    def test_nvme_disk_count_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins.nvme_disk_count == 2

    def test_get_local_disks_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins.get_local_disks() == ["nvme0n1", "nvme1n1"]

    def test_get_remote_disks_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_i3_2xlarge),
            unittest.mock.patch("lib.scylla_cloud.run", side_effect=mock_multi_run_i3_2xlarge),
            unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)),
        ):
            ins = AwsInstance()
            assert ins.get_remote_disks() == []


# Sample AWS IO params for testing
MOCK_AWS_IO_PARAMS = {
    "i3.xlarge": {
        "read_iops": 200800,
        "read_bandwidth": 1185106376,
        "write_iops": 53180,
        "write_bandwidth": 423621267,
    },
    "i3.ALL": {
        "read_iops": 411200,
        "read_bandwidth": 2015342735,
        "write_iops": 181500,
        "write_bandwidth": 808775652,
    },
}


class MockAwsInstance:
    """Mock AWS instance for testing AwsIoSetup."""

    def __init__(self, instance_type="i3.xlarge", instance_class="i3", local_disks=None, supported=True):
        self.instancetype = instance_type
        self._instance_class = instance_class
        self._local_disks = local_disks if local_disks else ["nvme0n1"]
        self._supported = supported

    def is_supported_instance_class(self):
        return self._supported

    def instance_class(self):
        return self._instance_class

    def get_local_disks(self):
        return self._local_disks


class TestAwsIoSetup(TestCase):
    """Tests for AwsIoSetup class."""

    def test_aws_io_setup_with_known_instance_type(self):
        """Test that AwsIoSetup correctly loads IO params from YAML file."""
        mock_instance = MockAwsInstance(instance_type="i3.xlarge", local_disks=["nvme0n1"])
        io_setup = AwsIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_AWS_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch.object(io_setup, "save") as mock_save,
        ):
            io_setup.generate()

            # Verify disk properties were set correctly (multiplied by nr_disks=1)
            assert io_setup.disk_properties["mountpoint"] == "/var/lib/scylla"
            assert io_setup.disk_properties["read_iops"] == 200800
            assert io_setup.disk_properties["read_bandwidth"] == 1185106376
            assert io_setup.disk_properties["write_iops"] == 53180
            assert io_setup.disk_properties["write_bandwidth"] == 423621267
            mock_save.assert_called_once()

    def test_aws_io_setup_with_multiple_disks(self):
        """Test AwsIoSetup correctly multiplies IO params by number of disks."""
        mock_instance = MockAwsInstance(instance_type="i3.xlarge", local_disks=["nvme0n1", "nvme1n1"])
        io_setup = AwsIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_AWS_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch.object(io_setup, "save") as mock_save,
        ):
            io_setup.generate()

            # Verify disk properties were multiplied by 2 (nr_disks)
            assert io_setup.disk_properties["read_iops"] == 200800 * 2
            assert io_setup.disk_properties["read_bandwidth"] == 1185106376 * 2
            assert io_setup.disk_properties["write_iops"] == 53180 * 2
            assert io_setup.disk_properties["write_bandwidth"] == 423621267 * 2
            mock_save.assert_called_once()

    def test_aws_io_setup_fallback_to_instance_class_all(self):
        """Test AwsIoSetup falls back to instance_class.ALL when specific type not found."""
        mock_instance = MockAwsInstance(instance_type="i3.8xlarge", instance_class="i3", local_disks=["nvme0n1"])
        io_setup = AwsIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_AWS_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch.object(io_setup, "save") as mock_save,
        ):
            io_setup.generate()

            # Should use i3.ALL values
            assert io_setup.disk_properties["read_iops"] == 411200
            assert io_setup.disk_properties["read_bandwidth"] == 2015342735
            mock_save.assert_called_once()

    def test_aws_io_setup_fallback_to_scylla_io_setup(self):
        """Test that AwsIoSetup falls back to scylla_io_setup when instance type not in YAML."""
        mock_instance = MockAwsInstance(
            instance_type="unknown.xlarge", instance_class="unknown", local_disks=["nvme0n1"]
        )
        io_setup = AwsIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_AWS_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch("subprocess.run") as mock_run,
        ):
            io_setup.generate()
            mock_run.assert_called_once_with(
                "scylla_io_setup", shell=True, check=True, capture_output=True, timeout=300
            )

    def test_aws_io_setup_raises_for_unsupported_instance_class(self):
        """Test that AwsIoSetup raises UnsupportedInstanceClassError for unsupported instances."""
        mock_instance = MockAwsInstance(instance_type="i3.xlarge", supported=False)
        io_setup = AwsIoSetup(mock_instance)

        with pytest.raises(UnsupportedInstanceClassError):
            io_setup.generate()


@pytest.mark.unit
class TestAwsM8gdM9gdParams(TestCase):
    """m8gd/m9gd must have both pre-configured IO params and network params."""

    io_params_path = Path(__file__).parent.parent / "common" / "aws_io_params.yaml"
    net_params_path = Path(__file__).parent.parent / "common" / "aws_net_params.json"

    sizes = [
        "medium",
        "large",
        "xlarge",
        "2xlarge",
        "4xlarge",
        "8xlarge",
        "12xlarge",
        "16xlarge",
        "24xlarge",
        "48xlarge",
    ]

    def test_io_and_net_params_present(self):
        with open(self.io_params_path) as f:
            io_params = yaml.safe_load(f)
        with open(self.net_params_path) as f:
            net_params = {info[0] for info in json.load(f)}

        for family in ("m8gd", "m9gd"):
            for size in self.sizes:
                instance_type = f"{family}.{size}"
                assert instance_type in io_params, f"{instance_type} missing from aws_io_params.yaml"
                assert instance_type in net_params, f"{instance_type} missing from aws_net_params.json"
                params = io_params[instance_type]
                for key in ("read_iops", "read_bandwidth", "write_iops", "write_bandwidth"):
                    assert params[key] > 0, f"{instance_type}: {key} must be positive"

    def test_metal_sizes_match_their_virtualized_counterpart(self):
        with open(self.io_params_path) as f:
            io_params = yaml.safe_load(f)

        for metal, virtualized in (
            ("m8gd.metal-24xl", "m8gd.24xlarge"),
            ("m8gd.metal-48xl", "m8gd.48xlarge"),
            ("m9gd.metal-48xl", "m9gd.48xlarge"),
        ):
            assert io_params[metal] == io_params[virtualized]


# Local NVMe disks per instance type as (count, size in GB), from `aws ec2 describe-instance-types`
# (InstanceStorageInfo.Disks).
# fmt: off
AWS_LOCAL_DISKS = {
    "c5d": {"large": (1, 50), "xlarge": (1, 100), "2xlarge": (1, 200), "4xlarge": (1, 400), "9xlarge": (1, 900), "12xlarge": (2, 900), "18xlarge": (2, 900), "24xlarge": (4, 900), "metal": (4, 900)},
    "c6gd": {"medium": (1, 59), "large": (1, 118), "xlarge": (1, 237), "2xlarge": (1, 474), "4xlarge": (1, 950), "8xlarge": (1, 1900), "12xlarge": (2, 1425), "16xlarge": (2, 1900), "metal": (2, 1900)},
    "i3": {"large": (1, 475), "xlarge": (1, 950)},
    "i3en": {"large": (1, 1250), "xlarge": (1, 2500), "2xlarge": (2, 2500)},
    "i4g": {"large": (1, 468), "xlarge": (1, 937), "2xlarge": (1, 1875), "4xlarge": (1, 3750), "8xlarge": (2, 3750), "16xlarge": (4, 3750)},
    "i4i": {"large": (1, 468), "xlarge": (1, 937), "2xlarge": (1, 1875), "4xlarge": (1, 3750), "8xlarge": (2, 3750), "12xlarge": (3, 3750), "16xlarge": (4, 3750), "24xlarge": (6, 3750), "32xlarge": (8, 3750), "metal": (8, 3750)},
    "i7i": {"large": (1, 468), "xlarge": (1, 937), "2xlarge": (1, 1875), "4xlarge": (1, 3750), "8xlarge": (2, 3750), "12xlarge": (3, 3750), "16xlarge": (4, 3750), "24xlarge": (6, 3750)},
    "i7ie": {"large": (1, 1250), "xlarge": (1, 2500), "2xlarge": (2, 2500), "3xlarge": (1, 7500), "6xlarge": (2, 7500), "12xlarge": (4, 7500), "18xlarge": (6, 7500), "24xlarge": (8, 7500)},
    "i8g": {"large": (1, 468), "xlarge": (1, 937), "2xlarge": (1, 1875), "4xlarge": (1, 3750), "8xlarge": (2, 3750), "12xlarge": (3, 3750), "16xlarge": (4, 3750), "24xlarge": (6, 3750)},
    "i8ge": {"large": (1, 1250), "xlarge": (1, 2500), "2xlarge": (2, 2500), "3xlarge": (1, 7500), "6xlarge": (2, 7500), "12xlarge": (4, 7500), "18xlarge": (6, 7500), "24xlarge": (8, 7500)},
    "im4gn": {"large": (1, 937), "xlarge": (1, 1875), "2xlarge": (1, 3750), "4xlarge": (1, 7500), "8xlarge": (2, 7500), "16xlarge": (4, 7500)},
    "is4gen": {"medium": (1, 937), "large": (1, 1875), "xlarge": (1, 3750), "2xlarge": (1, 7500), "4xlarge": (2, 7500), "8xlarge": (4, 7500)},
    "m5ad": {"large": (1, 75), "xlarge": (1, 150), "2xlarge": (1, 300), "4xlarge": (2, 300), "8xlarge": (2, 600), "12xlarge": (2, 900), "16xlarge": (4, 600), "24xlarge": (4, 900)},
    "m5d": {"large": (1, 75), "xlarge": (1, 150), "2xlarge": (1, 300), "4xlarge": (2, 300), "8xlarge": (2, 600), "12xlarge": (2, 900), "16xlarge": (4, 600), "24xlarge": (4, 900), "metal": (4, 900)},
    "m6gd": {"medium": (1, 59), "large": (1, 118), "xlarge": (1, 237), "2xlarge": (1, 474), "4xlarge": (1, 950), "8xlarge": (1, 1900), "12xlarge": (2, 1425), "16xlarge": (2, 1900), "metal": (2, 1900)},
    "m8gd": {"medium": (1, 59), "large": (1, 118), "xlarge": (1, 237), "2xlarge": (1, 474), "4xlarge": (1, 950), "8xlarge": (1, 1900), "12xlarge": (3, 950), "16xlarge": (2, 1900), "24xlarge": (3, 1900), "48xlarge": (6, 1900), "metal-24xl": (3, 1900), "metal-48xl": (6, 1900)},
    "m9gd": {"medium": (1, 59), "large": (1, 118), "xlarge": (1, 237), "2xlarge": (1, 474), "4xlarge": (1, 950), "8xlarge": (1, 1900), "12xlarge": (3, 950), "16xlarge": (1, 3800), "24xlarge": (3, 1900), "48xlarge": (3, 3800), "metal-48xl": (3, 3800)},
    "r5d": {"large": (1, 75), "xlarge": (1, 150), "2xlarge": (1, 300), "4xlarge": (2, 300), "8xlarge": (2, 600), "12xlarge": (2, 900), "16xlarge": (4, 600), "24xlarge": (4, 900), "metal": (4, 900)},
    "r6gd": {"medium": (1, 59), "large": (1, 118), "xlarge": (1, 237), "2xlarge": (1, 474), "4xlarge": (1, 950), "8xlarge": (1, 1900), "12xlarge": (2, 1425), "16xlarge": (2, 1900), "metal": (2, 1900)},
    "r8gd": {"medium": (1, 59), "large": (1, 118), "xlarge": (1, 237), "2xlarge": (1, 474), "4xlarge": (1, 950), "8xlarge": (1, 1900), "12xlarge": (3, 950), "16xlarge": (2, 1900), "24xlarge": (3, 1900), "48xlarge": (6, 1900), "metal-24xl": (3, 1900), "metal-48xl": (6, 1900)},
    "x2gd": {"medium": (1, 59), "large": (1, 118), "xlarge": (1, 237), "2xlarge": (1, 475), "4xlarge": (1, 950), "8xlarge": (1, 1900), "12xlarge": (2, 1425), "16xlarge": (2, 1900), "metal": (2, 1900)},
    "z1d": {"large": (1, 75), "xlarge": (1, 150), "2xlarge": (1, 300), "3xlarge": (1, 450), "6xlarge": (1, 900), "12xlarge": (2, 900), "metal": (2, 900)},
}
# fmt: on


@pytest.mark.unit
class TestAwsIoParamsPerDisk(TestCase):
    """aws_io_params.yaml entries are per local disk, since AwsIoSetup multiplies them by the disk count.

    i7i, i7ie, i8g and i8ge once had whole-instance entries for their multi-disk
    sizes, so e.g. i8ge.12xlarge got 4x its real bandwidth in io_properties.yaml.
    """

    io_params_path = Path(__file__).parent.parent / "common" / "aws_io_params.yaml"

    # Sizes built from the same drive differ by up to ~1.4x in bandwidth (im4gn), while a
    # whole-instance entry is at least 2x the per-disk one. IOPS vary too much to compare.
    max_bandwidth_spread = 1.75

    def io_params(self):
        with open(self.io_params_path) as f:
            return yaml.safe_load(f)

    def test_every_instance_type_has_its_disks_listed(self):
        for instance_type in self.io_params():
            family, size = instance_type.split(".")
            if size == "ALL":
                continue
            assert size in AWS_LOCAL_DISKS.get(family, {}), (
                f"{instance_type}: add its local disks to AWS_LOCAL_DISKS, "
                "and make sure its aws_io_params.yaml entry is per disk"
            )

    def test_sizes_with_the_same_drive_have_the_same_per_disk_bandwidth(self):
        io_params = self.io_params()
        for family, sizes in AWS_LOCAL_DISKS.items():
            by_drive = {}
            for size, (count, disk_gb) in sizes.items():
                by_drive.setdefault(disk_gb, []).append((f"{family}.{size}", count))
            for disk_gb, instance_types in by_drive.items():
                for key in ("read_bandwidth", "write_bandwidth"):
                    values = {f"{it} ({count} disks)": io_params[it][key] for it, count in instance_types}
                    assert max(values.values()) <= self.max_bandwidth_spread * min(values.values()), (
                        f"{family} sizes with {disk_gb} GB disks disagree on per-disk {key}, "
                        f"is one of them a whole-instance total? {values}"
                    )
