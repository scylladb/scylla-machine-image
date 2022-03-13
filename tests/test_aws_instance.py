import sys
import logging
import httpretty
import unittest.mock
from unittest import TestCase
from subprocess import CalledProcessError
from collections import namedtuple

sys.path.append('..')
from lib.scylla_cloud import aws_instance
import lib.scylla_cloud

LOGGER = logging.getLogger(__name__)

def _mock_multi_open(files, filename, *args, **kwargs):
    if filename in files:
        return unittest.mock.mock_open(read_data=files[filename]).return_value
    else:
        raise FileNotFoundError(f'Unable to open {filename}')

proc_filesystems = '	xfs\n'

def mock_multi_open_i3en_2xlarge(filename, *args, **kwargs):
    files = {
        '/sys/class/net/eth0/address': '00:00:5e:00:53:00\n',
        '/sys/class/nvme/nvme0/model': 'Amazon Elastic Block Store\n',
        '/sys/class/nvme/nvme1/model': 'Amazon EC2 NVMe Instance Storage\n',
        '/sys/class/nvme/nvme2/model': 'Amazon EC2 NVMe Instance Storage\n',
        '/proc/filesystems': proc_filesystems
    }
    return _mock_multi_open(files, filename, *args, **kwargs)

def mock_multi_open_i3en_2xlarge_with_ebs(filename, *args, **kwargs):
    files = {
        '/sys/class/net/eth0/address': '00:00:5e:00:53:00\n',
        '/sys/class/nvme/nvme0/model': 'Amazon Elastic Block Store\n',
        '/sys/class/nvme/nvme1/model': 'Amazon Elastic Block Store\n',
        '/sys/class/nvme/nvme2/model': 'Amazon Elastic Block Store\n',
        '/sys/class/nvme/nvme3/model': 'Amazon EC2 NVMe Instance Storage\n',
        '/sys/class/nvme/nvme4/model': 'Amazon EC2 NVMe Instance Storage\n',
        '/proc/filesystems': proc_filesystems
    }
    return _mock_multi_open(files, filename, *args, **kwargs)


def mock_multi_open_i3_2xlarge(filename, *args, **kwargs):
    files = {
        '/sys/class/net/eth0/address': '00:00:5e:00:53:00\n',
        '/sys/class/nvme/nvme0/model': 'Amazon EC2 NVMe Instance Storage\n',
        '/sys/class/nvme/nvme1/model': 'Amazon EC2 NVMe Instance Storage\n',
        '/proc/filesystems': proc_filesystems
    }
    return _mock_multi_open(files, filename, *args, **kwargs)

CompletedProcess = namedtuple('CompletedProcess', ['stdout'])
def _mock_multi_run(programs, *popenargs,
    input=None, capture_output=False, timeout=None, check=False, **kwargs):
    cmd = popenargs[0]
    if cmd in programs:
        retval = CompletedProcess(programs[cmd])
        return retval
    else:
        raise CalledProcessError(-1, cmd)

def mock_multi_run_i3en_2xlarge(*popenargs,
    input=None, capture_output=False, timeout=None, check=False, **kwargs):
    programs = {'findmnt -n -o SOURCE /': '/dev/nvme0n1p1\n'}
    return _mock_multi_run(programs, *popenargs, input, capture_output, timeout, check, **kwargs)

def mock_multi_run_i3_2xlarge(*popenargs,
    input=None, capture_output=False, timeout=None, check=False, **kwargs):
    programs = {'findmnt -n -o SOURCE /': '/dev/xvda1\n'}
    return _mock_multi_run(programs, *popenargs, input, capture_output, timeout, check, **kwargs)


sdiskpart = namedtuple('sdiskpart', ['device', 'mountpoint'])
mock_disk_partitions = [
    sdiskpart('/dev/root', '/'),
    sdiskpart(device='/dev/md0', mountpoint='/var/lib/scylla'),
    sdiskpart(device='/dev/md0', mountpoint='/var/lib/systemd/coredump')
]

mock_listdevdir_i3en_2xlarge = ['root', 'nvme0n1p1', 'nvme0n1', 'nvme2n1', 'nvme1n1', 'nvme2', 'nvme1', 'nvme0', 'zero', 'null']
mock_listdevdir_i3en_2xlarge_with_ebs = ['md0', 'root', 'nvme0n1p1', 'nvme0n1', 'nvme3n1', 'nvme4n1', 'nvme2n1', 'nvme4', 'nvme1n1', 'nvme3', 'nvme2', 'nvme1', 'nvme0', 'zero', 'null']
mock_listdevdir_i3_2xlarge = ['md0', 'root', 'nvme0n1', 'nvme1n1', 'xvda1', 'xvda', 'nvme1', 'nvme0', 'zero', 'null']


class TestAwsInstance(TestCase):
    def setUp(self):
        httpretty.enable(verbose=True, allow_net_connect=False)

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    def httpretty_aws_metadata(self, instance_type='i3en.2xlarge', with_ebs=False, with_userdata=False):
        if not with_userdata:
            httpretty.register_uri(
                httpretty.GET,
                'http://169.254.169.254/latest/',
                '''
dynamic
meta-data
'''[1:-1]
            )
        else:
            httpretty.register_uri(
                httpretty.GET,
                'http://169.254.169.254/latest/',
                '''
dynamic
meta-data
user-data
'''[1:-1]
            )
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/latest/meta-data/instance-type',
            instance_type
        )
        if not with_ebs:
            httpretty.register_uri(
                httpretty.GET,
                'http://169.254.169.254/latest/meta-data/block-device-mapping',
                '''
ami
root
'''[1:-1]
            )
        else:
            httpretty.register_uri(
                httpretty.GET,
                'http://169.254.169.254/latest/meta-data/block-device-mapping',
                '''
ami
ebs2
ebs3
root
'''[1:-1]
            )
            httpretty.register_uri(
                httpretty.GET,
                'http://169.254.169.254/latest/meta-data/block-device-mapping/ebs2',
                'sdb'
            )
            httpretty.register_uri(
                httpretty.GET,
                'http://169.254.169.254/latest/meta-data/block-device-mapping/ebs3',
                'sdc'
            )
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/latest/meta-data/block-device-mapping/ami',
            '/dev/sda1' if instance_type == 'i3.2xlarge' else 'sda1'
        )
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/latest/meta-data/block-device-mapping/root',
            '/dev/sda1'
        )
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/latest/meta-data/public-ipv4',
            '10.0.0.1'
        )
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/latest/meta-data/local-ipv4',
            '172.16.0.1'
        )
        httpretty.register_uri(
            httpretty.GET,
            'http://169.254.169.254/latest/meta-data/network/interfaces/macs/00:00:5e:00:53:00',
            '''
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
'''[1:-1]
        )
        if with_userdata:
            httpretty.register_uri(
                httpretty.GET,
                'http://169.254.169.254/latest/user-data',
                '{"scylla_yaml": {"cluster_name": "test-cluster"}}'
            )


    def test_is_aws_instance(self):
        self.httpretty_aws_metadata()
        assert aws_instance.is_aws_instance()

    def test_is_not_aws_instance(self):
        httpretty.disable()
        assert not aws_instance.is_aws_instance()

    def test_endpoint_snitch(self):
        self.httpretty_aws_metadata()
        ins = aws_instance()
        assert ins.endpoint_snitch == 'Ec2Snitch'

    def test_getting_started_url(self):
        self.httpretty_aws_metadata()
        ins = aws_instance()
        assert ins.getting_started_url == 'http://www.scylladb.com/doc/getting-started-amazon/'

    def test_instance_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        ins = aws_instance()
        assert ins.instance() == 'i3en.2xlarge'

    def test_instance_t3_nano(self):
        self.httpretty_aws_metadata(instance_type='t3.nano')
        ins = aws_instance()
        assert ins.instance() == 't3.nano'

    def test_instance_size_2xlarge(self):
        self.httpretty_aws_metadata()
        ins = aws_instance()
        assert ins.instance_size() == '2xlarge'

    def test_instance_size_nano(self):
        self.httpretty_aws_metadata(instance_type='t3.nano')
        ins = aws_instance()
        assert ins.instance_size() == 'nano'

    def test_instance_class_i3en(self):
        self.httpretty_aws_metadata()
        ins = aws_instance()
        assert ins.instance_class() == 'i3en'

    def test_instance_class_t3(self):
        self.httpretty_aws_metadata(instance_type='t3.nano')
        ins = aws_instance()
        assert ins.instance_class() == 't3'

    def test_is_supported_instance_class(self):
        self.httpretty_aws_metadata()
        ins = aws_instance()
        assert ins.is_supported_instance_class()

    def test_is_not_supported_instance_class(self):
        self.httpretty_aws_metadata(instance_type='t3.nano')
        ins = aws_instance()
        assert not ins.is_supported_instance_class()

    def test_get_en_interface_type_ena(self):
        self.httpretty_aws_metadata()
        ins = aws_instance()
        assert ins.get_en_interface_type() == 'ena'

    def test_get_en_interface_type_ixgbevf(self):
        self.httpretty_aws_metadata(instance_type='c3.large')
        ins = aws_instance()
        assert ins.get_en_interface_type() == 'ixgbevf'

    def test_public_ipv4(self):
        self.httpretty_aws_metadata()
        ins = aws_instance()
        assert ins.public_ipv4() == '10.0.0.1'

    def test_private_ipv4(self):
        self.httpretty_aws_metadata()
        ins = aws_instance()
        assert ins.private_ipv4() == '172.16.0.1'

    def test_is_vpc_enabled(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)):
            ins = aws_instance()
            assert ins.is_vpc_enabled()

    def test_user_data(self):
        self.httpretty_aws_metadata(with_userdata=True)
        ins = aws_instance()
        assert ins.user_data == '{"scylla_yaml": {"cluster_name": "test-cluster"}}'

    def test_no_user_data(self):
        self.httpretty_aws_metadata()
        ins = aws_instance()
        assert not ins.user_data


    def test_non_root_nvmes_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)):
            ins = aws_instance()
            assert ins._non_root_nvmes() == {'root': ['/dev/nvme0n1p1'], 'ephemeral': ['nvme2n1', 'nvme1n1'], 'ebs': []}

    def test_populate_disks_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)):
            ins = aws_instance()
            assert ins._disks['root'] == ['/dev/nvme0n1p1']
            assert ins._disks['ephemeral'] == ['nvme2n1', 'nvme1n1']
            assert ins._disks['ebs'] == []

    def test_disks_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)):
            ins = aws_instance()
            assert ins.disks() == {'nvme'}

    def test_root_device_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)):
            ins = aws_instance()
            assert ins.root_device() == {'/dev/nvme0n1p1'}

    def test_root_disk_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)):
            ins = aws_instance()
            assert ins.root_disk() == 'nvme'

    def test_non_root_disks_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)):
            ins = aws_instance()
            assert ins.non_root_disks() == {'nvme2n1', 'nvme1n1'}

    def test_nvme_disk_count_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)):
            ins = aws_instance()
            assert ins.nvme_disk_count == 2

    def test_get_local_disks_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)):
            ins = aws_instance()
            assert ins.get_local_disks() == ['nvme2n1', 'nvme1n1']

    def test_get_remote_disks_i3en_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge)):
            ins = aws_instance()
            assert ins.get_remote_disks() == []


    def test_non_root_nvmes_i3en_2xlarge_with_ebs(self):
        self.httpretty_aws_metadata(with_ebs=True)
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge_with_ebs),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge_with_ebs)):
            ins = aws_instance()
            assert ins._non_root_nvmes() == {'root': ['/dev/nvme0n1p1'], 'ephemeral': ['nvme3n1', 'nvme4n1'], 'ebs': ['nvme2n1', 'nvme1n1']}

    def test_populate_disks_i3en_2xlarge_with_ebs(self):
        self.httpretty_aws_metadata(with_ebs=True)
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge_with_ebs),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge_with_ebs)):
            ins = aws_instance()
            assert ins._disks['root'] == ['/dev/nvme0n1p1']
            assert ins._disks['ephemeral'] == ['nvme3n1', 'nvme4n1']
            assert ins._disks['ebs'] == ['nvme2n1', 'nvme1n1']

    def test_non_root_disks_i3en_2xlarge_with_ebs(self):
        self.httpretty_aws_metadata(with_ebs=True)
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge_with_ebs),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge_with_ebs)):
            ins = aws_instance()
            assert ins.non_root_disks() == {'nvme2n1', 'nvme4n1', 'nvme1n1', 'nvme3n1'}

    def test_nvme_disk_count_i3en_2xlarge_with_ebs(self):
        self.httpretty_aws_metadata(with_ebs=True)
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge_with_ebs),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge_with_ebs)):
            ins = aws_instance()
            assert ins.nvme_disk_count == 4

    def test_get_local_disks_i3en_2xlarge_with_ebs(self):
        self.httpretty_aws_metadata(with_ebs=True)
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge_with_ebs),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge_with_ebs)):
            ins = aws_instance()
            assert ins.get_local_disks() == ['nvme3n1', 'nvme4n1']

    def test_get_remote_disks_i3en_2xlarge_with_ebs(self):
        self.httpretty_aws_metadata(with_ebs=True)
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3en_2xlarge_with_ebs),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3en_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3en_2xlarge_with_ebs)):
            ins = aws_instance()
            assert ins.get_remote_disks() == ['nvme2n1', 'nvme1n1']


    def test_non_root_nvmes_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)):
            ins = aws_instance()
            assert ins._non_root_nvmes() == {'root': ['/dev/xvda1'], 'ephemeral': ['nvme0n1', 'nvme1n1'], 'ebs': []}

    def test_populate_disks_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)):
            ins = aws_instance()
            assert ins._disks['root'] == ['/dev/xvda1']
            assert ins._disks['ephemeral'] == ['nvme0n1', 'nvme1n1']
            assert ins._disks['ebs'] == []

    def test_disks_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)):
            ins = aws_instance()
            assert ins.disks() == {'nvme', 'xvda'}

    def test_root_device_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)):
            ins = aws_instance()
            assert ins.root_device() == {'/dev/xvda1'}

    def test_root_disk_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)):
            ins = aws_instance()
            assert ins.root_disk() == 'xvda'

    def test_non_root_disks_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)):
            ins = aws_instance()
            assert ins.non_root_disks() == {'nvme0n1', 'nvme1n1'}

    def test_nvme_disk_count_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)):
            ins = aws_instance()
            assert ins.nvme_disk_count == 2

    def test_get_local_disks_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)):
            ins = aws_instance()
            assert ins.get_local_disks() == ['nvme0n1', 'nvme1n1']

    def test_get_remote_disks_i3_2xlarge(self):
        self.httpretty_aws_metadata()
        with unittest.mock.patch('psutil.disk_partitions', return_value=mock_disk_partitions),\
            unittest.mock.patch('os.listdir', return_value=mock_listdevdir_i3_2xlarge),\
            unittest.mock.patch('lib.scylla_cloud.run', side_effect=mock_multi_run_i3_2xlarge),\
            unittest.mock.patch('builtins.open', unittest.mock.MagicMock(side_effect=mock_multi_open_i3_2xlarge)):
            ins = aws_instance()
            assert ins.get_remote_disks() == []

