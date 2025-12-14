#!/usr/bin/env python3
#
# Copyright 2024 ScyllaDB
#
# SPDX-License-Identifier: Apache-2.0

import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import Mock, patch


# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

# Import the scylla_create_devices module (file without extension) using SourceFileLoader
_module_path = str(Path(__file__).parent.parent / "common" / "scylla_create_devices")
scylla_create_devices = SourceFileLoader("scylla_create_devices", _module_path).load_module()
sys.modules["scylla_create_devices"] = scylla_create_devices

from scylla_create_devices import (  # noqa: E402
    _get_fresh_local_disks,
    _get_fresh_remote_disks,
    get_default_devices,
    get_disk_devices,
    wait_for_devices,
)


# Tests for wait_for_devices function


@patch("scylla_create_devices.time.sleep")
@patch("scylla_create_devices.time.time")
@patch("scylla_create_devices.Path")
def test_wait_for_devices_with_callable_retries(mock_path_class, mock_time, mock_sleep):
    """Test wait_for_devices with callable that returns devices after retries."""
    # Mock time to return incrementing values - start at 0
    time_values = [0]

    def get_time():
        val = time_values[0]
        time_values[0] += 5
        return val

    mock_time.side_effect = get_time

    # Create mock devices that don't exist at first, then appear
    mock_device = Mock()
    mock_device.exists.side_effect = [False, False, True]  # Found on third check
    mock_device.__str__ = Mock(return_value="/dev/nvme0n1")

    mock_path_class.return_value = mock_device

    call_count = [0]

    def device_getter():
        """Simulates device discovery that returns devices on third call."""
        call_count[0] += 1
        if call_count[0] >= 3:
            return ["nvme0n1"]
        return []

    wait_for_devices(device_getter, wait_seconds=30)

    # Should have slept multiple times before finding the device
    assert mock_sleep.call_count >= 2
    assert device_getter.__name__ == "device_getter"


@patch("scylla_create_devices.time.sleep")
@patch("scylla_create_devices.time.time")
def test_wait_for_devices_timeout(mock_time, mock_sleep):
    """Test wait_for_devices when no devices appear within timeout."""
    # Simulate timeout: start time 0, checks at 5, 10, 15, 20, 25, 30 (timeout)
    mock_time.side_effect = [0, 5, 10, 15, 20, 25, 31]

    call_count = [0]

    def device_getter():
        """Device getter that never returns any devices."""
        call_count[0] += 1
        return []

    # Should not raise, just print warning
    wait_for_devices(device_getter, wait_seconds=30)

    # Should have called device_getter multiple times
    assert call_count[0] > 0


@patch("scylla_create_devices.time.sleep")
@patch("scylla_create_devices.time.time")
@patch("scylla_create_devices.Path")
def test_wait_for_devices_with_callable_finds_any_device(mock_path_class, mock_time, mock_sleep):
    """Test that wait_for_devices stops when ANY device is found, not all."""
    mock_time.side_effect = [0, 5, 10]

    # Create two mock devices, only one exists
    mock_device1 = Mock()
    mock_device1.exists.return_value = True
    mock_device1.__str__ = Mock(return_value="/dev/nvme0n1")

    mock_device2 = Mock()
    mock_device2.exists.return_value = False
    mock_device2.__str__ = Mock(return_value="/dev/nvme1n1")

    mock_path_class.side_effect = [mock_device1, mock_device2]

    def device_getter():
        return ["nvme0n1", "nvme1n1"]

    wait_for_devices(device_getter, wait_seconds=30)

    # Should not sleep since one device was found
    mock_sleep.assert_not_called()


def test_wait_for_devices_zero_wait_seconds():
    """Test that wait_for_devices returns immediately when wait_seconds is 0."""
    call_count = [0]

    def device_getter():
        call_count[0] += 1
        return ["nvme0n1"]

    wait_for_devices(device_getter, wait_seconds=0)

    # Should not call device_getter at all
    assert call_count[0] == 0


# Tests for _get_fresh_* helper functions


@patch("scylla_create_devices.get_cloud_instance")
def test_get_fresh_local_disks_success(mock_get_cloud_instance):
    """Test _get_fresh_local_disks returns disks from cloud instance."""
    mock_instance = Mock()
    mock_instance.get_local_disks.return_value = ["nvme0n1", "nvme1n1"]
    mock_get_cloud_instance.return_value = mock_instance

    result = _get_fresh_local_disks()

    assert result == ["nvme0n1", "nvme1n1"]
    mock_get_cloud_instance.assert_called_once()
    mock_instance.get_local_disks.assert_called_once()


@patch("scylla_create_devices.get_cloud_instance")
def test_get_fresh_local_disks_none_instance(mock_get_cloud_instance):
    """Test _get_fresh_local_disks returns empty list when instance is None."""
    mock_get_cloud_instance.return_value = None

    result = _get_fresh_local_disks()

    assert result == []


@patch("scylla_create_devices.get_cloud_instance")
def test_get_fresh_local_disks_none_disks(mock_get_cloud_instance):
    """Test _get_fresh_local_disks returns empty list when get_local_disks returns None."""
    mock_instance = Mock()
    mock_instance.get_local_disks.return_value = None
    mock_get_cloud_instance.return_value = mock_instance

    result = _get_fresh_local_disks()

    assert result == []


@patch("scylla_create_devices.get_cloud_instance")
def test_get_fresh_remote_disks_success(mock_get_cloud_instance):
    """Test _get_fresh_remote_disks returns disks from cloud instance."""
    mock_instance = Mock()
    mock_instance.get_remote_disks.return_value = ["sdb", "sdc"]
    mock_get_cloud_instance.return_value = mock_instance

    result = _get_fresh_remote_disks()

    assert result == ["sdb", "sdc"]


# Tests for get_disk_devices function


@patch("scylla_create_devices.is_ec2")
@patch("scylla_create_devices.is_gce")
@patch("scylla_create_devices.is_azure")
@patch("scylla_create_devices.is_oci")
@patch("scylla_create_devices.wait_for_devices")
@patch("scylla_create_devices.check_persistent_disks_are_empty")
@patch("scylla_create_devices.Path")
def test_get_disk_devices_ec2_attached(
    mock_path, mock_check_empty, mock_wait, mock_is_oci, mock_is_azure, mock_is_gce, mock_is_ec2
):
    """Test get_disk_devices for EC2 with attached devices."""
    mock_is_ec2.return_value = True
    mock_is_gce.return_value = False
    mock_is_azure.return_value = False
    mock_is_oci.return_value = False

    mock_instance = Mock()
    mock_instance.get_remote_disks.return_value = ["nvme1n1", "nvme2n1"]

    # Mock Path to simulate devices exist
    mock_dev_path = Mock()
    mock_dev_path.exists.return_value = True
    mock_dev_path.__str__ = Mock(return_value="/dev/nvme1n1")
    mock_path.return_value = mock_dev_path

    result = get_disk_devices(mock_instance, "attached", wait_seconds=10)

    # Should call wait_for_devices with callable
    assert mock_wait.called
    # Should check that disks are empty
    mock_check_empty.assert_called_once()
    # Should return device paths
    assert len(result) > 0


@patch("scylla_create_devices.is_ec2")
@patch("scylla_create_devices.is_gce")
@patch("scylla_create_devices.get_default_devices")
def test_get_disk_devices_gce(mock_get_default, mock_is_gce, mock_is_ec2):
    """Test get_disk_devices for GCE uses get_default_devices."""
    mock_is_ec2.return_value = False
    mock_is_gce.return_value = True
    mock_get_default.return_value = ["/dev/sdb"]

    mock_instance = Mock()

    result = get_disk_devices(mock_instance, "auto", wait_seconds=10)

    mock_get_default.assert_called_once_with(mock_instance, 10)
    assert result == ["/dev/sdb"]


# Tests for get_default_devices function


@patch("scylla_create_devices.wait_for_devices")
@patch("scylla_create_devices.check_persistent_disks_are_empty")
@patch("scylla_create_devices.Path")
def test_get_default_devices_local_disks_found(mock_path, mock_check_empty, mock_wait):
    """Test get_default_devices when local disks are found."""
    mock_instance = Mock()
    mock_instance.get_local_disks.return_value = ["nvme0n1"]
    mock_instance.get_remote_disks.return_value = []

    mock_dev_path = Mock()
    mock_dev_path.exists.return_value = True
    mock_dev_path.__str__ = Mock(return_value="/dev/nvme0n1")
    mock_path.return_value = mock_dev_path

    result = get_default_devices(mock_instance, wait_seconds=10)

    # Should call wait_for_devices once for local disks
    assert mock_wait.call_count == 1
    # Should not check empty for local disks
    mock_check_empty.assert_not_called()
    assert len(result) > 0


@patch("scylla_create_devices.wait_for_devices")
@patch("scylla_create_devices.check_persistent_disks_are_empty")
@patch("scylla_create_devices.Path")
def test_get_default_devices_remote_disks_fallback(mock_path, mock_check_empty, mock_wait):
    """Test get_default_devices falls back to remote disks when no local disks."""
    mock_instance = Mock()
    mock_instance.get_local_disks.return_value = []
    mock_instance.get_remote_disks.return_value = ["sdb", "sdc"]

    mock_dev_path = Mock()
    mock_dev_path.exists.return_value = True
    mock_path.return_value = mock_dev_path

    get_default_devices(mock_instance, wait_seconds=10)

    # Should call wait_for_devices twice (local then remote)
    assert mock_wait.call_count == 2
    # Should check that remote disks are empty
    mock_check_empty.assert_called_once()
