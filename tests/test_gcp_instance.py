import sys
import logging
import httpretty
import unittest.mock
import json
from unittest import TestCase
from subprocess import CalledProcessError
from collections import namedtuple
from socket import AddressFamily, SocketKind

sys.path.append('..')
from lib.scylla_cloud import gcp_instance
import lib.scylla_cloud

LOGGER = logging.getLogger(__name__)

svmem = namedtuple('svmem', ['total'])

sdiskpart = namedtuple('sdiskpart', ['device', 'mountpoint'])
mock_disk_partitions = [
    sdiskpart('/dev/root', '/'),
    sdiskpart('/dev/sda15', '/boot/efi'),
    sdiskpart('/dev/md0', '/var/lib/scylla'),
    sdiskpart('/dev/md0', '/var/lib/systemd/coredump')
]

mock_listdevdir_n2_standard_8 = ['md0', 'root', 'sda15', 'sda14', 'sda1', 'sda', 'sg0', 'zero', 'null']
mock_listdevdir_n2_standard_8_4ssd = ['md0', 'root', 'nvme0n4', 'nvme0n3', 'nvme0n2', 'sda15', 'sda14', 'sda1', 'sda', 'sg0', 'nvme0n1', 'nvme0', 'zero', 'null']
mock_listdevdir_n2_highcpu_8_4ssd = mock_listdevdir_n2_standard_8_4ssd
mock_listdevdir_n2_standard_8_24ssd = ['md0', 'root', 'nvme0n24', 'nvme0n23', 'nvme0n22', 'nvme0n21', 'nvme0n20', 'nvme0n19', 'nvme0n18', 'nvme0n17', 'nvme0n16', 'nvme0n15', 'nvme0n14', 'nvme0n13', 'nvme0n12', 'nvme0n11', 'nvme0n10', 'nvme0n9', 'nvme0n8', 'nvme0n7', 'nvme0n6', 'nvme0n5', 'nvme0n4', 'nvme0n3', 'nvme0n2', 'nvme0n1', 'sda15', 'sda14', 'sda1', 'sda', 'sg0','nvme0', 'zero', 'null']
mock_listdevdir_n2_standard_8_4ssd_2persistent = ['sdc', 'sg2', 'sdb', 'sg1', 'md0', 'root', 'nvme0n4', 'nvme0n3', 'nvme0n2', 'sda15', 'sda14', 'sda1', 'sda', 'sg0', 'nvme0n1', 'nvme0', 'zero', 'null']
mock_glob_glob_dev_n2_standard_8 = ['/dev/sda15', '/dev/sda14', '/dev/sda1', '/dev/sda']
mock_glob_glob_dev_n2_standard_8_4ssd = mock_glob_glob_dev_n2_standard_8
mock_glob_glob_dev_n2_standard_8_24ssd = mock_glob_glob_dev_n2_standard_8
mock_glob_glob_dev_n2_highcpu_8_4ssd = mock_glob_glob_dev_n2_standard_8
mock_glob_glob_dev_n2_standard_8_4ssd_2persistent = ['/dev/sdc', '/dev/sdb', '/dev/sda15', '/dev/sda14', '/dev/sda1', '/dev/sda']

class TestGcpInstance(TestCase):
    def setUp(self):
        httpretty.enable(verbose=True, allow_net_connect=False)

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    def httpretty_gcp_metadata(self, instance_type='n2-standard-8', project_number='431729375847', instance_name='testcase_1', num_local_disks=4, num_remote_disks=0, with_userdata=False):
        httpretty.register_uri(
            httpretty.GET,
            'http://metadata.google.internal/computeMetadata/v1/instance/machine-type?recursive=false',
            f'projects/{project_number}/machineTypes/{instance_type}'
        )
        httpretty.register_uri(
            httpretty.GET,
            'http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/ip?recursive=false',
            '172.16.0.1'
        )
        disks = []
        i = 0
        disks.append({"deviceName": instance_name, "index": i, "interface": "SCSI", "mode": "READ_WRITE", "type": "PERSISTENT-BALANCED"})
        i += 1
        for j in range(num_local_disks):
            disks.append({"deviceName": f"local-ssd-{j}", "index": i, "interface": "NVME", "mode": "READ_WRITE", "type": "LOCAL-SSD"})
            i += 1
        for j in range(num_remote_disks):
            disks.append({"deviceName": f"disk-{j}", "index": i, "interface": "SCSI", "mode": "READ_WRITE", "type": "PERSISTENT-BALANCED"})
            i += 1
        httpretty.register_uri(
            httpretty.GET,
            'http://metadata.google.internal/computeMetadata/v1/instance/disks?recursive=true',
            json.dumps(disks)
        )
        if not with_userdata:
            httpretty.register_uri(
                httpretty.GET,
                'http://metadata.google.internal/computeMetadata/v1/instance/attributes/user-data?recursive=false',
                status = 404
            )
        else:
            httpretty.register_uri(
                httpretty.GET,
                'http://metadata.google.internal/computeMetadata/v1/instance/attributes/user-data?recursive=false',
                '{"scylla_yaml": {"cluster_name": "test-cluster"}}'
            )


    def test_is_gce_instance(self):
        self.httpretty_gcp_metadata()
        with unittest.mock.patch('socket.getaddrinfo', return_value=[(AddressFamily.AF_INET, SocketKind.SOCK_STREAM, 6, '', ('169.254.169.254', 80))]):
            assert gcp_instance.is_gce_instance()

    def test_is_not_gce_instance(self):
        assert not gcp_instance.is_gce_instance()

    def test_endpoint_snitch(self):
        self.httpretty_gcp_metadata()
        ins = gcp_instance()
        assert ins.endpoint_snitch == 'GoogleCloudSnitch'

    def test_getting_started_url(self):
        self.httpretty_gcp_metadata()
        ins = gcp_instance()
        assert ins.getting_started_url == 'http://www.scylladb.com/doc/getting-started-google/'

    def test_instancetype_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = gcp_instance()
        assert ins.instancetype == 'n2-standard-8'

    def test_instancetype_n2d_highmem_4(self):
        self.httpretty_gcp_metadata(instance_type='n2d-highmem-4')
        ins = gcp_instance()
        assert ins.instancetype == 'n2d-highmem-4'

    def test_instancetype_e2_micro(self):
        self.httpretty_gcp_metadata(instance_type='e2-micro')
        ins = gcp_instance()
        assert ins.instancetype == 'e2-micro'

    def test_cpu_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = gcp_instance()
        with unittest.mock.patch('psutil.cpu_count', return_value=8):
            assert ins.cpu == 8

    def test_cpu_n2d_highmem_4(self):
        self.httpretty_gcp_metadata(instance_type='n2d-highmem-4')
        ins = gcp_instance()
        with unittest.mock.patch('psutil.cpu_count', return_value=4):
            assert ins.cpu == 4

    def test_memoryGB_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = gcp_instance()
        # XXX: the value is little bit less than 32GB
        with unittest.mock.patch('psutil.virtual_memory', return_value=svmem(33663647744)):
            assert ins.memoryGB > 31 and ins.memoryGB <= 32

    def test_memoryGB_n2d_highmem_4(self):
        self.httpretty_gcp_metadata(instance_type='n2d-highmem-4')
        ins = gcp_instance()
        # XXX: the value is little bit less than 32GB
        with unittest.mock.patch('psutil.virtual_memory', return_value=svmem(33664700416)):
            assert ins.memoryGB > 31 and ins.memoryGB <= 32

    def test_memoryGB_n1_standard_1(self):
        self.httpretty_gcp_metadata(instance_type='n1-standard-1')
        ins = gcp_instance()
        # XXX: the value is little bit less than 3.75GB
        with unittest.mock.patch('psutil.virtual_memory', return_value=svmem(3850301440)):
            assert ins.memoryGB > 3 and ins.memoryGB < 4

    def test_instance_size_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = gcp_instance()
        assert ins.instance_size() == '8'

    def test_instance_size_n2d_highmem_4(self):
        self.httpretty_gcp_metadata(instance_type='n2d-highmem-4')
        ins = gcp_instance()
        assert ins.instance_size() == '4'

    def test_instance_size_e2_micro(self):
        self.httpretty_gcp_metadata(instance_type='e2-micro')
        ins = gcp_instance()
        assert not ins.instance_size()

    def test_instance_class_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = gcp_instance()
        assert ins.instance_class() == 'n2'

    def test_instance_class_n2d_highmem_4(self):
        self.httpretty_gcp_metadata(instance_type='n2d-highmem-4')
        ins = gcp_instance()
        assert ins.instance_class() == 'n2d'

    def test_instance_class_e2_micro(self):
        self.httpretty_gcp_metadata(instance_type='e2-micro')
        ins = gcp_instance()
        assert ins.instance_class() == 'e2'

    def test_instance_purpose_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = gcp_instance()
        assert ins.instance_purpose() == 'standard'

    def test_instance_purpose_n2d_highmem_4(self):
        self.httpretty_gcp_metadata(instance_type='n2d-highmem-4')
        ins = gcp_instance()
        assert ins.instance_purpose() == 'highmem'

    def test_instance_purpose_e2_micro(self):
        self.httpretty_gcp_metadata(instance_type='e2-micro')
        ins = gcp_instance()
        assert ins.instance_purpose() == 'micro'

    def test_is_not_unsupported_instance_class_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = gcp_instance()
        assert not ins.is_unsupported_instance_class()

    def test_is_not_unsupported_instance_class_n2d_highmem_4(self):
        self.httpretty_gcp_metadata(instance_type='n2d-highmem-4')
        ins = gcp_instance()
        assert not ins.is_unsupported_instance_class()

    def test_is_unsupported_instance_class_e2_micro(self):
        self.httpretty_gcp_metadata(instance_type='e2-micro')
        ins = gcp_instance()
        assert ins.is_unsupported_instance_class()

    def test_is_not_unsupported_instance_class_m1_megamem_96(self):
        self.httpretty_gcp_metadata(instance_type='m1-megamem-96')
        ins = gcp_instance()
        assert not ins.is_unsupported_instance_class()

    def test_is_supported_instance_class_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = gcp_instance()
        assert ins.is_supported_instance_class()

    def test_is_supported_instance_class_n2d_highmem_4(self):
        self.httpretty_gcp_metadata(instance_type='n2d-highmem-4')
        ins = gcp_instance()
        assert ins.is_supported_instance_class()

    def test_is_not_supported_instance_class_e2_micro(self):
        self.httpretty_gcp_metadata(instance_type='e2-micro')
        ins = gcp_instance()
        assert not ins.is_supported_instance_class()

    def test_is_supported_instance_class_m1_megamem_96(self):
        self.httpretty_gcp_metadata(instance_type='m1-megamem-96')
        ins = gcp_instance()
        assert ins.is_supported_instance_class()

    def test_is_recommended_instance_size_n2_standard_8(self):
        self.httpretty_gcp_metadata()
        ins = gcp_instance()
        assert ins.is_recommended_instance_size()

    def test_is_not_recommended_instance_size_n1_standard_1(self):
        self.httpretty_gcp_metadata(instance_type='n1-standard-1')
        ins = gcp_instance()
        assert not ins.is_recommended_instance_size()

    # Unsupported class, but recommended size
    def test_is_recommended_instance_size_e2_standard_8(self):
        self.httpretty_gcp_metadata(instance_type='e2-standard-8')
        ins = gcp_instance()
        assert ins.is_recommended_instance_size()

    def test_private_ipv4(self):
        self.httpretty_gcp_metadata()
        ins = gcp_instance()
        assert ins.private_ipv4() == '172.16.0.1'

    def test_user_data(self):
        self.httpretty_gcp_metadata(with_userdata=True)
        ins = gcp_instance()
        assert ins.user_data == '{"scylla_yaml": {"cluster_name": "test-cluster"}}'

    def test_no_user_data(self):
        self.httpretty_gcp_metadata()
        ins = gcp_instance()
        assert ins.user_data == ''

    def test_non_root_nvmes_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_n2_standard_8_4ssd):
            ins = gcp_instance()
            assert ins._non_root_nvmes() == {'root': ['/dev/root'], 'ephemeral': ['nvme0n4', 'nvme0n3', 'nvme0n2', 'nvme0n1']}

    def test_non_root_nvmes_n2_standard_8_4ssd_2persistent(self):
        self.httpretty_gcp_metadata(num_remote_disks=2)
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_n2_standard_8_4ssd_2persistent):
            ins = gcp_instance()
            assert ins._non_root_nvmes() == {'root': ['/dev/root'], 'ephemeral': ['nvme0n4', 'nvme0n3', 'nvme0n2', 'nvme0n1']}

    def test_non_root_disks_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_n2_standard_8_4ssd):
            ins = gcp_instance()
            assert ins._non_root_disks() == {'persistent': []}

    def test_non_root_disks_n2_standard_8_4ssd_2persistent(self):
        self.httpretty_gcp_metadata(num_remote_disks=2)
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_n2_standard_8_4ssd_2persistent):
            ins = gcp_instance()
            assert ins._non_root_disks() == {'persistent': ['sdc','sdb']}

    def test_os_disks_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_n2_standard_8_4ssd),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_n2_standard_8_4ssd):
            ins = gcp_instance()
            assert ins.os_disks ==  {'root': ['/dev/root'], 'ephemeral': ['nvme0n4', 'nvme0n3', 'nvme0n2', 'nvme0n1'], 'persistent': []}

    def test_os_disks_n2_standard_8_4ssd_2persistent(self):
        self.httpretty_gcp_metadata(num_remote_disks=2)
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_n2_standard_8_4ssd_2persistent),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_n2_standard_8_4ssd_2persistent):
            ins = gcp_instance()
            assert ins.os_disks ==  {'root': ['/dev/root'], 'ephemeral': ['nvme0n4', 'nvme0n3', 'nvme0n2', 'nvme0n1'], 'persistent': ['sdc','sdb']}

    def test_get_local_disks_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_n2_standard_8_4ssd),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_n2_standard_8_4ssd):
            ins = gcp_instance()
            assert ins.get_local_disks() == ['nvme0n4', 'nvme0n3', 'nvme0n2', 'nvme0n1']

    def test_get_local_disks_n2_standard_8_4ssd_2persistent(self):
        self.httpretty_gcp_metadata(num_remote_disks=2)
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_n2_standard_8_4ssd_2persistent),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_n2_standard_8_4ssd_2persistent):
            ins = gcp_instance()
            assert ins.get_local_disks() == ['nvme0n4', 'nvme0n3', 'nvme0n2', 'nvme0n1']

    def test_get_remote_disks_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_n2_standard_8_4ssd),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_n2_standard_8_4ssd):
            ins = gcp_instance()
            assert ins.get_remote_disks() == []

    def test_get_remote_disks_n2_standard_8_4ssd_2persistent(self):
        self.httpretty_gcp_metadata(num_remote_disks=2)
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_n2_standard_8_4ssd_2persistent),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_n2_standard_8_4ssd_2persistent):
            ins = gcp_instance()
            assert ins.get_remote_disks() == ['sdc','sdb']

    def test_get_nvme_disks_from_metadata_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        ins = gcp_instance()
        assert ins._gcp_instance__get_nvme_disks_from_metadata() == [{'deviceName': 'local-ssd-0', 'index': 1, 'interface': 'NVME', 'mode': 'READ_WRITE', 'type': 'LOCAL-SSD'}, {'deviceName': 'local-ssd-1', 'index': 2, 'interface': 'NVME', 'mode': 'READ_WRITE', 'type': 'LOCAL-SSD'}, {'deviceName': 'local-ssd-2', 'index': 3, 'interface': 'NVME', 'mode': 'READ_WRITE', 'type': 'LOCAL-SSD'}, {'deviceName': 'local-ssd-3', 'index': 4, 'interface': 'NVME', 'mode': 'READ_WRITE', 'type': 'LOCAL-SSD'}]

    def test_get_nvme_disks_from_metadata_n2_standard_8_4ssd_2persistent(self):
        self.httpretty_gcp_metadata(num_remote_disks=2)
        ins = gcp_instance()
        assert ins._gcp_instance__get_nvme_disks_from_metadata() == [{'deviceName': 'local-ssd-0', 'index': 1, 'interface': 'NVME', 'mode': 'READ_WRITE', 'type': 'LOCAL-SSD'}, {'deviceName': 'local-ssd-1', 'index': 2, 'interface': 'NVME', 'mode': 'READ_WRITE', 'type': 'LOCAL-SSD'}, {'deviceName': 'local-ssd-2', 'index': 3, 'interface': 'NVME', 'mode': 'READ_WRITE', 'type': 'LOCAL-SSD'}, {'deviceName': 'local-ssd-3', 'index': 4, 'interface': 'NVME', 'mode': 'READ_WRITE', 'type': 'LOCAL-SSD'}]

    def test_nvme_disk_count_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_n2_standard_8_4ssd),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_n2_standard_8_4ssd):
            ins = gcp_instance()
            assert ins.nvme_disk_count == 4

    def test_nvme_disk_count_n2_standard_8_4ssd_2persistent(self):
        self.httpretty_gcp_metadata(num_remote_disks=2)
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_n2_standard_8_4ssd_2persistent),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_n2_standard_8_4ssd_2persistent):
            ins = gcp_instance()
            assert ins.nvme_disk_count == 4

    def test_firstNvmeSize_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_n2_standard_8_4ssd),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_n2_standard_8_4ssd),\
                unittest.mock.patch('lib.scylla_cloud.gcp_instance.get_file_size_by_seek', return_value=402653184000):
            ins = gcp_instance()
            assert ins.firstNvmeSize == 375.0

    def test_is_recommended_instance_n2_standard_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with unittest.mock.patch('psutil.cpu_count', return_value=8),\
                unittest.mock.patch('psutil.virtual_memory', return_value=svmem(33663647744)),\
                unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_n2_standard_8_4ssd),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_n2_standard_8_4ssd),\
                unittest.mock.patch('lib.scylla_cloud.gcp_instance.get_file_size_by_seek', return_value=402653184000):
            ins = gcp_instance()
            assert ins.is_recommended_instance() == True

    def test_is_not_recommended_instance_n2_highcpu_8_4ssd(self):
        self.httpretty_gcp_metadata()
        with unittest.mock.patch('psutil.cpu_count', return_value=8),\
                unittest.mock.patch('psutil.virtual_memory', return_value=svmem(8334258176)),\
                unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_n2_highcpu_8_4ssd),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_n2_highcpu_8_4ssd),\
                unittest.mock.patch('lib.scylla_cloud.gcp_instance.get_file_size_by_seek', return_value=402653184000):
            ins = gcp_instance()
            # Not enough memory
            assert ins.is_recommended_instance() == False

    def test_is_not_recommended_instance_n2_standard_8_24ssd(self):
        self.httpretty_gcp_metadata(num_local_disks=24)
        with unittest.mock.patch('psutil.cpu_count', return_value=8),\
                unittest.mock.patch('psutil.virtual_memory', return_value=svmem(33663647744)),\
                unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('lib.scylla_cloud.gcp_instance.get_file_size_by_seek', return_value=402653184000):
            ins = gcp_instance()
            # Requires more CPUs to use this number of SSDs
            assert ins.is_recommended_instance() == False

    def test_is_not_recommended_instance_n2_standard_8(self):
        self.httpretty_gcp_metadata(num_local_disks=0)
        with unittest.mock.patch('psutil.cpu_count', return_value=8),\
                unittest.mock.patch('psutil.virtual_memory', return_value=svmem(33663647744)),\
                unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_n2_standard_8),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_n2_standard_8),\
                unittest.mock.patch('lib.scylla_cloud.gcp_instance.get_file_size_by_seek', return_value=402653184000):
            ins = gcp_instance()
            # No SSD
            assert ins.is_recommended_instance() == False
