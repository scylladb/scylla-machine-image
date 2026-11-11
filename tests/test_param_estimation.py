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
    estimate_streaming_bandwidth,
)


OCI_NET_PARAMS_PATH = str(Path(__file__).parent.parent / "common" / "oci_net_params.json")


class DummyOciCloudInstance:
    def __init__(self, instancetype, ocpus=0):
        self.instancetype = instancetype
        self.ocpus = ocpus


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
