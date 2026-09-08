#
# Copyright 2026 ScyllaDB
#
# SPDX-License-Identifier: Apache-2.0
"""Guard the hand-maintained AWS instance lists against drifting apart.

Supporting a new AWS instance family currently takes three separate edits:

1. ``common/aws_io_params.yaml`` -- the measured I/O properties, written by
   ``tools/io_properties_measurements/get_scylla_io_properties.py``
2. ``AwsInstance.is_supported_instance_class()`` -- gates I/O setup at first boot
3. ``AwsInstance.get_en_interface_type()`` -- gates the ``scylla_ec2_check`` login banner

Nothing ties the three together, and they have already drifted: i7i was added to
aws_io_params.yaml in commit 1598dc6 (2025-05-12) but only reached the two Python
lists in commit bcfeee3 (2025-07-02). For those seven weeks i7i shipped with I/O
presets that could never be read -- ``AwsIoSetup.generate()`` raises
``UnsupportedInstanceClassError`` before it ever opens the YAML, ``scylla_image_setup``
skips ``io_setup()`` entirely, and ``scylla_login`` greets the user with
"unsupported instance type".

These tests make that class of mistake fail in CI instead of in an AMI.

See SMI-184.
"""

import ast
import inspect
import sys
from pathlib import Path

import pytest
import yaml


sys.path.append(str(Path(__file__).parent.parent))
from lib.scylla_cloud import AwsInstance


AWS_IO_PARAMS_PATH = Path(__file__).parent.parent / "common" / "aws_io_params.yaml"

IO_PARAM_KEYS = frozenset({"read_iops", "read_bandwidth", "write_iops", "write_bandwidth"})


def aws_instance(instance_type):
    """Build an AwsInstance that answers for `instance_type` without touching the metadata service.

    ``instancetype`` is a functools.cached_property, so seeding the instance
    __dict__ short-circuits the lookup it would otherwise do over HTTP.
    """
    instance = AwsInstance.__new__(AwsInstance)
    instance.__dict__["instancetype"] = instance_type
    return instance


def io_params():
    with open(AWS_IO_PARAMS_PATH) as f:
        return yaml.safe_load(f)


def io_params_classes():
    """The instance classes aws_io_params.yaml has presets for, e.g. {"i3", "i4i", ...}."""
    return {key.split(".")[0] for key in io_params()}


def hardcoded_supported_classes():
    """The literal list inside AwsInstance.is_supported_instance_class()."""
    tree = ast.parse(inspect.getsource(AwsInstance.is_supported_instance_class).strip())
    classes = {
        element.value
        for node in ast.walk(tree)
        if isinstance(node, ast.List)
        for element in node.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    }
    assert classes, (
        "Could not find a literal list of instance classes in "
        "AwsInstance.is_supported_instance_class(). If the list is now derived from "
        f"{AWS_IO_PARAMS_PATH.name} (see SMI-184), this test has served its purpose and can go."
    )
    return classes


class TestAwsInstanceSupportConsistency:
    def test_io_params_classes_are_all_supported(self):
        """A class with measured I/O presets must be one we admit to supporting.

        This is the i7i regression: presets land in the YAML, the Python list is
        forgotten, and the instance boots as "unsupported" with no I/O tuning.
        """
        missing = sorted(
            cls for cls in io_params_classes() if not aws_instance(f"{cls}.large").is_supported_instance_class()
        )
        assert not missing, (
            f"{AWS_IO_PARAMS_PATH.name} has I/O presets for {missing}, but "
            "AwsInstance.is_supported_instance_class() rejects them, so those presets can never "
            "be applied. Add them to the list in lib/scylla_cloud.py."
        )

    def test_supported_classes_have_io_params(self):
        """A class we claim to support should have presets, or first boot falls back to iotune."""
        missing = sorted(hardcoded_supported_classes() - io_params_classes())
        assert not missing, (
            f"AwsInstance.is_supported_instance_class() accepts {missing}, but "
            f"{AWS_IO_PARAMS_PATH.name} has no presets for them, so first boot falls back to "
            "running scylla_io_setup. Add the measured values with "
            "tools/io_properties_measurements/get_scylla_io_properties.py --update-aws-params."
        )

    def test_supported_classes_have_enhanced_networking_type(self):
        """Every supported class must map to a NIC driver, or scylla_ec2_check cries wolf.

        scylla_ec2_check is only ever reached (via scylla_login) once
        is_supported_instance_class() returned True. A supported class missing from
        get_en_interface_type() therefore prints a red "doesn't support enhanced
        networking!" on every login and exits 1, for no reason.
        """
        missing = sorted(
            cls for cls in hardcoded_supported_classes() if aws_instance(f"{cls}.large").get_en_interface_type() is None
        )
        assert not missing, (
            f"AwsInstance.get_en_interface_type() returns None for supported classes {missing}. "
            "scylla_ec2_check will report they don't support enhanced networking. Add them to "
            "the ENA list in lib/scylla_cloud.py."
        )

    @pytest.mark.parametrize("instance_type", sorted(io_params()))
    def test_io_params_entries_are_well_formed(self, instance_type):
        """aws_io_params.yaml is tool-generated; make sure a bad run cannot ship silently."""
        entry = io_params()[instance_type]
        assert instance_type.count(".") == 1, f"{instance_type!r} is not a <class>.<size> key"
        assert isinstance(entry, dict), f"{instance_type} is not a mapping"
        assert (
            set(entry) == IO_PARAM_KEYS
        ), f"{instance_type} has keys {sorted(entry)}, expected {sorted(IO_PARAM_KEYS)}"
        for key, value in entry.items():
            assert isinstance(value, int), f"{instance_type}.{key} is {value!r}, expected an int"
            assert value > 0, f"{instance_type}.{key} is {value}, expected a positive int"
