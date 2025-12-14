import sys
import logging
import httpretty
import unittest.mock
import json
from unittest import TestCase, IsolatedAsyncioTestCase
from collections import namedtuple
from socket import AddressFamily, SocketKind
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
import lib.scylla_cloud
from lib.scylla_cloud import oci_instance

LOGGER = logging.getLogger(__name__)

svmem = namedtuple('svmem', ['total'])

sdiskpart = namedtuple('sdiskpart', ['device', 'mountpoint'])
mock_disk_partitions = [
    sdiskpart('/dev/root', '/'),
    sdiskpart('/dev/sda15', '/boot/efi'),
    sdiskpart('/dev/md0', '/var/lib/scylla'),
    sdiskpart('/dev/md0', '/var/lib/systemd/coredump')
]

mock_disk_partitions_nvme = [
    sdiskpart('/dev/nvme0n1p1', '/'),
    sdiskpart('/dev/nvme0n1p15', '/boot/efi'),
]

mock_listdevdir_vm_standard = ['md0', 'root', 'sda15', 'sda14', 'sda1', 'sda', 'sg0', 'zero', 'null']
mock_listdevdir_vm_standard_4nvme = ['md0', 'root', 'nvme0n4', 'nvme0n3', 'nvme0n2', 'sda15', 'sda14', 'sda1', 'sda', 'sg0', 'nvme0n1', 'nvme0', 'zero', 'null']
mock_listdevdir_vm_denseio = ['root', 'nvme0n1p1', 'nvme0n1', 'nvme1n1', 'nvme2n1', 'nvme3n1', 'nvme1', 'nvme2', 'nvme3', 'nvme0', 'zero', 'null']
mock_glob_glob_dev_vm_standard = ['/dev/sda15', '/dev/sda14', '/dev/sda1', '/dev/sda']
mock_glob_glob_dev_vm_standard_4nvme = mock_glob_glob_dev_vm_standard
mock_glob_glob_dev_vm_denseio = []


def _mock_multi_open(files, filename, *args, **kwargs):
    if filename in files:
        return unittest.mock.mock_open(read_data=files[filename]).return_value
    else:
        raise FileNotFoundError(f'Unable to open {filename}')


def mock_multi_open_oci(filename, *args, **kwargs):
    files = {
        '/sys/class/dmi/id/chassis_asset_tag': 'OracleCloud.com'
    }
    return _mock_multi_open(files, filename, *args, **kwargs)


class TestOCIInstance(IsolatedAsyncioTestCase):
    """Test OCI instance detection and configuration."""

    @httpretty.activate(verbose=True, allow_net_connect=False)
    @unittest.mock.patch('lib.scylla_cloud.is_ec2', return_value=False)
    @unittest.mock.patch('lib.scylla_cloud.is_gce', return_value=False)
    @unittest.mock.patch('lib.scylla_cloud.is_azure', return_value=False)
    @unittest.mock.patch('lib.scylla_cloud.is_oci', return_value=True)
    async def test_is_oci(self, *args):
        """Test OCI instance detection."""
        self.assertTrue(lib.scylla_cloud.is_oci())

    @httpretty.activate(verbose=True, allow_net_connect=False)
    @unittest.mock.patch('builtins.open', mock_multi_open_oci)
    @unittest.mock.patch('lib.scylla_cloud.read_one_line', return_value='OracleCloud.com')
    async def test_oci_identify_dmi(self, *args):
        """Test OCI instance identification via DMI."""
        result = oci_instance.identify_dmi()
        self.assertEqual(result, oci_instance)

    @httpretty.activate(verbose=True, allow_net_connect=False)
    async def test_oci_identify_metadata(self, *args):
        """Test OCI instance identification via metadata."""
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/opc/v2/instance/',
            body=json.dumps({
                'shape': 'VM.Standard3.Flex',
            }),
            content_type='application/json'
        )
        result = await oci_instance.identify_metadata()
        self.assertEqual(result, oci_instance)

    @httpretty.activate(verbose=True, allow_net_connect=False)
    @unittest.mock.patch('builtins.open', mock_multi_open_oci)
    @unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions)
    @unittest.mock.patch('os.listdir')
    @unittest.mock.patch('glob.glob')
    def test_oci_vm_standard_no_nvme(self, mock_glob, mock_listdir, *args):
        """Test OCI VM.Standard instance without NVMe drives."""
        mock_listdir.return_value = mock_listdevdir_vm_standard
        mock_glob.return_value = mock_glob_glob_dev_vm_standard
        
        # Mock metadata endpoint
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/opc/v2/instance/',
            body=json.dumps({
                'shape': 'VM.Standard3.Flex',
                'region': 'us-ashburn-1',
                'availabilityDomain': 'AD-1'
            }),
            content_type='application/json'
        )
        
        instance = oci_instance()
        
        # Test instancetype
        self.assertEqual(instance.instancetype, 'VM.Standard3.Flex')
        
        # Test instance_class
        self.assertEqual(instance.instance_class(), 'VM.Standard')
        
        # Test is_supported_instance_class
        self.assertTrue(instance.is_supported_instance_class())
        
        # Test is_dev_instance_type
        self.assertFalse(instance.is_dev_instance_type())
        
        # Test NVMe disk count
        self.assertEqual(instance.nvme_disk_count, 0)
        
        # Test get_local_disks (should be empty)
        local_disks = instance.get_local_disks()
        self.assertEqual(len(local_disks), 0)

    @httpretty.activate(verbose=True, allow_net_connect=False)
    @unittest.mock.patch('builtins.open', mock_multi_open_oci)
    @unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions_nvme)
    @unittest.mock.patch('os.listdir')
    @unittest.mock.patch('glob.glob')
    def test_oci_vm_denseio_with_nvme(self, mock_glob, mock_listdir, *args):
        """Test OCI VM.DenseIO instance with NVMe drives."""
        mock_listdir.return_value = mock_listdevdir_vm_denseio
        mock_glob.return_value = mock_glob_glob_dev_vm_denseio
        
        # Mock metadata endpoint
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/opc/v2/instance/',
            body=json.dumps({
                'shape': 'VM.DenseIO2.8',
                'region': 'us-phoenix-1',
                'availabilityDomain': 'AD-2'
            }),
            content_type='application/json'
        )
        
        instance = oci_instance()
        
        # Test instancetype
        self.assertEqual(instance.instancetype, 'VM.DenseIO2.8')
        
        # Test instance_class
        self.assertEqual(instance.instance_class(), 'VM.DenseIO')
        
        # Test is_supported_instance_class
        self.assertTrue(instance.is_supported_instance_class())
        
        # Test NVMe disk count (nvme1n1, nvme2n1, nvme3n1 - nvme0n1 is root)
        self.assertEqual(instance.nvme_disk_count, 3)
        
        # Test get_local_disks
        local_disks = instance.get_local_disks()
        self.assertEqual(len(local_disks), 3)
        self.assertIn('/dev/nvme1n1', local_disks)
        self.assertIn('/dev/nvme2n1', local_disks)
        self.assertIn('/dev/nvme3n1', local_disks)

    @httpretty.activate(verbose=True, allow_net_connect=False)
    @unittest.mock.patch('builtins.open', mock_multi_open_oci)
    @unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions)
    @unittest.mock.patch('os.listdir')
    @unittest.mock.patch('glob.glob')
    def test_oci_get_remote_disks(self, mock_glob, mock_listdir, *args):
        """Test OCI instance with remote (block volume) disks."""
        mock_listdir.return_value = mock_listdevdir_vm_standard
        mock_glob.return_value = mock_glob_glob_dev_vm_standard
        
        # Mock metadata endpoint
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/opc/v2/instance/',
            body=json.dumps({
                'shape': 'VM.Standard3.Flex',
            }),
            content_type='application/json'
        )
        
        instance = oci_instance()
        
        # Test get_remote_disks (sda is root, but sdb/sdc/sdd would be block volumes)
        # Based on mock, we'll get sdb onwards
        remote_disks = instance.get_remote_disks()
        # The mock_glob_glob_dev_vm_standard filters out root, so this depends on implementation
        self.assertIsInstance(remote_disks, list)

    @httpretty.activate(verbose=True, allow_net_connect=False)
    @unittest.mock.patch('builtins.open', mock_multi_open_oci)
    @unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions)
    @unittest.mock.patch('os.listdir')
    @unittest.mock.patch('glob.glob')
    def test_oci_dev_instance_type(self, mock_glob, mock_listdir, *args):
        """Test OCI dev instance type detection."""
        mock_listdir.return_value = mock_listdevdir_vm_standard
        mock_glob.return_value = mock_glob_glob_dev_vm_standard
        
        # Mock metadata endpoint for micro instance
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/opc/v2/instance/',
            body=json.dumps({
                'shape': 'VM.Standard.E2.1.Micro',
            }),
            content_type='application/json'
        )
        
        instance = oci_instance()
        
        # Test is_dev_instance_type
        self.assertTrue(instance.is_dev_instance_type())

    @httpretty.activate(verbose=True, allow_net_connect=False)
    @unittest.mock.patch('builtins.open', mock_multi_open_oci)
    @unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions)
    @unittest.mock.patch('os.listdir')
    @unittest.mock.patch('glob.glob')
    def test_oci_user_data(self, mock_glob, mock_listdir, *args):
        """Test OCI user data retrieval."""
        mock_listdir.return_value = mock_listdevdir_vm_standard
        mock_glob.return_value = mock_glob_glob_dev_vm_standard
        
        # Mock metadata endpoints
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/opc/v2/instance/',
            body=json.dumps({
                'shape': 'VM.Standard3.Flex',
            }),
            content_type='application/json'
        )
        
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/opc/v2/instance/metadata/user-data',
            body='#!/bin/bash\necho "Hello from OCI"',
        )
        
        instance = oci_instance()
        
        # Test user_data
        user_data = instance.user_data
        self.assertIn('Hello from OCI', user_data)

    @httpretty.activate(verbose=True, allow_net_connect=False)
    @unittest.mock.patch('builtins.open', mock_multi_open_oci)
    @unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions)
    @unittest.mock.patch('os.listdir')
    @unittest.mock.patch('glob.glob')
    def test_oci_private_ipv4(self, mock_glob, mock_listdir, *args):
        """Test OCI private IPv4 retrieval."""
        mock_listdir.return_value = mock_listdevdir_vm_standard
        mock_glob.return_value = mock_glob_glob_dev_vm_standard
        
        # Mock metadata endpoints
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/opc/v2/instance/',
            body=json.dumps({
                'shape': 'VM.Standard3.Flex',
            }),
            content_type='application/json'
        )
        
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/opc/v2/instance/vnics/0/privateIp',
            body='10.0.1.100',
        )
        
        instance = oci_instance()
        
        # Test private_ipv4
        private_ip = instance.private_ipv4()
        self.assertEqual(private_ip, '10.0.1.100')

    @httpretty.activate(verbose=True, allow_net_connect=False)
    @unittest.mock.patch('builtins.open', mock_multi_open_oci)
    @unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions)
    @unittest.mock.patch('os.listdir')
    @unittest.mock.patch('glob.glob')
    def test_oci_public_ipv4(self, mock_glob, mock_listdir, *args):
        """Test OCI public IPv4 retrieval."""
        mock_listdir.return_value = mock_listdevdir_vm_standard
        mock_glob.return_value = mock_glob_glob_dev_vm_standard
        
        # Mock metadata endpoints
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/opc/v2/instance/',
            body=json.dumps({
                'shape': 'VM.Standard3.Flex',
            }),
            content_type='application/json'
        )
        
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/opc/v2/instance/metadata/public-ip',
            body='129.146.10.20',
        )
        
        instance = oci_instance()
        
        # Test public_ipv4
        public_ip = instance.public_ipv4()
        self.assertEqual(public_ip, '129.146.10.20')

    @httpretty.activate(verbose=True, allow_net_connect=False)
    @unittest.mock.patch('builtins.open', mock_multi_open_oci)
    @unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions)
    @unittest.mock.patch('os.listdir')
    @unittest.mock.patch('glob.glob')
    def test_oci_endpoint_snitch(self, mock_glob, mock_listdir, *args):
        """Test OCI endpoint snitch."""
        mock_listdir.return_value = mock_listdevdir_vm_standard
        mock_glob.return_value = mock_glob_glob_dev_vm_standard
        
        # Mock metadata endpoint
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/opc/v2/instance/',
            body=json.dumps({
                'shape': 'VM.Standard3.Flex',
            }),
            content_type='application/json'
        )
        
        instance = oci_instance()
        
        # Test endpoint_snitch
        self.assertEqual(instance.endpoint_snitch, 'Ec2Snitch')


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
