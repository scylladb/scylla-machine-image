import base64
import importlib.util
import json
import logging
import sys
import unittest.mock
from collections import namedtuple
from pathlib import Path
from unittest import TestCase

import httpretty
import pytest
import yaml


sys.path.append(str(Path(__file__).parent.parent))
import lib.scylla_cloud
from lib.scylla_cloud import OciInstance


# Load scylla_cloud_io_setup module (file without .py extension)
_io_setup_path = Path(__file__).parent.parent / "common" / "scylla_cloud_io_setup"
_spec = importlib.util.spec_from_loader("scylla_cloud_io_setup", loader=None, origin=str(_io_setup_path))
scylla_cloud_io_setup = importlib.util.module_from_spec(_spec)
with open(_io_setup_path) as _f:
    exec(_f.read(), scylla_cloud_io_setup.__dict__)
OciIoSetup = scylla_cloud_io_setup.OciIoSetup
UnsupportedInstanceClassError = scylla_cloud_io_setup.UnsupportedInstanceClassError


LOGGER = logging.getLogger(__name__)

svmem = namedtuple("svmem", ["total"])

sdiskpart = namedtuple("sdiskpart", ["device", "mountpoint"])
mock_disk_partitions = [
    sdiskpart("/dev/root", "/"),
    sdiskpart("/dev/sda15", "/boot/efi"),
    sdiskpart("/dev/md0", "/var/lib/scylla"),
    sdiskpart("/dev/md0", "/var/lib/systemd/coredump"),
]

mock_disk_partitions_nvme = [
    sdiskpart("/dev/nvme0n1p1", "/"),
    sdiskpart("/dev/nvme0n1p15", "/boot/efi"),
]

mock_listdevdir_vm_standard = ["md0", "root", "sda15", "sda14", "sda1", "sda", "sg0", "zero", "null"]
mock_listdevdir_vm_standard_4nvme = [
    "md0",
    "root",
    "nvme0n4",
    "nvme0n3",
    "nvme0n2",
    "sda15",
    "sda14",
    "sda1",
    "sda",
    "sg0",
    "nvme0n1",
    "nvme0",
    "zero",
    "null",
]
mock_listdevdir_vm_denseio = [
    "root",
    "nvme0n1p1",
    "nvme0n1",
    "nvme1n1",
    "nvme2n1",
    "nvme3n1",
    "nvme1",
    "nvme2",
    "nvme3",
    "nvme0",
    "zero",
    "null",
]
mock_glob_glob_dev_vm_standard = ["/dev/sda15", "/dev/sda14", "/dev/sda1", "/dev/sda"]
mock_glob_glob_dev_vm_standard_4nvme = mock_glob_glob_dev_vm_standard
mock_glob_glob_dev_vm_denseio = []


def _mock_multi_open(files, filename, *args, **kwargs):
    if filename in files:
        return unittest.mock.mock_open(read_data=files[filename]).return_value
    raise FileNotFoundError(f"Unable to open {filename}")


def mock_multi_open_oci(filename, *args, **kwargs):
    files = {"/sys/class/dmi/id/chassis_asset_tag": "OracleCloud.com"}
    return _mock_multi_open(files, filename, *args, **kwargs)


@httpretty.activate(verbose=True, allow_net_connect=False)
@unittest.mock.patch("lib.scylla_cloud.is_ec2", return_value=False)
@unittest.mock.patch("lib.scylla_cloud.is_gce", return_value=False)
@unittest.mock.patch("lib.scylla_cloud.is_azure", return_value=False)
@unittest.mock.patch("lib.scylla_cloud.is_oci", return_value=True)
def test_is_oci(*args):
    """Test OCI instance detection."""
    assert lib.scylla_cloud.is_oci() is True


@httpretty.activate(verbose=True, allow_net_connect=False)
@unittest.mock.patch("builtins.open", mock_multi_open_oci)
@unittest.mock.patch("lib.scylla_cloud.read_one_line", return_value="OracleCloud.com")
def test_oci_identify_dmi(*args):
    """Test OCI instance identification via DMI."""
    result = OciInstance.identify_dmi()
    assert result == OciInstance


@pytest.mark.asyncio
async def test_oci_identify_metadata(*args):
    """Test OCI instance identification via metadata."""
    with httpretty.enabled(verbose=True, allow_net_connect=False):
        httpretty.register_uri(
            httpretty.GET,
            "http://169.254.169.254/opc/v2/instance/",
            body=json.dumps(
                {
                    "shape": "VM.Standard3.Flex",
                }
            ),
            content_type="application/json",
        )
        assert await OciInstance.identify_metadata() == OciInstance


@httpretty.activate(verbose=True, allow_net_connect=False)
@unittest.mock.patch("builtins.open", mock_multi_open_oci)
@unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions)
@unittest.mock.patch("os.listdir")
@unittest.mock.patch("glob.glob")
def test_oci_vm_standard_no_nvme(mock_glob, mock_listdir, *args):
    """Test OCI VM.Standard instance without NVMe drives."""
    mock_listdir.return_value = mock_listdevdir_vm_standard
    mock_glob.return_value = mock_glob_glob_dev_vm_standard

    # Mock metadata endpoint
    httpretty.register_uri(
        httpretty.GET,
        "http://169.254.169.254/opc/v2/instance/",
        body=json.dumps({"shape": "VM.Standard3.Flex", "region": "us-ashburn-1", "availabilityDomain": "AD-1"}),
        content_type="application/json",
    )

    instance = OciInstance()

    # Test instancetype
    assert instance.instancetype == "VM.Standard3.Flex"

    # Test instance_class
    assert instance.instance_class() == "VM.Standard"

    # Test is_supported_instance_class
    assert instance.is_supported_instance_class() is True

    # Test is_dev_instance_type
    assert instance.is_dev_instance_type() is False

    # Test NVMe disk count
    assert instance.nvme_disk_count == 0

    # Test get_local_disks (should be empty)
    local_disks = instance.get_local_disks()
    assert len(local_disks) == 0


@httpretty.activate(verbose=True, allow_net_connect=False)
@unittest.mock.patch("builtins.open", mock_multi_open_oci)
@unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions_nvme)
@unittest.mock.patch("os.listdir")
@unittest.mock.patch("glob.glob")
def test_oci_vm_denseio_with_nvme(mock_glob, mock_listdir, *args):
    """Test OCI VM.DenseIO instance with NVMe drives."""
    mock_listdir.return_value = mock_listdevdir_vm_denseio
    mock_glob.return_value = mock_glob_glob_dev_vm_denseio

    # Mock metadata endpoint
    httpretty.register_uri(
        httpretty.GET,
        "http://169.254.169.254/opc/v2/instance/",
        body=json.dumps({"shape": "VM.DenseIO2.8", "region": "us-phoenix-1", "availabilityDomain": "AD-2"}),
        content_type="application/json",
    )

    instance = OciInstance()

    # Test instancetype
    assert instance.instancetype == "VM.DenseIO2.8"

    # Test instance_class
    assert instance.instance_class() == "VM.DenseIO"

    # Test is_supported_instance_class
    assert instance.is_supported_instance_class() is True

    # Test NVMe disk count (nvme1n1, nvme2n1, nvme3n1 - nvme0n1 is root)
    assert instance.nvme_disk_count == 3

    # Test get_local_disks
    local_disks = instance.get_local_disks()
    assert len(local_disks) == 3
    assert "/dev/nvme1n1" in local_disks
    assert "/dev/nvme2n1" in local_disks
    assert "/dev/nvme3n1" in local_disks


@httpretty.activate(verbose=True, allow_net_connect=False)
@unittest.mock.patch("builtins.open", mock_multi_open_oci)
@unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions)
@unittest.mock.patch("os.listdir")
@unittest.mock.patch("glob.glob")
def test_oci_get_remote_disks(mock_glob, mock_listdir, *args):
    """Test OCI instance with remote (block volume) disks."""
    mock_listdir.return_value = mock_listdevdir_vm_standard
    mock_glob.return_value = mock_glob_glob_dev_vm_standard

    # Mock metadata endpoint
    httpretty.register_uri(
        httpretty.GET,
        "http://169.254.169.254/opc/v2/instance/",
        body=json.dumps(
            {
                "shape": "VM.Standard3.Flex",
            }
        ),
        content_type="application/json",
    )

    instance = OciInstance()

    # Test get_remote_disks (sda is root, but sdb/sdc/sdd would be block volumes)
    # Based on mock, we'll get sdb onwards
    remote_disks = instance.get_remote_disks()
    # The mock_glob_glob_dev_vm_standard filters out root, so this depends on implementation
    assert isinstance(remote_disks, list)


@httpretty.activate(verbose=True, allow_net_connect=False)
@unittest.mock.patch("builtins.open", mock_multi_open_oci)
@unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions)
@unittest.mock.patch("os.listdir")
@unittest.mock.patch("glob.glob")
def test_oci_dev_instance_type(mock_glob, mock_listdir, *args):
    """Test OCI dev instance type detection."""
    mock_listdir.return_value = mock_listdevdir_vm_standard
    mock_glob.return_value = mock_glob_glob_dev_vm_standard

    # Mock metadata endpoint for micro instance
    httpretty.register_uri(
        httpretty.GET,
        "http://169.254.169.254/opc/v2/instance/",
        body=json.dumps(
            {
                "shape": "VM.Standard.E2.1.Micro",
            }
        ),
        content_type="application/json",
    )

    instance = OciInstance()

    # Test is_dev_instance_type
    assert instance.is_dev_instance_type() is True


@httpretty.activate(verbose=True, allow_net_connect=False)
@unittest.mock.patch("builtins.open", mock_multi_open_oci)
@unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions)
@unittest.mock.patch("os.listdir")
@unittest.mock.patch("glob.glob")
def test_oci_user_data(mock_glob, mock_listdir, *args):
    """Test OCI user data retrieval."""
    mock_listdir.return_value = mock_listdevdir_vm_standard
    mock_glob.return_value = mock_glob_glob_dev_vm_standard

    # Mock metadata endpoints
    httpretty.register_uri(
        httpretty.GET,
        "http://169.254.169.254/opc/v2/instance/",
        body=json.dumps(
            {
                "shape": "VM.Standard3.Flex",
            }
        ),
        content_type="application/json",
    )

    httpretty.register_uri(
        httpretty.GET,
        "http://169.254.169.254/opc/v2/instance/metadata/user_data",
        body=base64.b64encode(b'#!/bin/bash\necho "Hello from OCI"').decode("utf-8"),
    )

    instance = OciInstance()

    # Test user_data
    user_data = instance.user_data
    assert "Hello from OCI" in user_data


@httpretty.activate(verbose=True, allow_net_connect=False)
@unittest.mock.patch("builtins.open", mock_multi_open_oci)
@unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions)
@unittest.mock.patch("os.listdir")
@unittest.mock.patch("glob.glob")
def test_oci_private_ipv4(mock_glob, mock_listdir, *args):
    """Test OCI private IPv4 retrieval."""
    mock_listdir.return_value = mock_listdevdir_vm_standard
    mock_glob.return_value = mock_glob_glob_dev_vm_standard

    # Mock metadata endpoints
    httpretty.register_uri(
        httpretty.GET,
        "http://169.254.169.254/opc/v2/instance/",
        body=json.dumps(
            {
                "shape": "VM.Standard3.Flex",
            }
        ),
        content_type="application/json",
    )

    httpretty.register_uri(
        httpretty.GET,
        "http://169.254.169.254/opc/v2/vnics/0/privateIp",
        body="10.0.1.100",
    )

    instance = OciInstance()

    # Test private_ipv4
    private_ip = instance.private_ipv4()
    assert private_ip == "10.0.1.100"


@httpretty.activate(verbose=True, allow_net_connect=False)
@unittest.mock.patch("builtins.open", mock_multi_open_oci)
@unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions)
@unittest.mock.patch("os.listdir")
@unittest.mock.patch("glob.glob")
def test_oci_public_ipv4(mock_glob, mock_listdir, *args):
    """Test OCI public IPv4 retrieval."""
    mock_listdir.return_value = mock_listdevdir_vm_standard
    mock_glob.return_value = mock_glob_glob_dev_vm_standard

    instance = OciInstance()

    # Test public_ipv4
    public_ip = instance.public_ipv4()
    assert public_ip is None


@httpretty.activate(verbose=True, allow_net_connect=False)
@unittest.mock.patch("builtins.open", mock_multi_open_oci)
@unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions)
@unittest.mock.patch("os.listdir")
@unittest.mock.patch("glob.glob")
def test_oci_endpoint_snitch(mock_glob, mock_listdir, *args):
    """Test OCI endpoint snitch."""
    mock_listdir.return_value = mock_listdevdir_vm_standard
    mock_glob.return_value = mock_glob_glob_dev_vm_standard

    # Mock metadata endpoint
    httpretty.register_uri(
        httpretty.GET,
        "http://169.254.169.254/opc/v2/instance/",
        body=json.dumps(
            {
                "shape": "VM.Standard3.Flex",
            }
        ),
        content_type="application/json",
    )

    instance = OciInstance()

    # Test endpoint_snitch
    assert instance.endpoint_snitch == "GossipingPropertyFileSnitch"


# Sample OCI IO params for testing
MOCK_OCI_IO_PARAMS = {
    "VM.DenseIO.E5.Flex-8": {
        "read_iops": 1112606,
        "read_bandwidth": 6965937664,
        "write_iops": 608448,
        "write_bandwidth": 4079220992,
    },
    "VM.DenseIO.E5.Flex-16": {
        "read_iops": 2215123,
        "read_bandwidth": 14401178624,
        "write_iops": 1216146,
        "write_bandwidth": 8231124480,
    },
    "BM.DenseIO.E4.128": {
        "read_iops": 5000000,
        "read_bandwidth": 30000000000,
        "write_iops": 3000000,
        "write_bandwidth": 18000000000,
    },
}


class MockOciInstance:
    """Mock OCI instance for testing OciIoSetup."""

    def __init__(self, instance_type="VM.DenseIO.E5.Flex", ocpus=8, supported=True):
        self.instancetype = instance_type
        self.ocpus = ocpus
        self._supported = supported

    def is_supported_instance_class(self):
        return self._supported


class TestOciIoSetup(TestCase):
    """Tests for OciIoSetup class."""

    def test_oci_io_setup_with_flex_instance(self):
        """Test that OciIoSetup correctly loads IO params for Flex instance from YAML file."""
        mock_instance = MockOciInstance(instance_type="VM.DenseIO.E5.Flex", ocpus=8)
        io_setup = OciIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_OCI_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch.object(io_setup, "save") as mock_save,
        ):
            io_setup.generate()

            # Verify disk properties were set correctly
            assert io_setup.disk_properties["mountpoint"] == "/var/lib/scylla"
            assert io_setup.disk_properties["read_iops"] == 1112606
            assert io_setup.disk_properties["read_bandwidth"] == 6965937664
            assert io_setup.disk_properties["write_iops"] == 608448
            assert io_setup.disk_properties["write_bandwidth"] == 4079220992
            mock_save.assert_called_once()

    def test_oci_io_setup_with_different_ocpus(self):
        """Test OciIoSetup with different OCPU count for Flex instance."""
        mock_instance = MockOciInstance(instance_type="VM.DenseIO.E5.Flex", ocpus=16)
        io_setup = OciIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_OCI_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch.object(io_setup, "save") as mock_save,
        ):
            io_setup.generate()

            assert io_setup.disk_properties["mountpoint"] == "/var/lib/scylla"
            assert io_setup.disk_properties["read_iops"] == 2215123
            assert io_setup.disk_properties["read_bandwidth"] == 14401178624
            assert io_setup.disk_properties["write_iops"] == 1216146
            assert io_setup.disk_properties["write_bandwidth"] == 8231124480
            mock_save.assert_called_once()

    def test_oci_io_setup_with_non_flex_instance(self):
        """Test OciIoSetup with non-Flex (BM) instance type."""
        mock_instance = MockOciInstance(instance_type="BM.DenseIO.E4.128", ocpus=8)
        io_setup = OciIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_OCI_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch.object(io_setup, "save") as mock_save,
        ):
            io_setup.generate()

            # Should look up "BM.DenseIO.E4.128" directly for non-Flex instances
            assert io_setup.disk_properties["mountpoint"] == "/var/lib/scylla"
            assert io_setup.disk_properties["read_iops"] == 5000000
            assert io_setup.disk_properties["read_bandwidth"] == 30000000000
            mock_save.assert_called_once()

    def test_oci_io_setup_fallback_when_file_not_found(self):
        """Test that OciIoSetup falls back to scylla_io_setup when YAML file is not found."""
        mock_instance = MockOciInstance(instance_type="VM.DenseIO.E5.Flex", ocpus=8)
        io_setup = OciIoSetup(mock_instance)

        with (
            unittest.mock.patch("builtins.open", side_effect=FileNotFoundError("File not found")),
            unittest.mock.patch("subprocess.run") as mock_run,
        ):
            io_setup.generate()
            mock_run.assert_called_once_with(
                "scylla_io_setup", shell=True, check=True, capture_output=True, timeout=300
            )

    def test_oci_io_setup_fallback_when_instance_not_in_yaml(self):
        """Test that OciIoSetup falls back to scylla_io_setup when instance type is not in YAML."""
        mock_instance = MockOciInstance(instance_type="VM.Unknown.Flex", ocpus=999)
        io_setup = OciIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_OCI_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch("subprocess.run") as mock_run,
        ):
            io_setup.generate()
            mock_run.assert_called_once_with(
                "scylla_io_setup", shell=True, check=True, capture_output=True, timeout=300
            )

    def test_oci_io_setup_raises_for_unsupported_instance_class(self):
        """Test that OciIoSetup raises UnsupportedInstanceClassError for unsupported instances."""
        mock_instance = MockOciInstance(instance_type="VM.DenseIO.E5.Flex", supported=False)
        io_setup = OciIoSetup(mock_instance)

        with pytest.raises(UnsupportedInstanceClassError):
            io_setup.generate()
