import sys
import logging
import httpretty
import unittest.mock
import json
import base64
import re
from unittest import TestCase
from subprocess import CalledProcessError
from collections import namedtuple
from socket import AddressFamily, SocketKind
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from lib.scylla_cloud import azure_instance
import lib.scylla_cloud

LOGGER = logging.getLogger(__name__)

svmem = namedtuple('svmem', ['total'])

sdiskpart = namedtuple('sdiskpart', ['device', 'mountpoint'])
mock_disk_partitions = [
    sdiskpart('/dev/root', '/'),
    sdiskpart('/dev/sda15', '/boot/efi'),
    sdiskpart('/dev/md0', '/var/lib/scylla'),
    sdiskpart('/dev/md0', '/var/lib/systemd/coredump'),
    sdiskpart('/dev/sdb1', '/mnt')
]
mock_disk_partitions_noswap = [
    sdiskpart('/dev/root', '/'),
    sdiskpart('/dev/sda15', '/boot/efi'),
    sdiskpart('/dev/md0', '/var/lib/scylla'),
    sdiskpart('/dev/md0', '/var/lib/systemd/coredump')
]

mock_listdevdir_standard_l16s_v2 = ['sdb1', 'root', 'ng1n1', 'nvme1n1', 'ng0n1', 'nvme0n1', 'sdb', 'sg1', 'sda15', 'sda14', 'sda1', 'sda', 'sg0', 'nvme1', 'nvme0', 'zero', 'null']
mock_listdevdir_standard_l16s_v2_noswap = ['root', 'ng1n1', 'nvme1n1', 'ng0n1', 'nvme0n1', 'sda15', 'sda14', 'sda1', 'sda', 'sg0', 'nvme1', 'nvme0', 'zero', 'null']
mock_listdevdir_standard_l32s_v2 = ['sdb1', 'root', 'ng2n1', 'nvme2n1', 'ng3n1', 'nvme3n1', 'ng0n1', 'nvme0n1', 'ng1n1', 'nvme1n1','sdb',  'sda15', 'sda14', 'sda1', 'sda', 'sg1', 'sg0', 'nvme3', 'nvme2', 'nvme1', 'nvme0', 'zero', 'null']
mock_listdevdir_standard_l16s_v2_2persistent = ['sdb1', 'root', 'ng1n1', 'ng0n1', 'nvme0n1', 'nvme1n1', 'sdd', 'sg4', 'sdc', 'sg3', 'sdb', 'sda15', 'sda14', 'sda1', 'sda', 'sg1', 'sg0', 'nvme1', 'nvme0', 'zero', 'null']
mock_listdevdir_standard_l16s_v2_2persistent_noswap = ['root', 'ng1n1', 'ng0n1', 'nvme0n1', 'nvme1n1', 'sdc', 'sg3', 'sdb', 'sda15', 'sda14', 'sda1', 'sda', 'sg1', 'sg0', 'nvme1', 'nvme0', 'zero', 'null']
mock_glob_glob_dev_standard_l16s_v2 = ['/dev/sdb1', '/dev/sdb', '/dev/sda15', '/dev/sda14', '/dev/sda1', '/dev/sda']
mock_glob_glob_dev_standard_l16s_v2_noswap = ['/dev/sda15', '/dev/sda14', '/dev/sda1', '/dev/sda']
mock_glob_glob_dev_standard_l16s_v2_2persistent = ['/dev/sdb1', '/dev/sdd', '/dev/sdc', '/dev/sdb', '/dev/sda15', '/dev/sda14', '/dev/sda1', '/dev/sda']
mock_glob_glob_dev_standard_l16s_v2_2persistent_noswap = ['/dev/sdc', '/dev/sdb', '/dev/sda15', '/dev/sda14', '/dev/sda1', '/dev/sda']
mock_glob_glob_dev_standard_l32s_v2 = mock_glob_glob_dev_standard_l16s_v2

class TestAzureInstance(TestCase):
    def setUp(self):
        httpretty.enable(verbose=True, allow_net_connect=False)

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    def httpretty_azure_metadata(self, instance_type='Standard_L16s_v2', with_userdata=False):
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/metadata/instance?api-version=2021-01-01&format=text',
            '''
compute/
network/
'''[1:-1]
        )
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/metadata/instance/compute/vmSize?api-version=2021-01-01&format=text',
            instance_type
        )
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/metadata/instance/compute/zone?api-version=2021-01-01&format=text',
            ''
        )
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/metadata/instance/compute/location?api-version=2021-01-01&format=text',
            'eastus'
        )
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/metadata/instance/network/interface/0/ipv4/ipAddress/0/privateIpAddress?api-version=2021-01-01&format=text',
            '172.16.0.1'
        )
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/metadata/instance/compute/userData?api-version=2021-01-01&format=text',
            base64.b64encode(b'{"scylla_yaml": {"cluster_name": "test-cluster"}}') if with_userdata else ''
        )

    def httpretty_no_azure_metadata(self):
        httpretty.register_uri(
            httpretty.GET,
            re.compile('http://.*'),
            '',
            status=404
        )

    def test_is_azure_instance(self):
        self.httpretty_azure_metadata()
        assert azure_instance.is_azure_instance()

    # XXX: Seems like Github Actions is running in Azure, we cannot disable
    # httpretty here (it suceeded to connect metadata server even we disabled
    # httpretty)
    def test_is_not_azure_instance(self):
        self.httpretty_no_azure_metadata()
        assert not azure_instance.is_azure_instance()

    def test_endpoint_snitch(self):
        self.httpretty_azure_metadata()
        ins = azure_instance()
        assert ins.endpoint_snitch == 'AzureSnitch'

    def test_getting_started_url(self):
        self.httpretty_azure_metadata()
        ins = azure_instance()
        assert ins.getting_started_url == 'http://www.scylladb.com/doc/getting-started-azure/'

    def test_instancelocation_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = azure_instance()
        assert ins.instancelocation == 'eastus'

    def test_instancezone_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = azure_instance()
        assert ins.instancezone == ''

    def test_instancetype_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = azure_instance()
        assert ins.instancetype == 'Standard_L16s_v2'

    def test_instancetype_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type='Standard_L32s_v2')
        ins = azure_instance()
        assert ins.instancetype == 'Standard_L32s_v2'

    def test_cpu_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = azure_instance()
        with unittest.mock.patch('psutil.cpu_count', return_value=16):
            assert ins.cpu == 16

    def test_cpu_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type='Standard_L32s_v2')
        ins = azure_instance()
        with unittest.mock.patch('psutil.cpu_count', return_value=32):
            assert ins.cpu == 32

    def test_memoryGB_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = azure_instance()
        # XXX: the value is little bit less than 128GB
        with unittest.mock.patch('psutil.virtual_memory', return_value=svmem(135185641472)):
            assert ins.memoryGB > 125 and ins.memoryGB <= 128

    def test_memoryGB_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type='Standard_L32s_v2')
        ins = azure_instance()
        # XXX: the value is little bit less than 256GB
        with unittest.mock.patch('psutil.virtual_memory', return_value=svmem(270471569408)):
            assert ins.memoryGB > 251 and ins.memoryGB <= 254

    def test_instance_purpose_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = azure_instance()
        assert ins.instance_purpose() == 'Standard'

    def test_instance_purpose_basic_a0(self):
        self.httpretty_azure_metadata(instance_type='Basic_A0')
        ins = azure_instance()
        assert ins.instance_purpose() == 'Basic'

    def test_instance_class_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = azure_instance()
        assert ins.instance_class() == 'L16s'

    def test_instance_class_standard_ds1_v2(self):
        self.httpretty_azure_metadata(instance_type='Standard_DS1_v2')
        ins = azure_instance()
        assert ins.instance_class() == 'DS1'

    def test_is_supported_instance_class_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = azure_instance()
        assert ins.is_supported_instance_class()

    def test_is_not_supported_instance_class_standard_ds1_v2(self):
        self.httpretty_azure_metadata(instance_type='Standard_DS1_v2')
        ins = azure_instance()
        assert not ins.is_supported_instance_class()

    def test_is_recommended_instance_size_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = azure_instance()
        with unittest.mock.patch('psutil.cpu_count', return_value=16):
            assert ins.is_recommended_instance_size()

    def test_is_recommended_instance_size_standard_ds1_v2(self):
        self.httpretty_azure_metadata(instance_type='Standard_DS1_v2')
        ins = azure_instance()
        with unittest.mock.patch('psutil.cpu_count', return_value=1):
            assert not ins.is_recommended_instance_size()

    def test_is_recommended_instance_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = azure_instance()
        with unittest.mock.patch('psutil.cpu_count', return_value=16):
            assert ins.is_recommended_instance()

    def test_is_not_recommended_instance_standard_ds1_v2(self):
        self.httpretty_azure_metadata(instance_type='Standard_DS1_v2')
        ins = azure_instance()
        with unittest.mock.patch('psutil.cpu_count', return_value=1):
            assert not ins.is_recommended_instance()

    def test_private_ipv4(self):
        self.httpretty_azure_metadata()
        ins = azure_instance()
        assert ins.private_ipv4() == '172.16.0.1'

    def test_user_data(self):
        self.httpretty_azure_metadata(with_userdata=True)
        ins = azure_instance()
        assert ins.user_data == '{"scylla_yaml": {"cluster_name": "test-cluster"}}'

    def test_no_user_data(self):
        self.httpretty_azure_metadata()
        ins = azure_instance()
        assert ins.user_data == ''

    def test_non_root_nvmes_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l16s_v2):
            ins = azure_instance()
            assert ins._non_root_nvmes() == {'root': ['/dev/root'], 'ephemeral': ['nvme1n1', 'nvme0n1']}

    def test_non_root_nvmes_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type='Standard_L32s_v2')
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l32s_v2):
            ins = azure_instance()
            assert ins._non_root_nvmes() == {'root': ['/dev/root'], 'ephemeral': ['nvme2n1', 'nvme3n1', 'nvme0n1', 'nvme1n1']}

    def test_non_root_disks_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2),\
                unittest.mock.patch('os.path.exists', return_value=True),\
                unittest.mock.patch('os.path.realpath', return_value='/dev/sdb'):
            ins = azure_instance()
            assert ins._non_root_disks() == {'persistent': [], 'swap': ['sdb']}

    def test_non_root_disks_standard_l16s_v2_noswap(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions_noswap),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2_noswap),\
                unittest.mock.patch('os.path.exists', return_value=False):
            ins = azure_instance()
            assert ins._non_root_disks() == {'persistent': [], 'swap': []}

    def test_non_root_disks_standard_l16s_v2_2persistent(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent),\
                unittest.mock.patch('os.path.exists', return_value=True),\
                unittest.mock.patch('os.path.realpath', return_value='/dev/sdb'):
            ins = azure_instance()
            assert ins._non_root_disks() == {'persistent': ['sdd', 'sdc'], 'swap': ['sdb']}

    def test_non_root_disks_standard_l16s_v2_2persistent_noswap(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions_noswap),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent_noswap),\
                unittest.mock.patch('os.path.exists', return_value=False):
            ins = azure_instance()
            assert ins._non_root_disks() == {'persistent': ['sdc', 'sdb'], 'swap': []}

    def test_non_root_disks_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type='Standard_L32s_v2')
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l32s_v2),\
                unittest.mock.patch('os.path.exists', return_value=True),\
                unittest.mock.patch('os.path.realpath', return_value='/dev/sdb'):
            ins = azure_instance()
            assert ins._non_root_disks() == {'persistent': [], 'swap': ['sdb']}

    def test_os_disks_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l16s_v2),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2),\
                unittest.mock.patch('os.path.exists', return_value=True),\
                unittest.mock.patch('os.path.realpath', return_value='/dev/sdb'):
            ins = azure_instance()
            assert ins.os_disks == {'root': ['/dev/root'], 'ephemeral': ['nvme1n1', 'nvme0n1'], 'persistent': [], 'swap': ['sdb']}

    def test_os_disks_standard_l16s_v2_noswap(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions_noswap),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l16s_v2_noswap),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2_noswap),\
                unittest.mock.patch('os.path.exists', return_value=False):
            ins = azure_instance()
            assert ins.os_disks == {'root': ['/dev/root'], 'ephemeral': ['nvme1n1', 'nvme0n1'], 'persistent': [], 'swap': []}

    def test_os_disks_standard_l16s_v2_2persistent(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l16s_v2_2persistent),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent),\
                unittest.mock.patch('os.path.exists', return_value=True),\
                unittest.mock.patch('os.path.realpath', return_value='/dev/sdb'):
            ins = azure_instance()
            assert ins.os_disks == {'root': ['/dev/root'], 'ephemeral': ['nvme0n1', 'nvme1n1'], 'persistent': ['sdd', 'sdc'], 'swap': ['sdb']}

    def test_os_disks_standard_l16s_v2_2persistent_noswap(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions_noswap),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l16s_v2_2persistent_noswap),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent_noswap),\
                unittest.mock.patch('os.path.exists', return_value=False):
            ins = azure_instance()
            assert ins.os_disks == {'root': ['/dev/root'], 'ephemeral': ['nvme0n1', 'nvme1n1'], 'persistent': ['sdc', 'sdb'], 'swap': []}

    def test_os_disks_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type='Standard_L32s_v2')
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l32s_v2),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l32s_v2),\
                unittest.mock.patch('os.path.exists', return_value=True),\
                unittest.mock.patch('os.path.realpath', return_value='/dev/sdb'):
            ins = azure_instance()
            assert ins.os_disks == {'root': ['/dev/root'], 'ephemeral': ['nvme2n1', 'nvme3n1', 'nvme0n1', 'nvme1n1'], 'persistent': [], 'swap': ['sdb']}

    def test_get_local_disks_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l16s_v2),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2),\
                unittest.mock.patch('os.path.exists', return_value=True),\
                unittest.mock.patch('os.path.realpath', return_value='/dev/sdb'):
            ins = azure_instance()
            assert ins.get_local_disks() ==  ['nvme1n1', 'nvme0n1']

    def test_get_local_disks_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type='Standard_L32s_v2')
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l32s_v2),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l32s_v2),\
                unittest.mock.patch('os.path.exists', return_value=True),\
                unittest.mock.patch('os.path.realpath', return_value='/dev/sdb'):
            ins = azure_instance()
            assert ['nvme2n1', 'nvme3n1', 'nvme0n1', 'nvme1n1']

    def test_get_remote_disks_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l16s_v2),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2),\
                unittest.mock.patch('os.path.exists', return_value=True),\
                unittest.mock.patch('os.path.realpath', return_value='/dev/sdb'):
            ins = azure_instance()
            assert ins.get_remote_disks() == []

    def test_get_remote_disks_standard_l16s_v2_noswap(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions_noswap),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l16s_v2_noswap),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2_noswap),\
                unittest.mock.patch('os.path.exists', return_value=False):
            ins = azure_instance()
            assert ins.get_remote_disks() == []

    def test_get_remote_disks_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type='Standard_L32s_v2')
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l32s_v2),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l32s_v2),\
                unittest.mock.patch('os.path.exists', return_value=True),\
                unittest.mock.patch('os.path.realpath', return_value='/dev/sdb'):
            ins = azure_instance()
            assert ins.get_remote_disks() == []

    def test_get_remote_disks_standard_l16s_v2_2persistent(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l16s_v2_2persistent),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent),\
                unittest.mock.patch('os.path.exists', return_value=True),\
                unittest.mock.patch('os.path.realpath', return_value='/dev/sdb'):
            ins = azure_instance()
            assert ins.get_remote_disks() == ['sdd', 'sdc']

    def test_get_remote_disks_standard_l16s_v2_2persistent_noswap(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions_noswap),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l16s_v2_2persistent_noswap),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent_noswap),\
                unittest.mock.patch('os.path.exists', return_value=False):
            ins = azure_instance()
            assert ins.get_remote_disks() == ['sdc', 'sdb']

    def test_get_swap_disks_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l16s_v2),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2),\
                unittest.mock.patch('os.path.exists', return_value=True),\
                unittest.mock.patch('os.path.realpath', return_value='/dev/sdb'):
            ins = azure_instance()
            assert ins.get_swap_disks() == ['sdb']

    def test_get_swap_disks_standard_l16s_v2_noswap(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions_noswap),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l16s_v2_noswap),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2_noswap),\
                unittest.mock.patch('os.path.exists', return_value=False):
            ins = azure_instance()
            assert ins.get_swap_disks() == []

    def test_get_swap_disks_standard_l16s_v2_2persistent(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l16s_v2_2persistent),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent),\
                unittest.mock.patch('os.path.exists', return_value=True),\
                unittest.mock.patch('os.path.realpath', return_value='/dev/sdb'):
            ins = azure_instance()
            assert ins.get_swap_disks() == ['sdb']

    def test_get_swap_disks_standard_l16s_v2_2persistent_noswap(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions_noswap),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l16s_v2_2persistent_noswap),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2_2persistent_noswap),\
                unittest.mock.patch('os.path.exists', return_value=False):
            ins = azure_instance()
            assert ins.get_swap_disks() == []

    def test_get_nvme_disks_count_from_metadata_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        ins = azure_instance()
        assert ins._azure_instance__get_nvme_disks_count_from_metadata() == 2

    def test_get_nvme_disks_count_from_metadata_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type='Standard_L32s_v2')
        ins = azure_instance()
        assert ins._azure_instance__get_nvme_disks_count_from_metadata() == 4

    def test_nvme_disk_count_standard_l16s_v2(self):
        self.httpretty_azure_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l16s_v2),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l16s_v2):
            ins = azure_instance()
            assert ins.nvme_disk_count == 2

    def test_nvme_disk_count_standard_l32s_v2(self):
        self.httpretty_azure_metadata(instance_type='Standard_L32s_v2')
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
                unittest.mock.patch('os.listdir', return_value=mock_listdevdir_standard_l32s_v2),\
                unittest.mock.patch('glob.glob', return_value=mock_glob_glob_dev_standard_l32s_v2):
            ins = azure_instance()
            assert ins.nvme_disk_count == 4
