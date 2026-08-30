#
# Copyright 2025 ScyllaDB
#
# SPDX-License-Identifier: Apache-2.0
import json
import sys
import unittest.mock
from pathlib import Path
from unittest import TestCase

import pytest


sys.path.append(str(Path(__file__).parent.parent))

from lib.param_estimation import (
    _get_nic_speed_mbps,
    detect_gcp_tier1,
    estimate_streaming_bandwidth,
)


GCP_NET_PARAMS_PATH = str(Path(__file__).parent.parent / "common" / "gcp_net_params.json")
OCI_NET_PARAMS_PATH = str(Path(__file__).parent.parent / "common" / "oci_net_params.json")


class DummyCloudInstance:
    def __init__(self, instancetype):
        self.instancetype = instancetype

    @property
    def tier1_override(self):
        """Mirror GcpInstance.tier1_override, which honours an assigned override."""
        return getattr(self, "_tier1_override", None)


class DummyOciCloudInstance:
    def __init__(self, instancetype, ocpus=0):
        self.instancetype = instancetype
        self.ocpus = ocpus


@pytest.mark.unit
class TestGcpStreamingBandwidth(TestCase):
    """Tests for GCP streaming bandwidth estimation using gcp_net_params.json."""

    def _estimate_gcp_bandwidth(self, instance_type):
        """Helper to estimate streaming bandwidth for a GCP instance type."""
        with (
            unittest.mock.patch("lib.param_estimation.is_ec2", return_value=False),
            unittest.mock.patch("lib.param_estimation.is_oci", return_value=False),
            unittest.mock.patch("lib.param_estimation.is_azure", return_value=False),
            unittest.mock.patch("lib.param_estimation.is_gce", return_value=True),
            unittest.mock.patch(
                "lib.param_estimation.get_cloud_instance",
                return_value=DummyCloudInstance(instance_type),
            ),
            unittest.mock.patch(
                "builtins.open",
                unittest.mock.mock_open(read_data=Path(GCP_NET_PARAMS_PATH).read_text()),
            ),
            unittest.mock.patch("lib.param_estimation._get_nic_speed_mbps", return_value=None),
            unittest.mock.patch("lib.param_estimation._query_metadata_tier1_attribute", return_value=None),
            unittest.mock.patch("lib.param_estimation._query_compute_api_tier", return_value=None),
        ):
            return estimate_streaming_bandwidth()

    def _expected_bandwidth_mib(self, gbps):
        """Calculate the expected streaming bandwidth in MiB/s (75% of network bandwidth)."""
        net_bw_bps = int(gbps * 1000 * 1000 * 1000)
        return int((0.75 * net_bw_bps) / (8 * 1024 * 1024))

    def test_n2_standard_2_bandwidth(self):
        """n2-standard-2 has 10 Gbps default bandwidth."""
        result = self._estimate_gcp_bandwidth("n2-standard-2")
        assert result == self._expected_bandwidth_mib(10.0)

    def test_n2_standard_8_bandwidth(self):
        """n2-standard-8 has 16 Gbps default bandwidth."""
        result = self._estimate_gcp_bandwidth("n2-standard-8")
        assert result == self._expected_bandwidth_mib(16.0)

    def test_n2_standard_16_bandwidth(self):
        """n2-standard-16 has 32 Gbps default bandwidth."""
        result = self._estimate_gcp_bandwidth("n2-standard-16")
        assert result == self._expected_bandwidth_mib(32.0)

    def test_n2_standard_128_bandwidth(self):
        """n2-standard-128 has 32 Gbps default bandwidth (caps at 32 without Tier_1)."""
        result = self._estimate_gcp_bandwidth("n2-standard-128")
        assert result == self._expected_bandwidth_mib(32.0)

    def test_n2d_highmem_4_bandwidth(self):
        """n2d-highmem-4 has 10 Gbps default bandwidth."""
        result = self._estimate_gcp_bandwidth("n2d-highmem-4")
        assert result == self._expected_bandwidth_mib(10.0)

    def test_n2d_standard_224_bandwidth(self):
        """n2d-standard-224 has 32 Gbps default bandwidth."""
        result = self._estimate_gcp_bandwidth("n2d-standard-224")
        assert result == self._expected_bandwidth_mib(32.0)

    def test_z3_highmem_8_highlssd_bandwidth(self):
        """z3-highmem-8-highlssd has 100 Gbps default bandwidth."""
        result = self._estimate_gcp_bandwidth("z3-highmem-8-highlssd")
        assert result == self._expected_bandwidth_mib(100.0)

    def test_z3_highmem_88_standardlssd_bandwidth(self):
        """z3-highmem-88-standardlssd has 100 Gbps default bandwidth."""
        result = self._estimate_gcp_bandwidth("z3-highmem-88-standardlssd")
        assert result == self._expected_bandwidth_mib(100.0)

    def test_z3_highmem_176_standardlssd_bandwidth(self):
        """z3-highmem-176-standardlssd has 100 Gbps default bandwidth."""
        result = self._estimate_gcp_bandwidth("z3-highmem-176-standardlssd")
        assert result == self._expected_bandwidth_mib(100.0)

    def test_unknown_instance_returns_zero(self):
        """Unknown instance types should return 0 bandwidth."""
        result = self._estimate_gcp_bandwidth("unknown-instance-type")
        assert result == 0

    def test_bandwidth_values_are_positive(self):
        """All known GCP instance types should have positive bandwidth."""
        with open(GCP_NET_PARAMS_PATH) as f:
            netinfo = json.load(f)
        for entry in netinfo:
            result = self._estimate_gcp_bandwidth(entry[0])
            assert result > 0, f"Instance {entry[0]} should have positive bandwidth, got {result}"

    def test_json_structure(self):
        """Verify gcp_net_params.json has the expected structure."""
        with open(GCP_NET_PARAMS_PATH) as f:
            netinfo = json.load(f)
        assert isinstance(netinfo, list)
        for entry in netinfo:
            assert isinstance(entry, list)
            assert len(entry) == 3
            assert isinstance(entry[0], str)  # instance type
            assert isinstance(entry[1], int | float)  # default bandwidth
            assert entry[2] is None or isinstance(entry[2], int | float)  # tier1 bandwidth

    def test_n2_family_coverage(self):
        """Verify all expected N2 instance types are present."""
        with open(GCP_NET_PARAMS_PATH) as f:
            netinfo = json.load(f)
        types = {entry[0] for entry in netinfo}
        for purpose, sizes in [
            ("standard", [2, 4, 8, 16, 32, 48, 64, 80, 96, 128]),
            ("highmem", [2, 4, 8, 16, 32, 48, 64, 80, 96, 128]),
            ("highcpu", [2, 4, 8, 16, 32, 48, 64, 80, 96]),
        ]:
            for size in sizes:
                assert f"n2-{purpose}-{size}" in types

    def test_n2d_family_coverage(self):
        """Verify all expected N2D instance types are present."""
        with open(GCP_NET_PARAMS_PATH) as f:
            netinfo = json.load(f)
        types = {entry[0] for entry in netinfo}
        for purpose, sizes in [
            ("standard", [2, 4, 8, 16, 32, 48, 64, 80, 96, 128, 224]),
            ("highmem", [2, 4, 8, 16, 32, 48, 64, 80, 96]),
            ("highcpu", [2, 4, 8, 16, 32, 48, 64, 80, 96, 128, 224]),
        ]:
            for size in sizes:
                assert f"n2d-{purpose}-{size}" in types

    def test_z3_family_coverage(self):
        """Verify Z3 instance types from gcp_io_params.yaml are present."""
        with open(GCP_NET_PARAMS_PATH) as f:
            netinfo = json.load(f)
        types = {entry[0] for entry in netinfo}
        # Z3 types from gcp_io_params.yaml
        expected_z3 = [
            "z3-highmem-8-highlssd",
            "z3-highmem-16-highlssd",
            "z3-highmem-22-highlssd",
            "z3-highmem-88-standardlssd",
            "z3-highmem-176-standardlssd",
        ]
        for z3_type in expected_z3:
            assert z3_type in types

    def test_tier1_values_only_for_large_n2(self):
        """Tier_1 bandwidth should only be set for N2/N2D instances with >=48 vCPUs."""
        with open(GCP_NET_PARAMS_PATH) as f:
            netinfo = json.load(f)
        for entry in netinfo:
            inst_type = entry[0]
            tier1_bw = entry[2]
            if inst_type.startswith(("n2-", "n2d-")):
                parts = inst_type.split("-")
                vcpus = int(parts[2])
                if vcpus < 48:
                    assert tier1_bw is None, f"{inst_type} with {vcpus} vCPUs should not have Tier_1"
                else:
                    assert tier1_bw is not None, f"{inst_type} with {vcpus} vCPUs should have Tier_1"

    def test_z3_all_have_tier1(self):
        """All Z3 instance types should have Tier_1 bandwidth of 200 Gbps."""
        with open(GCP_NET_PARAMS_PATH) as f:
            netinfo = json.load(f)
        for entry in netinfo:
            if entry[0].startswith("z3-"):
                assert entry[1] == 100.0, f"{entry[0]} default should be 100 Gbps"
                assert entry[2] == 200.0, f"{entry[0]} Tier_1 should be 200 Gbps"


@pytest.mark.unit
class TestGcpTier1Detection(TestCase):
    """Tests for GCP Tier 1 networking detection logic."""

    GCP_NET_PARAMS = Path(GCP_NET_PARAMS_PATH).read_text()

    def _estimate_with_tier1_mocks(self, instance_type, nic_speed=None, api_tier=None, tier1_override=None):
        cloud_inst = DummyCloudInstance(instance_type)
        if tier1_override is not None:
            cloud_inst._tier1_override = tier1_override
        with (
            unittest.mock.patch("lib.param_estimation.is_ec2", return_value=False),
            unittest.mock.patch("lib.param_estimation.is_oci", return_value=False),
            unittest.mock.patch("lib.param_estimation.is_azure", return_value=False),
            unittest.mock.patch("lib.param_estimation.is_gce", return_value=True),
            unittest.mock.patch("lib.param_estimation.get_cloud_instance", return_value=cloud_inst),
            unittest.mock.patch(
                "builtins.open",
                unittest.mock.mock_open(read_data=self.GCP_NET_PARAMS),
            ),
            unittest.mock.patch("lib.param_estimation._query_metadata_tier1_attribute", return_value=None),
            unittest.mock.patch("lib.param_estimation._get_nic_speed_mbps", return_value=nic_speed),
            unittest.mock.patch("lib.param_estimation._query_compute_api_tier", return_value=api_tier),
        ):
            return estimate_streaming_bandwidth()

    def _expected_bandwidth_mib(self, gbps):
        net_bw_bps = int(gbps * 1000 * 1000 * 1000)
        return int((0.75 * net_bw_bps) / (8 * 1024 * 1024))

    def test_gce_uses_tier1_when_sysfs_speed_exceeds_default(self):
        """Sysfs speed > default_mbps → tier1 bandwidth is selected."""
        result = self._estimate_with_tier1_mocks("n2-standard-48", nic_speed=50000)
        assert result == self._expected_bandwidth_mib(50.0)

    def test_gce_uses_default_when_sysfs_speed_matches_default(self):
        """Sysfs speed == default_mbps → default bandwidth is selected."""
        result = self._estimate_with_tier1_mocks("n2-standard-48", nic_speed=32000)
        assert result == self._expected_bandwidth_mib(32.0)

    def test_gce_uses_tier1_when_api_returns_tier1(self):
        """Sysfs unavailable but API returns TIER_1 → tier1 bandwidth is selected."""
        result = self._estimate_with_tier1_mocks("n2-standard-48", nic_speed=None, api_tier="TIER_1")
        assert result == self._expected_bandwidth_mib(50.0)

    def test_gce_uses_default_when_sysfs_fails_and_api_unavailable(self):
        """Both sysfs and API unavailable → conservative default bandwidth."""
        result = self._estimate_with_tier1_mocks("n2-standard-48", nic_speed=None, api_tier=None)
        assert result == self._expected_bandwidth_mib(32.0)

    def test_gce_user_override_true_forces_tier1(self):
        """tier1_networking=True in user-data always selects tier1, even when sysfs says default."""
        result = self._estimate_with_tier1_mocks("n2-standard-48", nic_speed=32000, api_tier=None, tier1_override=True)
        assert result == self._expected_bandwidth_mib(50.0)

    def test_gce_user_override_false_prevents_tier1(self):
        """tier1_networking=False in user-data always selects default, even when sysfs says tier1."""
        result = self._estimate_with_tier1_mocks(
            "n2-standard-48", nic_speed=50000, api_tier="TIER_1", tier1_override=False
        )
        assert result == self._expected_bandwidth_mib(32.0)

    def test_gce_tier1_null_instance_always_default(self):
        """Instance with null tier1_bw in JSON always returns default regardless of overrides."""
        result = self._estimate_with_tier1_mocks(
            "n2-standard-2", nic_speed=99999, api_tier="TIER_1", tier1_override=True
        )
        assert result == self._expected_bandwidth_mib(10.0)

    def test_get_nic_speed_mbps_reads_sysfs(self):
        """_get_nic_speed_mbps reads integer Mbps from /sys/class/net/<iface>/speed."""
        with (
            unittest.mock.patch("lib.param_estimation._get_gcp_primary_interface", return_value="ens4"),
            unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data="32000\n")),
        ):
            result = _get_nic_speed_mbps()
        assert result == 32000

    def test_get_nic_speed_mbps_returns_none_on_failure(self):
        """_get_nic_speed_mbps returns None when sysfs file is unavailable."""
        with unittest.mock.patch("builtins.open", side_effect=OSError("No such file or directory")):
            result = _get_nic_speed_mbps()
        assert result is None

    def test_detect_gcp_tier1_returns_false_for_unsupported_instance(self):
        """detect_gcp_tier1 returns False when tier1_bw_gbps is None regardless of inputs."""
        assert detect_gcp_tier1(10.0, None) is False
        assert detect_gcp_tier1(10.0, None, tier1_override=True) is False

    def test_detect_gcp_tier1_override_respected_before_sysfs(self):
        """detect_gcp_tier1 returns override value before any sysfs/API calls."""
        with (
            unittest.mock.patch("lib.param_estimation._get_nic_speed_mbps") as mock_speed,
            unittest.mock.patch("lib.param_estimation._query_compute_api_tier") as mock_api,
            unittest.mock.patch("lib.param_estimation._query_metadata_tier1_attribute") as mock_meta,
        ):
            assert detect_gcp_tier1(32.0, 50.0, tier1_override=True) is True
            assert detect_gcp_tier1(32.0, 50.0, tier1_override=False) is False
            mock_speed.assert_not_called()
            mock_api.assert_not_called()
            mock_meta.assert_not_called()

    def test_detect_gcp_tier1_metadata_attribute_true(self):
        """GCP instance metadata attribute scylla_tier1_networking=true enables tier1."""
        with (
            unittest.mock.patch("lib.param_estimation._query_metadata_tier1_attribute", return_value=True),
            unittest.mock.patch("lib.param_estimation._get_nic_speed_mbps", return_value=None),
            unittest.mock.patch("lib.param_estimation._query_compute_api_tier", return_value=None),
        ):
            result = detect_gcp_tier1(32.0, 50.0, tier1_override=None)
        assert result is True

    def test_detect_gcp_tier1_inconclusive_returns_none(self):
        """No evidence in either direction returns None, not False.

        None must stay distinguishable from False: callers clamp the MTU on
        False and skip entirely on None.
        """
        with (
            unittest.mock.patch("lib.param_estimation._query_metadata_tier1_attribute", return_value=None),
            unittest.mock.patch("lib.param_estimation._get_nic_speed_mbps", return_value=None),
            unittest.mock.patch("lib.param_estimation._query_compute_api_tier", return_value=None),
        ):
            result = detect_gcp_tier1(32.0, 50.0, tier1_override=None)
        assert result is None

    def test_detect_gcp_tier1_api_default_returns_false(self):
        """An explicit DEFAULT tier from the Compute API is negative evidence, not absence of it."""
        with (
            unittest.mock.patch("lib.param_estimation._query_metadata_tier1_attribute", return_value=None),
            unittest.mock.patch("lib.param_estimation._get_nic_speed_mbps", return_value=None),
            unittest.mock.patch("lib.param_estimation._query_compute_api_tier", return_value="DEFAULT"),
        ):
            result = detect_gcp_tier1(32.0, 50.0, tier1_override=None)
        assert result is False

    def test_detect_gcp_tier1_metadata_attribute_false(self):
        """GCP instance metadata attribute scylla_tier1_networking=false disables tier1 even if sysfs says tier1."""
        with (
            unittest.mock.patch("lib.param_estimation._query_metadata_tier1_attribute", return_value=False),
            unittest.mock.patch("lib.param_estimation._get_nic_speed_mbps", return_value=50000),
            unittest.mock.patch("lib.param_estimation._query_compute_api_tier", return_value="TIER_1"),
        ):
            result = detect_gcp_tier1(32.0, 50.0, tier1_override=None)
        assert result is False

    def test_detect_gcp_tier1_user_override_beats_metadata_attribute(self):
        """User-data tier1_override always wins over metadata attribute."""
        with (
            unittest.mock.patch("lib.param_estimation._query_metadata_tier1_attribute") as mock_meta,
            unittest.mock.patch("lib.param_estimation._get_nic_speed_mbps") as mock_speed,
        ):
            # User says False, metadata says True — user wins
            result = detect_gcp_tier1(32.0, 50.0, tier1_override=False)
            assert result is False
            mock_meta.assert_not_called()
            mock_speed.assert_not_called()

    def test_detect_gcp_tier1_override_string_normalization(self):
        """String tier1_override values are normalized to boolean (user-data may be strings)."""
        with (
            unittest.mock.patch("lib.param_estimation._query_metadata_tier1_attribute") as mock_meta,
            unittest.mock.patch("lib.param_estimation._get_nic_speed_mbps") as mock_speed,
            unittest.mock.patch("lib.param_estimation._query_compute_api_tier") as mock_api,
        ):
            assert detect_gcp_tier1(32.0, 50.0, tier1_override="true") is True
            assert detect_gcp_tier1(32.0, 50.0, tier1_override="True") is True
            assert detect_gcp_tier1(32.0, 50.0, tier1_override="1") is True
            assert detect_gcp_tier1(32.0, 50.0, tier1_override="yes") is True
            assert detect_gcp_tier1(32.0, 50.0, tier1_override="false") is False
            assert detect_gcp_tier1(32.0, 50.0, tier1_override="False") is False
            assert detect_gcp_tier1(32.0, 50.0, tier1_override="0") is False
            assert detect_gcp_tier1(32.0, 50.0, tier1_override="no") is False
            assert detect_gcp_tier1(32.0, 50.0, tier1_override="garbage") is False
            mock_meta.assert_not_called()
            mock_speed.assert_not_called()
            mock_api.assert_not_called()


@pytest.mark.unit
class TestOciStreamingBandwidth(TestCase):
    """Tests for OCI streaming bandwidth estimation using oci_net_params.json.

    Covers the SMI-300 fix: for flex shapes the bandwidth must be computed from
    networkingBandwidthPerOcpuInGbps * ocpus and NOT be overwritten by the fixed
    (per-instance) networkingBandwidthInGbps value.
    """

    OCI_NET_PARAMS = Path(OCI_NET_PARAMS_PATH).read_text()

    def _estimate_oci_bandwidth(self, instance_type, ocpus=0):
        """Helper to estimate streaming bandwidth for an OCI instance type."""
        with (
            unittest.mock.patch("lib.param_estimation.is_ec2", return_value=False),
            unittest.mock.patch("lib.param_estimation.is_oci", return_value=True),
            unittest.mock.patch("lib.param_estimation.is_azure", return_value=False),
            unittest.mock.patch("lib.param_estimation.is_gce", return_value=False),
            unittest.mock.patch(
                "lib.param_estimation.get_cloud_instance",
                return_value=DummyOciCloudInstance(instance_type, ocpus=ocpus),
            ),
            unittest.mock.patch(
                "builtins.open",
                unittest.mock.mock_open(read_data=self.OCI_NET_PARAMS),
            ),
        ):
            return estimate_streaming_bandwidth()

    def _expected_bandwidth_mib(self, gbps):
        """Calculate the expected streaming bandwidth in MiB/s (75% of network bandwidth)."""
        net_bw_bps = int(gbps * 1000 * 1000 * 1000)
        return int((0.75 * net_bw_bps) / (8 * 1024 * 1024))

    def _lookup(self, instance_type):
        """Return the [type, bw, bw_per_ocpu] entry for an instance type from the JSON."""
        netinfo = json.loads(self.OCI_NET_PARAMS)
        for entry in netinfo:
            if entry[0] == instance_type:
                return entry
        raise AssertionError(f"{instance_type} not found in oci_net_params.json")

    def test_flex_shape_scales_with_ocpus(self):
        """SMI-300: flex shapes must use per-OCPU bandwidth * ocpus, not the fixed base value.

        Before the fix, `if instance_info[1]:` overwrote the per-OCPU computation with
        the (small) fixed base bandwidth, collapsing the streaming limit to ~1 Gbps.
        """
        _, _, per_ocpu = self._lookup("VM.Standard.E4.Flex")
        ocpus = 32
        result = self._estimate_oci_bandwidth("VM.Standard.E4.Flex", ocpus=ocpus)
        assert result == self._expected_bandwidth_mib(per_ocpu * ocpus)

    def test_flex_shape_not_capped_by_base_bandwidth(self):
        """A flex shape with many OCPUs must exceed what the old (buggy) base value produced."""
        _, base, per_ocpu = self._lookup("VM.Optimized3.Flex")
        ocpus = 18
        result = self._estimate_oci_bandwidth("VM.Optimized3.Flex", ocpus=ocpus)
        buggy_result = self._expected_bandwidth_mib(base)  # what the pre-fix code returned
        assert result == self._expected_bandwidth_mib(per_ocpu * ocpus)
        assert result > buggy_result

    def test_flex_shape_various_ocpu_counts(self):
        """Streaming bandwidth for flex shapes scales linearly with OCPU count."""
        _, _, per_ocpu = self._lookup("VM.Standard.E4.Flex")
        for ocpus in (1, 4, 8, 64):
            result = self._estimate_oci_bandwidth("VM.Standard.E4.Flex", ocpus=ocpus)
            assert result == self._expected_bandwidth_mib(per_ocpu * ocpus)

    def test_flex_shape_zero_ocpus_returns_zero(self):
        """When ocpus is unavailable (0) a flex shape yields no bandwidth."""
        result = self._estimate_oci_bandwidth("VM.Standard.E4.Flex", ocpus=0)
        assert result == 0

    def test_non_flex_bare_metal_uses_fixed_bandwidth(self):
        """Non-flex (bare-metal) shapes use networkingBandwidthInGbps regardless of ocpus."""
        _, base, per_ocpu = self._lookup("BM.Standard.E4.128")
        assert per_ocpu is None  # non-flex shape has no per-OCPU value
        result = self._estimate_oci_bandwidth("BM.Standard.E4.128", ocpus=0)
        assert result == self._expected_bandwidth_mib(base)

    def test_non_flex_ignores_ocpus(self):
        """Non-flex shapes must not scale with ocpus (per-OCPU value is null)."""
        _, base, _ = self._lookup("BM.Standard2.52")
        result_no_ocpus = self._estimate_oci_bandwidth("BM.Standard2.52", ocpus=0)
        result_many_ocpus = self._estimate_oci_bandwidth("BM.Standard2.52", ocpus=52)
        assert result_no_ocpus == result_many_ocpus == self._expected_bandwidth_mib(base)

    def test_unknown_instance_returns_zero(self):
        """Unknown OCI instance types should return 0 bandwidth."""
        result = self._estimate_oci_bandwidth("VM.DoesNotExist.Flex", ocpus=16)
        assert result == 0

    def test_json_structure(self):
        """Verify oci_net_params.json has the expected [type, bw, bw_per_ocpu] structure."""
        netinfo = json.loads(self.OCI_NET_PARAMS)
        assert isinstance(netinfo, list)
        for entry in netinfo:
            assert isinstance(entry, list)
            assert len(entry) == 3
            assert isinstance(entry[0], str)  # instance type
            assert isinstance(entry[1], int | float)  # networkingBandwidthInGbps
            # networkingBandwidthPerOcpuInGbps: set for flex shapes, null otherwise
            assert entry[2] is None or isinstance(entry[2], int | float)

    def test_flex_shapes_have_per_ocpu_value(self):
        """Every '.Flex' shape must carry a per-OCPU bandwidth value (needed by the fix)."""
        netinfo = json.loads(self.OCI_NET_PARAMS)
        for entry in netinfo:
            if entry[0].endswith(".Flex"):
                assert entry[2] is not None, f"{entry[0]} flex shape must have a per-OCPU bandwidth"
