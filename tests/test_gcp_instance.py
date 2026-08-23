import importlib.util
import json
import logging
import sys
import tempfile
import unittest.mock
from collections import namedtuple
from pathlib import Path
from socket import AddressFamily, SocketKind
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase

import httpretty
import pytest
import yaml


sys.path.append(str(Path(__file__).parent.parent))
import lib.scylla_cloud
from lib.scylla_cloud import GcpInstance


# Load scylla_cloud_io_setup module (file without .py extension)
_io_setup_path = Path(__file__).parent.parent / "common" / "scylla_cloud_io_setup"
_spec = importlib.util.spec_from_loader("scylla_cloud_io_setup", loader=None, origin=str(_io_setup_path))
scylla_cloud_io_setup = importlib.util.module_from_spec(_spec)
with open(_io_setup_path) as _f:
    exec(_f.read(), scylla_cloud_io_setup.__dict__)
GcpIoSetup = scylla_cloud_io_setup.GcpIoSetup
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

mock_listdevdir_n2_standard_8 = ["md0", "root", "sda15", "sda14", "sda1", "sda", "sg0", "zero", "null"]
mock_listdevdir_n2_standard_8_4ssd = [
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
mock_listdevdir_n2_highcpu_8_4ssd = mock_listdevdir_n2_standard_8_4ssd
mock_listdevdir_n2_standard_8_24ssd = [
    "md0",
    "root",
    "nvme0n24",
    "nvme0n23",
    "nvme0n22",
    "nvme0n21",
    "nvme0n20",
    "nvme0n19",
    "nvme0n18",
    "nvme0n17",
    "nvme0n16",
    "nvme0n15",
    "nvme0n14",
    "nvme0n13",
    "nvme0n12",
    "nvme0n11",
    "nvme0n10",
    "nvme0n9",
    "nvme0n8",
    "nvme0n7",
    "nvme0n6",
    "nvme0n5",
    "nvme0n4",
    "nvme0n3",
    "nvme0n2",
    "nvme0n1",
    "sda15",
    "sda14",
    "sda1",
    "sda",
    "sg0",
    "nvme0",
    "zero",
    "null",
]
mock_listdevdir_n2_standard_8_4ssd_2persistent = [
    "sdc",
    "sg2",
    "sdb",
    "sg1",
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
mock_glob_glob_dev_n2_standard_8 = ["/dev/sda15", "/dev/sda14", "/dev/sda1", "/dev/sda"]
mock_glob_glob_dev_n2_standard_8_4ssd = mock_glob_glob_dev_n2_standard_8
mock_glob_glob_dev_n2_standard_8_24ssd = mock_glob_glob_dev_n2_standard_8
mock_glob_glob_dev_n2_highcpu_8_4ssd = mock_glob_glob_dev_n2_standard_8
mock_glob_glob_dev_n2_standard_8_4ssd_2persistent = [
    "/dev/sdc",
    "/dev/sdb",
    "/dev/sda15",
    "/dev/sda14",
    "/dev/sda1",
    "/dev/sda",
]


def _mock_multi_open(files, filename, *args, **kwargs):
    if filename in files:
        return unittest.mock.mock_open(read_data=files[filename]).return_value
    raise FileNotFoundError(f"Unable to open {filename}")


def mock_multi_open_n2(filename, *args, **kwargs):
    files = {"/sys/class/dmi/id/product_name": "Google Compute Engine"}
    return _mock_multi_open(files, filename, *args, **kwargs)


class GcpMetadata:
    def httpretty_gcp_metadata(
        self,
        instance_type="n2-standard-8",
        project_number="431729375847",
        instance_name="testcase_1",
        num_local_disks=4,
        num_remote_disks=0,
        with_userdata=False,
        userdata='{"scylla_yaml": {"cluster_name": "test-cluster"}}',
        mtu=1460,
    ):
        httpretty.register_uri(
            httpretty.GET,
            "http://metadata.google.internal/computeMetadata/v1/instance/machine-type?recursive=false",
            f"projects/{project_number}/machineTypes/{instance_type}",
        )
        httpretty.register_uri(
            httpretty.GET,
            "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/ip?recursive=false",
            "172.16.0.1",
        )
        httpretty.register_uri(
            httpretty.GET,
            "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/mtu?recursive=false",
            str(mtu),
        )
        disks = []
        i = 0
        disks.append(
            {
                "deviceName": instance_name,
                "index": i,
                "interface": "SCSI",
                "mode": "READ_WRITE",
                "type": "PERSISTENT-BALANCED",
            }
        )
        i += 1
        for j in range(num_local_disks):
            disks.append(
                {
                    "deviceName": f"local-ssd-{j}",
                    "index": i,
                    "interface": "NVME",
                    "mode": "READ_WRITE",
                    "type": "LOCAL-SSD",
                }
            )
            i += 1
        for j in range(num_remote_disks):
            disks.append(
                {
                    "deviceName": f"disk-{j}",
                    "index": i,
                    "interface": "SCSI",
                    "mode": "READ_WRITE",
                    "type": "PERSISTENT-BALANCED",
                }
            )
            i += 1
        httpretty.register_uri(
            httpretty.GET,
            "http://metadata.google.internal/computeMetadata/v1/instance/disks?recursive=true",
            json.dumps(disks),
        )
        if not with_userdata:
            httpretty.register_uri(
                httpretty.GET,
                "http://metadata.google.internal/computeMetadata/v1/instance/attributes/user-data?recursive=false",
                status=404,
            )
        else:
            httpretty.register_uri(
                httpretty.GET,
                "http://metadata.google.internal/computeMetadata/v1/instance/attributes/user-data?recursive=false",
                userdata,
            )


class TestAsyncGcpInstance(IsolatedAsyncioTestCase, GcpMetadata):
    def setUp(self):
        httpretty.enable(verbose=True, allow_net_connect=False)

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    async def test_identify_metadata(self):
        self.httpretty_gcp_metadata()
        with unittest.mock.patch(
            "socket.getaddrinfo",
            return_value=[(AddressFamily.AF_INET, SocketKind.SOCK_STREAM, 6, "", ("169.254.169.254", 80))],
        ):
            assert await GcpInstance.identify_metadata()

    async def test_not_identify_metadata(self):
        assert not await GcpInstance.identify_metadata()


class TestGcpInstance(TestCase, GcpMetadata):
    def setUp(self):
        httpretty.enable(verbose=True, allow_net_connect=False)

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    def test_identify_dmi(self):
        with unittest.mock.patch("builtins.open", unittest.mock.MagicMock(side_effect=mock_multi_open_n2)):
            assert GcpInstance.identify_dmi()

    def test_endpoint_snitch(self):
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        assert ins.endpoint_snitch == "GoogleCloudSnitch"

    def test_instancetype_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        assert ins.instancetype == "n2-standard-8"

    def test_instancetype_n2d_highmem_4(self):
        self.httpretty_gcp_metadata(instance_type="n2d-highmem-4")
        ins = GcpInstance()
        assert ins.instancetype == "n2d-highmem-4"

    def test_instancetype_e2_micro(self):
        self.httpretty_gcp_metadata(instance_type="e2-micro")
        ins = GcpInstance()
        assert ins.instancetype == "e2-micro"

    def test_cpu_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        with unittest.mock.patch("psutil.cpu_count", return_value=8):
            assert ins.cpu == 8

    def test_cpu_n2d_highmem_4(self):
        self.httpretty_gcp_metadata(instance_type="n2d-highmem-4")
        ins = GcpInstance()
        with unittest.mock.patch("psutil.cpu_count", return_value=4):
            assert ins.cpu == 4

    def test_memoryGB_n2_standard_8(self):  # noqa: N802
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        # XXX: the value is little bit less than 32GB
        with unittest.mock.patch("psutil.virtual_memory", return_value=svmem(33663647744)):
            assert ins.memory_gb > 31
            assert ins.memory_gb <= 32

    def test_memoryGB_n2d_highmem_4(self):  # noqa: N802
        self.httpretty_gcp_metadata(instance_type="n2d-highmem-4")
        ins = GcpInstance()
        # XXX: the value is little bit less than 32GB
        with unittest.mock.patch("psutil.virtual_memory", return_value=svmem(33664700416)):
            assert ins.memory_gb > 31
            assert ins.memory_gb <= 32

    def test_memoryGB_n1_standard_1(self):  # noqa: N802
        self.httpretty_gcp_metadata(instance_type="n1-standard-1")
        ins = GcpInstance()
        # XXX: the value is little bit less than 3.75GB
        with unittest.mock.patch("psutil.virtual_memory", return_value=svmem(3850301440)):
            assert ins.memory_gb > 3
            assert ins.memory_gb < 4

    def test_instance_size_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        assert ins.instance_size() == "8"

    def test_instance_size_n2d_highmem_4(self):
        self.httpretty_gcp_metadata(instance_type="n2d-highmem-4")
        ins = GcpInstance()
        assert ins.instance_size() == "4"

    def test_instance_size_e2_micro(self):
        self.httpretty_gcp_metadata(instance_type="e2-micro")
        ins = GcpInstance()
        assert not ins.instance_size()

    def test_instance_class_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        assert ins.instance_class() == "n2"

    def test_instance_class_n2d_highmem_4(self):
        self.httpretty_gcp_metadata(instance_type="n2d-highmem-4")
        ins = GcpInstance()
        assert ins.instance_class() == "n2d"

    def test_instance_class_e2_micro(self):
        self.httpretty_gcp_metadata(instance_type="e2-micro")
        ins = GcpInstance()
        assert ins.instance_class() == "e2"

    def test_instance_purpose_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        assert ins.instance_purpose() == "standard"

    def test_instance_purpose_n2d_highmem_4(self):
        self.httpretty_gcp_metadata(instance_type="n2d-highmem-4")
        ins = GcpInstance()
        assert ins.instance_purpose() == "highmem"

    def test_instance_purpose_e2_micro(self):
        self.httpretty_gcp_metadata(instance_type="e2-micro")
        ins = GcpInstance()
        assert ins.instance_purpose() == "micro"

    def test_is_not_unsupported_instance_class_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        assert not ins.is_unsupported_instance_class()

    def test_is_not_unsupported_instance_class_n2d_highmem_4(self):
        self.httpretty_gcp_metadata(instance_type="n2d-highmem-4")
        ins = GcpInstance()
        assert not ins.is_unsupported_instance_class()

    def test_is_unsupported_instance_class_e2_micro(self):
        self.httpretty_gcp_metadata(instance_type="e2-micro")
        ins = GcpInstance()
        assert ins.is_unsupported_instance_class()

    def test_is_not_unsupported_instance_class_m1_megamem_96(self):
        self.httpretty_gcp_metadata(instance_type="m1-megamem-96")
        ins = GcpInstance()
        assert not ins.is_unsupported_instance_class()

    def test_is_supported_instance_class_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        assert ins.is_supported_instance_class()

    def test_is_supported_instance_class_n2d_highmem_4(self):
        self.httpretty_gcp_metadata(instance_type="n2d-highmem-4")
        ins = GcpInstance()
        assert ins.is_supported_instance_class()

    def test_is_not_supported_instance_class_e2_micro(self):
        self.httpretty_gcp_metadata(instance_type="e2-micro")
        ins = GcpInstance()
        assert not ins.is_supported_instance_class()

    def test_is_supported_instance_class_m1_megamem_96(self):
        self.httpretty_gcp_metadata(instance_type="m1-megamem-96")
        ins = GcpInstance()
        assert ins.is_supported_instance_class()

    def test_is_supported_instance_class_z3_highmem_8_highlssd(self):
        self.httpretty_gcp_metadata(instance_type="z3-highmem-8-highlssd")
        ins = GcpInstance()
        assert ins.is_supported_instance_class()

    def test_is_supported_instance_class_z3_highmem_88_standardlssd(self):
        self.httpretty_gcp_metadata(instance_type="z3-highmem-88-standardlssd")
        ins = GcpInstance()
        assert ins.is_supported_instance_class()

    def test_is_recommended_instance_size_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        assert ins.is_recommended_instance_size()

    def test_is_not_recommended_instance_size_n1_standard_1(self):
        self.httpretty_gcp_metadata(instance_type="n1-standard-1")
        ins = GcpInstance()
        assert not ins.is_recommended_instance_size()

    # Unsupported class, but recommended size
    def test_is_recommended_instance_size_e2_standard_8(self):
        self.httpretty_gcp_metadata(instance_type="e2-standard-8")
        ins = GcpInstance()
        assert ins.is_recommended_instance_size()

    def test_private_ipv4(self):
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        assert ins.private_ipv4() == "172.16.0.1"

    def test_network_mtu_default(self):
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        assert ins.network_mtu == 1460

    def test_network_mtu_jumbo(self):
        self.httpretty_gcp_metadata(mtu=8896)
        ins = GcpInstance()
        assert ins.network_mtu == 8896

    def _net_params_file(self, netinfo):
        """Point GCP_NET_PARAMS_PATH at a temporary gcp_net_params.json."""
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = Path(tmpdir.name) / "gcp_net_params.json"
        path.write_text(json.dumps(netinfo))
        return unittest.mock.patch("lib.scylla_cloud.GCP_NET_PARAMS_PATH", path)

    def test_is_tier1_networking_delegates_to_detection(self):
        """A known instance type passes its bandwidth columns to detect_gcp_tier1."""
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        with (
            self._net_params_file([["n2-standard-8", 32.0, 50.0]]),
            unittest.mock.patch("lib.param_estimation.detect_gcp_tier1", return_value=True) as mock_detect,
        ):
            assert ins.is_tier1_networking is True
        mock_detect.assert_called_once_with(32.0, 50.0, None)

    def test_is_tier1_networking_none_for_unknown_instance_type(self):
        """An instance type missing from the table is inconclusive, not 'standard'."""
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        with self._net_params_file([["some-other-type", 10.0, None]]):
            assert ins.is_tier1_networking is None

    def test_is_tier1_networking_none_when_data_file_missing(self):
        """A missing data file is inconclusive, so callers leave the MTU alone."""
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        with unittest.mock.patch("lib.scylla_cloud.GCP_NET_PARAMS_PATH", Path("/nonexistent/gcp_net_params.json")):
            assert ins.is_tier1_networking is None

    def test_is_tier1_networking_none_when_data_file_corrupt(self):
        """Corrupt JSON is inconclusive rather than an exception or 'standard'."""
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = Path(tmpdir.name) / "gcp_net_params.json"
        path.write_text("{ not json")
        with unittest.mock.patch("lib.scylla_cloud.GCP_NET_PARAMS_PATH", path):
            assert ins.is_tier1_networking is None

    def test_is_tier1_networking_passes_user_data_override(self):
        """tier1_networking from user-data reaches the detection helper."""
        self.httpretty_gcp_metadata(with_userdata=True, userdata=yaml.dump({"tier1_networking": True}))
        ins = GcpInstance()
        with (
            self._net_params_file([["n2-standard-8", 32.0, 50.0]]),
            unittest.mock.patch("lib.param_estimation.detect_gcp_tier1", return_value=True) as mock_detect,
        ):
            assert ins.is_tier1_networking is True
        mock_detect.assert_called_once_with(32.0, 50.0, True)

    def test_tier1_override_none_when_user_data_is_not_a_mapping(self):
        """Valid YAML that isn't a mapping must not raise out of tier1_override."""
        self.httpretty_gcp_metadata(with_userdata=True, userdata="just a string")
        ins = GcpInstance()
        assert ins.tier1_override is None

    def test_tier1_override_honours_assigned_value(self):
        """An override assigned later (scylla_configure does this) wins and skips user-data."""
        self.httpretty_gcp_metadata(with_userdata=True, userdata=yaml.dump({"tier1_networking": False}))
        ins = GcpInstance()
        ins._tier1_override = True
        assert ins.tier1_override is True

    def test_check_prints_recorded_warning(self):
        """The banner prints whatever scylla_image_setup recorded at boot."""
        ins = GcpInstance()
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = Path(tmpdir.name) / "gcp_mtu_warning"
        path.write_text("VPC MTU is 1460; jumbo frames are not enabled. Set it to 8896.\n")
        with (
            unittest.mock.patch("lib.scylla_cloud.GCP_MTU_WARNING_PATH", path),
            unittest.mock.patch("builtins.print") as mock_print,
        ):
            ins.check()
        mock_print.assert_called_once()
        output = mock_print.call_args[0][0]
        assert "1460" in output
        assert "8896" in output

    def test_check_silent_when_nothing_recorded(self):
        """No recorded warning means a silent banner — and no metadata lookups."""
        ins = GcpInstance()
        with (
            unittest.mock.patch("lib.scylla_cloud.GCP_MTU_WARNING_PATH", Path("/nonexistent/gcp_mtu_warning")),
            unittest.mock.patch("builtins.print") as mock_print,
        ):
            ins.check()
        mock_print.assert_not_called()

    def test_user_data(self):
        self.httpretty_gcp_metadata(with_userdata=True)
        ins = GcpInstance()
        assert ins.user_data == '{"scylla_yaml": {"cluster_name": "test-cluster"}}'

    def test_no_user_data(self):
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        real_curl = lib.scylla_cloud.curl

        def mocked_curl(*args, **kwargs):
            kwargs["timeout"] = 0.001
            kwargs["retry_interval"] = 0.0001
            return real_curl(*args, **kwargs)

        with unittest.mock.patch("lib.scylla_cloud.curl", new=mocked_curl):
            assert ins.user_data == ""

    def test_non_root_nvmes_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_n2_standard_8_4ssd),
        ):
            ins = GcpInstance()
            assert ins._non_root_nvmes() == {
                "root": ["/dev/root"],
                "ephemeral": ["nvme0n4", "nvme0n3", "nvme0n2", "nvme0n1"],
            }

    def test_non_root_nvmes_n2_standard_8_4ssd_2persistent(self):
        self.httpretty_gcp_metadata(num_remote_disks=2)
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_n2_standard_8_4ssd_2persistent),
        ):
            ins = GcpInstance()
            assert ins._non_root_nvmes() == {
                "root": ["/dev/root"],
                "ephemeral": ["nvme0n4", "nvme0n3", "nvme0n2", "nvme0n1"],
            }

    def test_non_root_disks_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_n2_standard_8_4ssd),
        ):
            ins = GcpInstance()
            assert ins._non_root_disks() == {"persistent": []}

    def test_non_root_disks_n2_standard_8_4ssd_2persistent(self):
        self.httpretty_gcp_metadata(num_remote_disks=2)
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_n2_standard_8_4ssd_2persistent),
        ):
            ins = GcpInstance()
            assert ins._non_root_disks() == {"persistent": ["sdc", "sdb"]}

    def test_os_disks_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_n2_standard_8_4ssd),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_n2_standard_8_4ssd),
        ):
            ins = GcpInstance()
            assert ins.os_disks == {
                "root": ["/dev/root"],
                "ephemeral": ["nvme0n4", "nvme0n3", "nvme0n2", "nvme0n1"],
                "persistent": [],
            }

    def test_os_disks_n2_standard_8_4ssd_2persistent(self):
        self.httpretty_gcp_metadata(num_remote_disks=2)
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_n2_standard_8_4ssd_2persistent),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_n2_standard_8_4ssd_2persistent),
        ):
            ins = GcpInstance()
            assert ins.os_disks == {
                "root": ["/dev/root"],
                "ephemeral": ["nvme0n4", "nvme0n3", "nvme0n2", "nvme0n1"],
                "persistent": ["sdc", "sdb"],
            }

    def test_get_local_disks_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_n2_standard_8_4ssd),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_n2_standard_8_4ssd),
        ):
            ins = GcpInstance()
            assert ins.get_local_disks() == ["nvme0n4", "nvme0n3", "nvme0n2", "nvme0n1"]

    def test_get_local_disks_n2_standard_8_4ssd_2persistent(self):
        self.httpretty_gcp_metadata(num_remote_disks=2)
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_n2_standard_8_4ssd_2persistent),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_n2_standard_8_4ssd_2persistent),
        ):
            ins = GcpInstance()
            assert ins.get_local_disks() == ["nvme0n4", "nvme0n3", "nvme0n2", "nvme0n1"]

    def test_get_remote_disks_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_n2_standard_8_4ssd),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_n2_standard_8_4ssd),
        ):
            ins = GcpInstance()
            assert ins.get_remote_disks() == []

    def test_get_remote_disks_n2_standard_8_4ssd_2persistent(self):
        self.httpretty_gcp_metadata(num_remote_disks=2)
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_n2_standard_8_4ssd_2persistent),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_n2_standard_8_4ssd_2persistent),
        ):
            ins = GcpInstance()
            assert ins.get_remote_disks() == ["sdc", "sdb"]

    def test_get_nvme_disks_from_metadata_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        ins = GcpInstance()
        assert ins._GcpInstance__get_nvme_disks_from_metadata() == [
            {"deviceName": "local-ssd-0", "index": 1, "interface": "NVME", "mode": "READ_WRITE", "type": "LOCAL-SSD"},
            {"deviceName": "local-ssd-1", "index": 2, "interface": "NVME", "mode": "READ_WRITE", "type": "LOCAL-SSD"},
            {"deviceName": "local-ssd-2", "index": 3, "interface": "NVME", "mode": "READ_WRITE", "type": "LOCAL-SSD"},
            {"deviceName": "local-ssd-3", "index": 4, "interface": "NVME", "mode": "READ_WRITE", "type": "LOCAL-SSD"},
        ]

    def test_get_nvme_disks_from_metadata_n2_standard_8_4ssd_2persistent(self):
        self.httpretty_gcp_metadata(num_remote_disks=2)
        ins = GcpInstance()
        assert ins._GcpInstance__get_nvme_disks_from_metadata() == [
            {"deviceName": "local-ssd-0", "index": 1, "interface": "NVME", "mode": "READ_WRITE", "type": "LOCAL-SSD"},
            {"deviceName": "local-ssd-1", "index": 2, "interface": "NVME", "mode": "READ_WRITE", "type": "LOCAL-SSD"},
            {"deviceName": "local-ssd-2", "index": 3, "interface": "NVME", "mode": "READ_WRITE", "type": "LOCAL-SSD"},
            {"deviceName": "local-ssd-3", "index": 4, "interface": "NVME", "mode": "READ_WRITE", "type": "LOCAL-SSD"},
        ]

    def test_nvme_disk_count_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_n2_standard_8_4ssd),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_n2_standard_8_4ssd),
        ):
            ins = GcpInstance()
            assert ins.nvme_disk_count == 4

    def test_nvme_disk_count_n2_standard_8_4ssd_2persistent(self):
        self.httpretty_gcp_metadata(num_remote_disks=2)
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_n2_standard_8_4ssd_2persistent),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_n2_standard_8_4ssd_2persistent),
        ):
            ins = GcpInstance()
            assert ins.nvme_disk_count == 4

    def test_firstNvmeSize_n2_standard_8_4ssd(self):  # noqa: N802
        self.httpretty_gcp_metadata()
        with (
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_n2_standard_8_4ssd),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_n2_standard_8_4ssd),
            unittest.mock.patch("lib.scylla_cloud.GcpInstance.get_file_size_by_seek", return_value=402653184000),
        ):
            ins = GcpInstance()
            assert ins.first_nvme_size == 375.0

    def test_is_recommended_instance_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with (
            unittest.mock.patch("psutil.cpu_count", return_value=8),
            unittest.mock.patch("psutil.virtual_memory", return_value=svmem(33663647744)),
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_n2_standard_8_4ssd),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_n2_standard_8_4ssd),
            unittest.mock.patch("lib.scylla_cloud.GcpInstance.get_file_size_by_seek", return_value=402653184000),
        ):
            ins = GcpInstance()
            assert ins.is_recommended_instance()

    def test_is_not_recommended_instance_n2_highcpu_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with (
            unittest.mock.patch("psutil.cpu_count", return_value=8),
            unittest.mock.patch("psutil.virtual_memory", return_value=svmem(8334258176)),
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_n2_highcpu_8_4ssd),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_n2_highcpu_8_4ssd),
            unittest.mock.patch("lib.scylla_cloud.GcpInstance.get_file_size_by_seek", return_value=402653184000),
        ):
            ins = GcpInstance()
            # Not enough memory
            assert not ins.is_recommended_instance()

    def test_is_not_recommended_instance_n2_standard_8_24ssd(self):
        self.httpretty_gcp_metadata(num_local_disks=24)
        with (
            unittest.mock.patch("psutil.cpu_count", return_value=8),
            unittest.mock.patch("psutil.virtual_memory", return_value=svmem(33663647744)),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_n2_standard_8_24ssd),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_n2_standard_8_24ssd),
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("lib.scylla_cloud.GcpInstance.get_file_size_by_seek", return_value=402653184000),
        ):
            ins = GcpInstance()
            # Requires more CPUs to use this number of SSDs
            assert not ins.is_recommended_instance()

    def test_is_not_recommended_instance_n2_standard_8(self):
        self.httpretty_gcp_metadata(num_local_disks=0)
        with (
            unittest.mock.patch("psutil.cpu_count", return_value=8),
            unittest.mock.patch("psutil.virtual_memory", return_value=svmem(33663647744)),
            unittest.mock.patch("psutil.disk_partitions", return_value=mock_disk_partitions),
            unittest.mock.patch("os.listdir", return_value=mock_listdevdir_n2_standard_8),
            unittest.mock.patch("glob.glob", return_value=mock_glob_glob_dev_n2_standard_8),
            unittest.mock.patch("lib.scylla_cloud.GcpInstance.get_file_size_by_seek", return_value=402653184000),
        ):
            ins = GcpInstance()
            # No SSD
            assert not ins.is_recommended_instance()


# Sample GCP IO params for testing
MOCK_GCP_IO_PARAMS = {
    "z3-highmem-8-highlssd": {
        "read_iops": 750000,
        "read_bandwidth": 3221225472,
        "write_iops": 500000,
        "write_bandwidth": 2684354560,
    },
    "z3-highmem-16-highlssd": {
        "read_iops": 1500000,
        "read_bandwidth": 6442450944,
        "write_iops": 1000000,
        "write_bandwidth": 5368709120,
    },
    "local_ssd_nvme": {
        2: {
            "read_iops": 340000,
            "read_bandwidth": 1384120320,
            "write_iops": 180000,
            "write_bandwidth": 734003200,
        },
        4: {
            "read_iops": 680000,
            "read_bandwidth": 2778726400,
            "write_iops": 360000,
            "write_bandwidth": 1468006400,
        },
        16: {
            "read_iops": 1600000,
            "read_bandwidth": 6543114240,
            "write_iops": 800000,
            "write_bandwidth": 3271557120,
        },
        24: {
            "read_iops": 2400000,
            "read_bandwidth": 9814671360,
            "write_iops": 1200000,
            "write_bandwidth": 4907335680,
        },
    },
}


class MockGcpInstance:
    """Mock GCP instance for testing GcpIoSetup."""

    def __init__(self, instance_type="z3-highmem-8-highlssd", nvme_disk_count=4, supported=True, cpu=32):
        self.instancetype = instance_type
        self.nvme_disk_count = nvme_disk_count
        self.cpu = cpu
        self._supported = supported

    def is_supported_instance_class(self):
        return self._supported

    def instance_class(self):
        return self.instancetype.split("-")[0]


class TestGcpIoSetup(TestCase):
    """Tests for GcpIoSetup class."""

    def test_gcp_io_setup_with_known_instance_type(self):
        """Test that GcpIoSetup correctly loads IO params from YAML file."""
        mock_instance = MockGcpInstance(instance_type="z3-highmem-8-highlssd", nvme_disk_count=4)
        io_setup = GcpIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_GCP_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch.object(io_setup, "save") as mock_save,
        ):
            io_setup.generate()

            # Verify disk properties were set correctly
            assert io_setup.disk_properties["mountpoint"] == "/var/lib/scylla"
            assert io_setup.disk_properties["read_iops"] == 750000
            assert io_setup.disk_properties["read_bandwidth"] == 3221225472
            assert io_setup.disk_properties["write_iops"] == 500000
            assert io_setup.disk_properties["write_bandwidth"] == 2684354560
            mock_save.assert_called_once()

    def test_gcp_io_setup_with_different_instance_type(self):
        """Test GcpIoSetup with z3-highmem-16-highlssd instance type."""
        mock_instance = MockGcpInstance(instance_type="z3-highmem-16-highlssd", nvme_disk_count=8)
        io_setup = GcpIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_GCP_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch.object(io_setup, "save") as mock_save,
        ):
            io_setup.generate()

            assert io_setup.disk_properties["mountpoint"] == "/var/lib/scylla"
            assert io_setup.disk_properties["read_iops"] == 1500000
            assert io_setup.disk_properties["read_bandwidth"] == 6442450944
            assert io_setup.disk_properties["write_iops"] == 1000000
            assert io_setup.disk_properties["write_bandwidth"] == 5368709120
            mock_save.assert_called_once()

    def test_gcp_io_setup_fallback_to_disk_count_logic(self):
        """Test GcpIoSetup falls back to the per-disk-count Google limits when instance not in YAML."""
        mock_instance = MockGcpInstance(instance_type="n2-standard-32", nvme_disk_count=2, cpu=32)
        io_setup = GcpIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_GCP_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch.object(io_setup, "save") as mock_save,
        ):
            io_setup.generate()

            assert io_setup.disk_properties["read_iops"] == 340000
            assert io_setup.disk_properties["read_bandwidth"] == 1384120320
            assert io_setup.disk_properties["write_iops"] == 180000
            assert io_setup.disk_properties["write_bandwidth"] == 734003200
            mock_save.assert_called_once()

    def test_gcp_io_setup_uses_google_limits(self):
        """Google's published limits are used for the 16 and 24 local SSD configurations."""
        expected = {
            16: (1600000, 6543114240, 800000, 3271557120),
            24: (2400000, 9814671360, 1200000, 4907335680),
        }
        mock_yaml_content = yaml.dump(MOCK_GCP_IO_PARAMS)

        for nr_disks, (read_iops, read_bandwidth, write_iops, write_bandwidth) in expected.items():
            with self.subTest(nr_disks=nr_disks):
                mock_instance = MockGcpInstance(instance_type="n2-standard-32", nvme_disk_count=nr_disks, cpu=32)
                io_setup = GcpIoSetup(mock_instance)

                with (
                    unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
                    unittest.mock.patch.object(io_setup, "save") as mock_save,
                ):
                    io_setup.generate()

                    assert io_setup.disk_properties["read_iops"] == read_iops
                    assert io_setup.disk_properties["read_bandwidth"] == read_bandwidth
                    assert io_setup.disk_properties["write_iops"] == write_iops
                    assert io_setup.disk_properties["write_bandwidth"] == write_bandwidth
                    mock_save.assert_called_once()

    def test_gcp_io_setup_runs_iotune_when_not_enough_cpus(self):
        """Instances with too few vCPUs cannot reach Google's limits, so iotune measures them."""
        # n2 needs at least 24 vCPUs, n1 needs at least 32
        for instance_type, cpu in [("n2-standard-16", 16), ("n1-standard-24", 24)]:
            with self.subTest(instance_type=instance_type):
                mock_instance = MockGcpInstance(instance_type=instance_type, nvme_disk_count=24, cpu=cpu)
                io_setup = GcpIoSetup(mock_instance)

                mock_yaml_content = yaml.dump(MOCK_GCP_IO_PARAMS)

                with (
                    unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
                    unittest.mock.patch.object(io_setup, "save") as mock_save,
                    unittest.mock.patch("subprocess.run") as mock_run,
                ):
                    io_setup.generate()

                    assert "read_iops" not in io_setup.disk_properties
                    mock_save.assert_not_called()
                    mock_run.assert_called_once()
                    assert mock_run.call_args.args[0] == "scylla_io_setup"

    def test_gcp_io_setup_runs_iotune_for_unknown_disk_count(self):
        """A disk count Google doesn't publish limits for is measured by iotune."""
        mock_instance = MockGcpInstance(instance_type="n2-standard-32", nvme_disk_count=9, cpu=32)
        io_setup = GcpIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_GCP_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch.object(io_setup, "save") as mock_save,
            unittest.mock.patch("subprocess.run") as mock_run,
        ):
            io_setup.generate()

            mock_save.assert_not_called()
            mock_run.assert_called_once()

    def test_gcp_io_setup_uses_instance_type_regardless_of_cpu_count(self):
        """A machine type with its own entry is measured from that entry, not the per-disk table."""
        mock_instance = MockGcpInstance(instance_type="z3-highmem-8-highlssd", nvme_disk_count=4, cpu=8)
        io_setup = GcpIoSetup(mock_instance)

        mock_yaml_content = yaml.dump(MOCK_GCP_IO_PARAMS)

        with (
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=mock_yaml_content)),
            unittest.mock.patch.object(io_setup, "save") as mock_save,
        ):
            io_setup.generate()

            assert io_setup.disk_properties["read_iops"] == 750000
            mock_save.assert_called_once()

    def test_gcp_io_setup_fallback_when_file_not_found(self):
        """Test GcpIoSetup falls back to iotune when the YAML file is not found."""
        mock_instance = MockGcpInstance(instance_type="n2-standard-32", nvme_disk_count=4, cpu=32)
        io_setup = GcpIoSetup(mock_instance)

        with (
            unittest.mock.patch("builtins.open", side_effect=FileNotFoundError("File not found")),
            unittest.mock.patch.object(io_setup, "save") as mock_save,
            unittest.mock.patch("subprocess.run") as mock_run,
        ):
            io_setup.generate()

            mock_save.assert_not_called()
            mock_run.assert_called_once()

    def test_gcp_io_setup_raises_for_unsupported_instance_class(self):
        """Test that GcpIoSetup raises UnsupportedInstanceClassError for unsupported instances."""
        mock_instance = MockGcpInstance(instance_type="z3-highmem-8-highlssd", supported=False)
        io_setup = GcpIoSetup(mock_instance)

        with pytest.raises(UnsupportedInstanceClassError):
            io_setup.generate()


# Load scylla_image_setup module (file without .py extension)
_image_setup_path = Path(__file__).parent.parent / "common" / "scylla_image_setup"
_setup_spec = importlib.util.spec_from_loader("scylla_image_setup", loader=None, origin=str(_image_setup_path))
scylla_image_setup = importlib.util.module_from_spec(_setup_spec)
with open(_image_setup_path) as _f:
    exec(compile(_f.read(), _image_setup_path, "exec"), scylla_image_setup.__dict__)


class TestSyncGcpMtu(TestCase, GcpMetadata):
    NETPLAN_1460 = "network:\n  version: 2\n  ethernets:\n    eth0:\n      mtu: 1460\n"

    def setUp(self):
        httpretty.enable(verbose=True, allow_net_connect=False)

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    def _paths(self):
        """Redirect every path sync_gcp_mtu_from_vpc() writes into a temp dir."""
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        root = Path(tmpdir.name)
        return SimpleNamespace(
            netplan=root / "99-scylla-mtu.yaml",
            stamp=root / "gcp_mtu_netplan_applied",
            warning=root / "gcp_mtu_warning",
        )

    def _sync(self, tier1, mtu, paths=None):
        """Run one boot's worth of sync_gcp_mtu_from_vpc(), returning the run mock.

        Pass the same `paths` twice to simulate consecutive boots.
        """
        paths = paths or self._paths()
        httpretty.reset()
        self.httpretty_gcp_metadata(mtu=mtu)
        instance = GcpInstance()
        with (
            unittest.mock.patch.object(scylla_image_setup, "run") as mock_run,
            unittest.mock.patch.object(scylla_image_setup, "get_cloud_instance", return_value=instance),
            unittest.mock.patch.object(
                type(instance), "is_tier1_networking", unittest.mock.PropertyMock(return_value=tier1)
            ),
            unittest.mock.patch.object(scylla_image_setup, "NETPLAN_MTU_PATH", paths.netplan),
            unittest.mock.patch.object(scylla_image_setup, "NETPLAN_APPLIED_STAMP", paths.stamp),
            unittest.mock.patch.object(scylla_image_setup, "GCP_MTU_WARNING_PATH", paths.warning),
        ):
            scylla_image_setup.sync_gcp_mtu_from_vpc()
        return mock_run, paths

    def test_tier1_applies_vpc_mtu(self):
        """Tier 1 instance gets the VPC MTU applied to eth0, with no clamp left behind."""
        mock_run, paths = self._sync(tier1=True, mtu=8896)
        mock_run.assert_any_call("ip link set dev eth0 mtu 8896", shell=True, check=True)
        assert not paths.netplan.exists()
        assert not paths.warning.exists()

    def test_tier1_on_non_jumbo_vpc_records_warning(self):
        """A Tier 1 instance on a 1460 VPC records the warning for the login banner."""
        _, paths = self._sync(tier1=True, mtu=1460)
        recorded = paths.warning.read_text()
        assert "1460" in recorded
        assert "8896" in recorded

    def test_recorded_warning_is_world_readable(self):
        """The banner runs as the logging-in user, so the mode must not follow umask."""
        _, paths = self._sync(tier1=True, mtu=1460)
        assert paths.warning.stat().st_mode & 0o777 == 0o644

    def test_standard_clamps_to_min_of_1460_and_vpc(self):
        """Standard instance is clamped to min(1460, vpc_mtu) and the clamp is persisted."""
        mock_run, paths = self._sync(tier1=False, mtu=8896)
        mock_run.assert_any_call("ip link set dev eth0 mtu 1460", shell=True, check=True)
        mock_run.assert_any_call("netplan apply", shell=True, check=True)
        assert "mtu: 1460" in paths.netplan.read_text()
        # Jumbo frames are reserved for Tier 1, so nothing to warn about.
        assert not paths.warning.exists()

    def test_standard_respects_low_vpc_mtu(self):
        """Standard instance on a VPC below 1460 uses the VPC MTU, never exceeds it."""
        mock_run, paths = self._sync(tier1=False, mtu=1400)
        mock_run.assert_any_call("ip link set dev eth0 mtu 1400", shell=True, check=True)
        assert "mtu: 1400" in paths.netplan.read_text()

    def test_standard_does_not_reapply_netplan_on_second_boot(self):
        """An unchanged, already-applied config must not restart networkd again."""
        mock_run, paths = self._sync(tier1=False, mtu=8896)
        assert mock_run.call_args_list.count(unittest.mock.call("netplan apply", shell=True, check=True)) == 1
        second_run, _ = self._sync(tier1=False, mtu=8896, paths=paths)
        assert unittest.mock.call("netplan apply", shell=True, check=True) not in second_run.call_args_list

    def test_standard_reapplies_netplan_when_previous_apply_failed(self):
        """A file written but never successfully applied is retried on the next boot."""
        paths = self._paths()
        paths.netplan.write_text(self.NETPLAN_1460)  # written last boot, stamp absent -> apply failed
        mock_run, _ = self._sync(tier1=False, mtu=8896, paths=paths)
        mock_run.assert_any_call("netplan apply", shell=True, check=True)
        assert paths.stamp.read_text() == self.NETPLAN_1460

    def test_standard_reapplies_netplan_when_file_removed_but_stamp_survives(self):
        """A stamp that outlives the file it describes must not suppress the apply."""
        mock_run, paths = self._sync(tier1=False, mtu=8896)
        mock_run.assert_any_call("netplan apply", shell=True, check=True)
        paths.netplan.unlink()  # file removed by hand; stamp still matches
        second_run, _ = self._sync(tier1=False, mtu=8896, paths=paths)
        assert paths.netplan.exists()
        second_run.assert_any_call("netplan apply", shell=True, check=True)

    def test_tier1_clears_stale_clamp(self):
        """Switching from a standard to a Tier 1 instance type removes the netplan clamp."""
        paths = self._paths()
        paths.netplan.write_text(self.NETPLAN_1460)
        paths.stamp.write_text(self.NETPLAN_1460)
        mock_run, _ = self._sync(tier1=True, mtu=8896, paths=paths)
        assert not paths.netplan.exists()
        assert not paths.stamp.exists()
        mock_run.assert_any_call("netplan apply", shell=True, check=True)

    def test_unknown_instance_skips_mtu(self):
        """Inconclusive detection changes nothing at all."""
        mock_run, paths = self._sync(tier1=None, mtu=8896)
        mock_run.assert_not_called()
        assert not paths.netplan.exists()
        assert not paths.warning.exists()

    def test_inconclusive_boot_clears_stale_warning(self):
        """A warning from a previous boot must not survive an inconclusive one."""
        _, paths = self._sync(tier1=True, mtu=1460)
        assert "1460" in paths.warning.read_text()
        # Next boot: VPC raised to 8896, but detection can no longer tell.
        self._sync(tier1=None, mtu=8896, paths=paths)
        assert not paths.warning.exists()

    def test_tier1_on_jumbo_vpc_clears_stale_warning(self):
        """Once the VPC is fixed, the recorded warning goes away."""
        _, paths = self._sync(tier1=True, mtu=1460)
        assert paths.warning.exists()
        self._sync(tier1=True, mtu=8896, paths=paths)
        assert not paths.warning.exists()
