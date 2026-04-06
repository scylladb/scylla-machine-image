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

from lib.param_estimation import estimate_streaming_bandwidth


GCP_NET_PARAMS_PATH = str(Path(__file__).parent.parent / "common" / "gcp_net_params.json")


class DummyCloudInstance:
    def __init__(self, instancetype):
        self.instancetype = instancetype


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
                unittest.mock.mock_open(
                    read_data=Path(GCP_NET_PARAMS_PATH).read_text()
                ),
            ),
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
            assert isinstance(entry[1], (int, float))  # default bandwidth
            assert entry[2] is None or isinstance(entry[2], (int, float))  # tier1 bandwidth

    def test_n2_family_coverage(self):
        """Verify all expected N2 instance types are present."""
        with open(GCP_NET_PARAMS_PATH) as f:
            netinfo = json.load(f)
        types = {entry[0] for entry in netinfo}
        for purpose in ["standard", "highmem", "highcpu"]:
            for size in [2, 4, 8, 16, 32, 48, 64, 80, 96]:
                assert f"n2-{purpose}-{size}" in types

    def test_n2d_family_coverage(self):
        """Verify all expected N2D instance types are present."""
        with open(GCP_NET_PARAMS_PATH) as f:
            netinfo = json.load(f)
        types = {entry[0] for entry in netinfo}
        for purpose in ["standard", "highmem", "highcpu"]:
            for size in [2, 4, 8, 16, 32, 48, 64, 80, 96]:
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
