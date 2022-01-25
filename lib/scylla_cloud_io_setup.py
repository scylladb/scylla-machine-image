#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright 2022 ScyllaDB
#
# SPDX-License-Identifier: Apache-2.0

import os
import yaml
import logging
import sys
sys.path.append('/opt/scylladb/scripts')
from scylla_util import is_developer_mode, etcdir, datadir
from abc import ABCMeta, abstractmethod

def UnsupportedInstanceClassError(Exception):
    pass

def PresetNotFoundError(Exception):
    pass

class cloud_io_setup(metaclass=ABCMeta):
    @abstractmethod
    def generate(self):
        pass

    def save(self):
        assert "read_iops" in self.disk_properties
        properties_file = open(etcdir() + "/scylla.d/io_properties.yaml", "w")
        yaml.dump({"disks": [self.disk_properties]}, properties_file,  default_flow_style=False)
        ioconf = open(etcdir() + "/scylla.d/io.conf", "w")
        ioconf.write("SEASTAR_IO=\"--io-properties-file={}\"\n".format(properties_file.name))
        os.chmod(etcdir() + '/scylla.d/io_properties.yaml', 0o644)
        os.chmod(etcdir() + '/scylla.d/io.conf', 0o644)


class aws_io_setup(cloud_io_setup):
    def __init__(self, idata):
        self.idata = idata
        self.disk_properties = {}

    def generate(self):
        if not self.idata.is_supported_instance_class():
            raise UnsupportedInstanceClassError()
        self.disk_properties["mountpoint"] = datadir()
        nr_disks = len(self.idata.get_local_disks())
        ## both i3 and i2 can run with 1 I/O Queue per shard
        if self.idata.instance() == "i3.large":
            self.disk_properties["read_iops"] = 111000
            self.disk_properties["read_bandwidth"] = 653925080
            self.disk_properties["write_iops"] = 36800
            self.disk_properties["write_bandwidth"] = 215066473
        elif self.idata.instance() == "i3.xlarge":
            self.disk_properties["read_iops"] = 200800
            self.disk_properties["read_bandwidth"] = 1185106376
            self.disk_properties["write_iops"] = 53180
            self.disk_properties["write_bandwidth"] = 423621267
        elif self.idata.instance_class() == "i3":
            self.disk_properties["read_iops"] = 411200 * nr_disks
            self.disk_properties["read_bandwidth"] = 2015342735 * nr_disks
            self.disk_properties["write_iops"] = 181500 * nr_disks
            self.disk_properties["write_bandwidth"] = 808775652 * nr_disks
        elif self.idata.instance_class() == "i3en":
            if self.idata.instance() == "i3en.large":
                self.disk_properties["read_iops"] = 43315
                self.disk_properties["read_bandwidth"] = 330301440
                self.disk_properties["write_iops"] = 33177
                self.disk_properties["write_bandwidth"] = 165675008
            elif self.idata.instance() in ("i3en.xlarge", "i3en.2xlarge"):
                self.disk_properties["read_iops"] = 84480 * nr_disks
                self.disk_properties["read_bandwidth"] = 666894336 * nr_disks
                self.disk_properties["write_iops"] = 66969 * nr_disks
                self.disk_properties["write_bandwidth"] = 333447168 * nr_disks
            else:
                self.disk_properties["read_iops"] = 257024 * nr_disks
                self.disk_properties["read_bandwidth"] = 2043674624 * nr_disks
                self.disk_properties["write_iops"] = 174080 * nr_disks
                self.disk_properties["write_bandwidth"] = 1024458752 * nr_disks
        elif self.idata.instance_class() == "i2":
            self.disk_properties["read_iops"] = 64000 * nr_disks
            self.disk_properties["read_bandwidth"] = 507338935 * nr_disks
            self.disk_properties["write_iops"] = 57100 * nr_disks
            self.disk_properties["write_bandwidth"] = 483141731 * nr_disks
        elif self.idata.instance_class() in ("c6gd", "m6gd", "r6gd", "x2gd"):
            if self.idata.instance_size() == "medium":
                self.disk_properties["read_iops"] = 14808
                self.disk_properties["read_bandwidth"] = 77869147
                self.disk_properties["write_iops"] = 5972
                self.disk_properties["write_bandwidth"] = 32820302
            elif self.idata.instance_size() == "large":
                self.disk_properties["read_iops"] = 29690
                self.disk_properties["read_bandwidth"] = 157712240
                self.disk_properties["write_iops"] = 12148
                self.disk_properties["write_bandwidth"] = 65978069
            elif self.idata.instance_size() == "xlarge":
                self.disk_properties["read_iops"] = 59688
                self.disk_properties["read_bandwidth"] = 318762880
                self.disk_properties["write_iops"] = 24449
                self.disk_properties["write_bandwidth"] = 133311808
            elif self.idata.instance_size() == "2xlarge":
                self.disk_properties["read_iops"] = 119353
                self.disk_properties["read_bandwidth"] = 634795733
                self.disk_properties["write_iops"] = 49069
                self.disk_properties["write_bandwidth"] = 266841680
            elif self.idata.instance_size() == "4xlarge":
                self.disk_properties["read_iops"] = 237196
                self.disk_properties["read_bandwidth"] = 1262309504
                self.disk_properties["write_iops"] = 98884
                self.disk_properties["write_bandwidth"] = 533938080
            elif self.idata.instance_size() == "8xlarge":
                self.disk_properties["read_iops"] = 442945
                self.disk_properties["read_bandwidth"] = 2522688939
                self.disk_properties["write_iops"] = 166021
                self.disk_properties["write_bandwidth"] = 1063041152
            elif self.idata.instance_size() == "12xlarge":
                self.disk_properties["read_iops"] = 353691 * nr_disks
                self.disk_properties["read_bandwidth"] = 1908192256 * nr_disks
                self.disk_properties["write_iops"] = 146732 * nr_disks
                self.disk_properties["write_bandwidth"] = 806399360 * nr_disks
            elif self.idata.instance_size() == "16xlarge":
                self.disk_properties["read_iops"] = 426893 * nr_disks
                self.disk_properties["read_bandwidth"] = 2525781589 * nr_disks
                self.disk_properties["write_iops"] = 161740 * nr_disks
                self.disk_properties["write_bandwidth"] = 1063389952 * nr_disks
            elif self.idata.instance_size() == "metal":
                self.disk_properties["read_iops"] = 416257 * nr_disks
                self.disk_properties["read_bandwidth"] = 2527296683 * nr_disks
                self.disk_properties["write_iops"] = 156326 * nr_disks
                self.disk_properties["write_bandwidth"] = 1063657088 * nr_disks
        elif self.idata.instance() == "im4gn.large":
            self.disk_properties["read_iops"] = 33943
            self.disk_properties["read_bandwidth"] = 288433525
            self.disk_properties["write_iops"] = 27877
            self.disk_properties["write_bandwidth"] = 126864680
        elif self.idata.instance() == "im4gn.xlarge":
            self.disk_properties["read_iops"] = 68122
            self.disk_properties["read_bandwidth"] = 576603520
            self.disk_properties["write_iops"] = 55246
            self.disk_properties["write_bandwidth"] = 254534954
        elif self.idata.instance() == "im4gn.2xlarge":
            self.disk_properties["read_iops"] = 136422
            self.disk_properties["read_bandwidth"] = 1152663765
            self.disk_properties["write_iops"] = 92184
            self.disk_properties["write_bandwidth"] = 508926453
        elif self.idata.instance() == "im4gn.4xlarge":
            self.disk_properties["read_iops"] = 273050
            self.disk_properties["read_bandwidth"] = 1638427264
            self.disk_properties["write_iops"] = 92173
            self.disk_properties["write_bandwidth"] = 1027966826
        elif self.idata.instance() == "im4gn.8xlarge":
            self.disk_properties["read_iops"] = 250241 * nr_disks
            self.disk_properties["read_bandwidth"] = 1163130709 * nr_disks
            self.disk_properties["write_iops"] = 86374 * nr_disks
            self.disk_properties["write_bandwidth"] = 977617664 * nr_disks
        elif self.idata.instance() == "im4gn.16xlarge":
            self.disk_properties["read_iops"] = 273030 * nr_disks
            self.disk_properties["read_bandwidth"] = 1638211413 * nr_disks
            self.disk_properties["write_iops"] = 92607 * nr_disks
            self.disk_properties["write_bandwidth"] = 1028340266 * nr_disks
        elif self.idata.instance() == "is4gen.medium":
            self.disk_properties["read_iops"] = 33965
            self.disk_properties["read_bandwidth"] = 288462506
            self.disk_properties["write_iops"] = 27876
            self.disk_properties["write_bandwidth"] = 126954200
        elif self.idata.instance() == "is4gen.large":
            self.disk_properties["read_iops"] = 68131
            self.disk_properties["read_bandwidth"] = 576654869
            self.disk_properties["write_iops"] = 55257
            self.disk_properties["write_bandwidth"] = 254551002
        elif self.idata.instance() == "is4gen.xlarge":
            self.disk_properties["read_iops"] = 136413
            self.disk_properties["read_bandwidth"] = 1152747904
            self.disk_properties["write_iops"] = 92180
            self.disk_properties["write_bandwidth"] = 508889546
        elif self.idata.instance() == "is4gen.2xlarge":
            self.disk_properties["read_iops"] = 273038
            self.disk_properties["read_bandwidth"] = 1628982613
            self.disk_properties["write_iops"] = 92182
            self.disk_properties["write_bandwidth"] = 1027983530
        elif self.idata.instance() == "is4gen.4xlarge":
            self.disk_properties["read_iops"] = 260493 * nr_disks
            self.disk_properties["read_bandwidth"] = 1217396928 * nr_disks
            self.disk_properties["write_iops"] = 83169 * nr_disks
            self.disk_properties["write_bandwidth"] = 1000390784 * nr_disks
        elif self.idata.instance() == "is4gen.8xlarge":
            self.disk_properties["read_iops"] = 273021 * nr_disks
            self.disk_properties["read_bandwidth"] = 1656354602 * nr_disks
            self.disk_properties["write_iops"] = 92233 * nr_disks
            self.disk_properties["write_bandwidth"] = 1028010325 * nr_disks
        if not "read_iops" in self.disk_properties:
            raise PresetNotFoundError()


class gcp_io_setup(cloud_io_setup):
    def __init__(self, idata):
        self.idata = idata
        self.disk_properties = {}

    def generate(self):
        if not self.idata.is_supported_instance_class():
            raise UnsupportedInstanceClassError()
        self.disk_properties = {}
        self.disk_properties["mountpoint"] = datadir()
        nr_disks = self.idata.nvme_disk_count
        # below is based on https://cloud.google.com/compute/docs/disks/local-ssd#performance
        # and https://cloud.google.com/compute/docs/disks/local-ssd#nvme
        # note that scylla iotune might measure more, this is GCP recommended
        mbs=1024*1024
        if nr_disks >= 1 and nr_disks < 4:
            self.disk_properties["read_iops"] = 170000 * nr_disks
            self.disk_properties["read_bandwidth"] = 660 * mbs * nr_disks
            self.disk_properties["write_iops"] = 90000 * nr_disks
            self.disk_properties["write_bandwidth"] = 350 * mbs * nr_disks
        elif nr_disks >= 4 and nr_disks <= 8:
            self.disk_properties["read_iops"] = 680000
            self.disk_properties["read_bandwidth"] = 2650 * mbs
            self.disk_properties["write_iops"] = 360000
            self.disk_properties["write_bandwidth"] = 1400 * mbs
        elif nr_disks == 16:
            self.disk_properties["read_iops"] = 1600000
            self.disk_properties["read_bandwidth"] = 4521251328
            #below is google, above is our measured
            #self.disk_properties["read_bandwidth"] = 6240 * mbs
            self.disk_properties["write_iops"] = 800000
            self.disk_properties["write_bandwidth"] = 2759452672
            #below is google, above is our measured
            #self.disk_properties["write_bandwidth"] = 3120 * mbs
        elif nr_disks == 24:
            self.disk_properties["read_iops"] = 2400000
            self.disk_properties["read_bandwidth"] = 5921532416
            #below is google, above is our measured
            #self.disk_properties["read_bandwidth"] = 9360 * mbs
            self.disk_properties["write_iops"] = 1200000
            self.disk_properties["write_bandwidth"] = 4663037952
            #below is google, above is our measured
            #self.disk_properties["write_bandwidth"] = 4680 * mbs

        if not "read_iops" in self.disk_properties:
            raise PresetNotFoundError()


class azure_io_setup(cloud_io_setup):
    def __init__(self, idata):
        self.idata = idata
        self.disk_properties = {}

    def generate(self):
        if not self.idata.is_supported_instance_class():
            raise UnsupportedInstanceClassError()

        self.disk_properties = {}
        self.disk_properties["mountpoint"] = datadir()
        nr_disks = self.idata.nvme_disk_count
        # below is based on https://docs.microsoft.com/en-us/azure/virtual-machines/lsv2-series
        # note that scylla iotune might measure more, this is Azure recommended
        # since write properties are not defined, they come from our iotune tests
        mbs = 1024*1024
        if nr_disks == 1:
            self.disk_properties["read_iops"] = 400000
            self.disk_properties["read_bandwidth"] = 2000 * mbs
            self.disk_properties["write_iops"] = 271696
            self.disk_properties["write_bandwidth"] = 1314 * mbs
        elif nr_disks == 2:
            self.disk_properties["read_iops"] = 800000
            self.disk_properties["read_bandwidth"] = 4000 * mbs
            self.disk_properties["write_iops"] = 552434
            self.disk_properties["write_bandwidth"] = 2478 * mbs
        elif nr_disks == 4:
            self.disk_properties["read_iops"] = 1500000
            self.disk_properties["read_bandwidth"] = 8000 * mbs
            self.disk_properties["write_iops"] = 1105063
            self.disk_properties["write_bandwidth"] = 4948 * mbs
        elif nr_disks == 6:
            self.disk_properties["read_iops"] = 2200000
            self.disk_properties["read_bandwidth"] = 14000 * mbs
            self.disk_properties["write_iops"] = 1616847
            self.disk_properties["write_bandwidth"] = 7892 * mbs
        elif nr_disks == 8:
            self.disk_properties["read_iops"] = 2900000
            self.disk_properties["read_bandwidth"] = 16000 * mbs
            self.disk_properties["write_iops"] = 2208081
            self.disk_properties["write_bandwidth"] = 9694 * mbs
        elif nr_disks == 10:
            self.disk_properties["read_iops"] = 3800000
            self.disk_properties["read_bandwidth"] = 20000 * mbs
            self.disk_properties["write_iops"] = 2546511
            self.disk_properties["write_bandwidth"] = 11998 * mbs
        if not "read_iops" in self.disk_properties:
            raise PresetNotFoundError()
