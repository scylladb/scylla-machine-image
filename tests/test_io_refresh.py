#
# Copyright 2026 ScyllaDB
#
# SPDX-License-Identifier: Apache-2.0
"""Refreshing io_properties.yaml on upgrade and boot (SMI-218)."""

import importlib.util
import sys
import threading
import types
import unittest.mock
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))

from lib import io_refresh  # noqa: E402
from lib.io_refresh import History, classify, render  # noqa: E402


def load_script(name):
    path = REPO / "common" / name
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader=None, origin=str(path)))
    exec(path.read_text(), module.__dict__)
    return module


io_setup = load_script("scylla_cloud_io_setup")

I7IE_LARGE_NOW = (54183, 500035616, 43342, 250007504)
I7IE_LARGE_OLD = (58449, 574854656, 47145, 253132917)
I7IE_2XL_NOW = (216617, 2000095104, 173259, 1000054656)
# Shipped before SMI-326: the whole-instance entry multiplied again by the 2 disks (SMI-324).
I7IE_2XL_DOUBLED = (432946, 4000089600, 346960, 2000141952)
IOTUNE_OUTPUT = (61234, 512345678, 44321, 251234567)

AWS_HISTORY = History(yaml.safe_load((REPO / "common/aws_io_params_history.yaml").read_text()))


def params(values):
    return dict(zip(io_refresh.PARAM_KEYS, values, strict=False))


def decide(**kwargs):
    return decision(**kwargs).action


def decision(file_values=I7IE_LARGE_OLD, preset=I7IE_LARGE_NOW, keys=("i7ie.large",), **kwargs):
    args = {
        "preset": preset,
        "yaml_text": render(params(file_values)) if file_values else None,
        "conf_text": io_refresh.io_conf_line(),
        "provenance": None,
        "history": AWS_HISTORY,
        "keys": list(keys),
        "first_boot": "2026-01-01",
        "policy": "auto",
    }
    args.update(kwargs)
    return classify(**args)


class TestClassify:
    def test_no_preset_leaves_everything(self):
        assert decide(preset=None) == "skip"
        assert decide(preset=None, force=True) == "skip"

    def test_no_file_is_generated(self):
        assert decide(file_values=None) == "apply"

    def test_already_current(self):
        assert decide(file_values=I7IE_LARGE_NOW) == "adopt"
        prov = {"values": list(I7IE_LARGE_NOW)}
        assert decide(file_values=I7IE_LARGE_NOW, provenance=prov) == "current"

    def test_recorded_and_unchanged_is_updated(self):
        assert decide(provenance={"values": list(I7IE_LARGE_OLD)}) == "apply"

    def test_recorded_but_edited_is_left(self):
        assert decide(file_values=IOTUNE_OUTPUT, provenance={"values": list(I7IE_LARGE_OLD)}) == "notify"

    def test_recorded_by_newer_package_is_left(self):
        assert decide(file_values=IOTUNE_OUTPUT, provenance={"values": list(IOTUNE_OUTPUT)}) == "notify"

    @pytest.mark.parametrize(
        ("itype", "old", "now"),
        [
            ("i7ie.large", I7IE_LARGE_OLD, I7IE_LARGE_NOW),
            ("i7ie.2xlarge", I7IE_2XL_DOUBLED, I7IE_2XL_NOW),
            # branch-2025.1 stored the doubled value as a per-disk entry, so nodes got it x2 again
            ("i7ie.2xlarge", (865892, 8000179200, 693920, 4000283904), I7IE_2XL_NOW),
            # branch-2025.3 only, never on next
            ("i7ie.2xlarge", (433692, 4000036864, 346580, 2000333824), I7IE_2XL_NOW),
            # Enterprise 2024.1, presets in Python code: 54987 per disk, 1 disk
            ("i4i.large", (54987, 378494048, 30459, 279713216), None),
        ],
        ids=["i7ie-1-disk", "i7ie-2-disks", "2025.1-quadrupled", "2025.3-branch-only", "enterprise-2024.1"],
    )
    def test_file_from_any_release_is_updated(self, itype, old, now):
        """A file without provenance written by any release's preset, next or a release branch."""
        assert decide(file_values=old, preset=now or (1, 2, 3, 4), keys=[itype]) == "apply"

    def test_azure_disk_count_preset_is_updated(self):
        """Azure presets were keyed by disk count before the table (Jan 2026)."""
        azure = History(yaml.safe_load((REPO / "common/azure_io_params_history.yaml").read_text()))
        old = (400000, 2097152000, 271696, 1377828864)
        assert decide(file_values=old, preset=(1, 2, 3, 4), keys=["Standard_L8s_v3", "*/1"], history=azure) == "apply"

    def test_unknown_values_are_left(self):
        assert decide(file_values=IOTUNE_OUTPUT) == "notify"

    def test_predates_support_is_notified(self):
        early = decision(file_values=IOTUNE_OUTPUT, first_boot="2024-06-01")
        assert (early.action, early.reason.split(",")[0]) == ("notify", "predates our preset for this instance type")
        assert decision(file_values=IOTUNE_OUTPUT).reason == "values match no preset we published"

    def test_predates_uses_the_instance_type_date_not_the_wildcard(self):
        """GCP `*/4` dates from 2022, c4-standard-48-lssd from 2026-10-22: a node booted
        between them predates the c4 preset."""
        gcp = History(yaml.safe_load((REPO / "common/gcp_io_params_history.yaml").read_text()))
        keys = ["c4-standard-48-lssd/4", "c4-standard-48-lssd", "*/4"]
        assert gcp.first_published(keys) == str(gcp.first["c4-standard-48-lssd"])
        got = decision(file_values=IOTUNE_OUTPUT, keys=keys, history=gcp, first_boot="2026-06-01")
        assert got.reason.startswith("predates our preset")

    @pytest.mark.parametrize(
        "yaml_text",
        [
            "# tuned by hand\n" + render(params(I7IE_LARGE_OLD)),
            render(params(I7IE_LARGE_OLD)).replace("/var/lib/scylla", "/data"),
            yaml.dump({"disks": [{"mountpoint": "/var/lib/scylla", **params(I7IE_LARGE_OLD)}] * 2}),
            yaml.dump(
                {"disks": [{"mountpoint": "/var/lib/scylla", "read_saturation_length": 1, **params(I7IE_LARGE_OLD)}]}
            ),
            "disks: [",
        ],
        ids=["comment", "mountpoint", "two-disks", "extra-key", "unparseable"],
    )
    def test_hand_edited_layouts_are_left(self, yaml_text):
        assert decide(yaml_text=yaml_text) == "notify"

    def test_redirected_io_conf_is_left(self):
        conf = 'SEASTAR_IO="--io-properties-file=/etc/scylla.d/mine.yaml"\n'
        assert decide(conf_text=conf) == "notify"
        assert decide(file_values=None, conf_text=conf) == "notify"

    def test_pinned_and_never(self):
        assert decide(provenance={"pinned": True}) == "skip"
        assert decide(policy="never") == "skip"

    def test_notify_policy_never_updates(self):
        assert decide(policy="notify") == "notify"
        assert decide(provenance={"values": list(I7IE_LARGE_OLD)}, policy="notify") == "notify"

    def test_force(self):
        assert decide(file_values=IOTUNE_OUTPUT, force=True) == "apply"
        assert decide(provenance={"pinned": True}, force=True) == "apply"


@pytest.mark.parametrize("cloud", ["aws", "gcp", "azure", "oci"])
def test_history_has_every_current_preset(cloud):
    """Changing a preset must carry it into history: run ./tools/gen_io_params_history.py."""
    sys.path.insert(0, str(REPO / "tools"))
    import gen_io_params_history as gen

    table = yaml.safe_load((REPO / f"common/{cloud}_io_params.yaml").read_text())
    history = History(yaml.safe_load((REPO / f"common/{cloud}_io_params_history.yaml").read_text()))
    missing = [k for k, p in gen.render(cloud, table).items() if not history.knows([k], io_refresh.values_of(p))]
    assert not missing, f"not in {cloud}_io_params_history.yaml, run ./tools/gen_io_params_history.py: {missing}"


@pytest.fixture
def node(tmp_path, monkeypatch):
    """A node's /etc and /run in tmp_path, with the repo's tables and history."""
    for name, rel in [
        ("IO_YAML", "etc/scylla.d/io_properties.yaml"),
        ("IO_CONF", "etc/scylla.d/io.conf"),
        ("PROVENANCE", "etc/scylla/io_properties_provenance.yaml"),
        ("NOTICE", "etc/scylla/io_properties_notice"),
        ("POLICY", "etc/scylla/io_properties_refresh_policy"),
        ("CONFIGURED_MARKER", "etc/scylla/machine_image_configured"),
        ("LOCK", "run/lock/scylla-io-properties.lock"),
    ]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(io_refresh, name, path)
    monkeypatch.setattr(io_refresh, "HISTORY_DIR", REPO / "common")
    io_refresh.CONFIGURED_MARKER.touch()
    return types.SimpleNamespace(yaml=io_refresh.IO_YAML, conf=io_refresh.IO_CONF)


def write_legacy(node, values):
    node.yaml.write_text(render(params(values)))
    node.conf.write_text(io_refresh.io_conf_line())


def file_values(node):
    return io_refresh.parse_ours(node.yaml.read_text())


def refresh(preset=I7IE_LARGE_NOW, **kwargs):
    return io_refresh.refresh(preset=params(preset), cloud="aws", keys=["i7ie.large"], **kwargs).action


class TestRefreshOnNode:
    def test_update_backup_rollback_pin(self, node):
        write_legacy(node, I7IE_LARGE_OLD)
        assert refresh() == "apply"
        assert file_values(node) == I7IE_LARGE_NOW
        assert io_refresh.read_provenance()["values"] == list(I7IE_LARGE_NOW)
        assert io_refresh.parse_ours(io_refresh.backup_path(node.yaml).read_text()) == I7IE_LARGE_OLD

        assert refresh() == "current"  # idempotent

        io_refresh.rollback()
        assert file_values(node) == I7IE_LARGE_OLD
        assert refresh() == "skip"  # the rollback is not re-applied on the next boot
        assert file_values(node) == I7IE_LARGE_OLD

        io_refresh.set_pinned(False)
        assert refresh() == "notify"  # the restored file is no longer the one we recorded

    def test_dry_run_changes_nothing(self, node):
        write_legacy(node, I7IE_LARGE_OLD)
        assert refresh(dry_run=True) == "apply"
        assert file_values(node) == I7IE_LARGE_OLD
        assert not io_refresh.PROVENANCE.exists()

    def test_hand_tuned_file_gets_candidate_and_notice(self, node):
        write_legacy(node, IOTUNE_OUTPUT)
        assert refresh() == "notify"
        assert file_values(node) == IOTUNE_OUTPUT
        assert io_refresh.parse_ours(io_refresh.candidate_path(node.yaml).read_text()) == I7IE_LARGE_NOW
        assert "--refresh --force" in io_refresh.NOTICE.read_text()

        assert refresh(force=True) == "apply"  # the command the notice prints
        assert file_values(node) == I7IE_LARGE_NOW
        assert not io_refresh.NOTICE.exists()
        assert not io_refresh.candidate_path(node.yaml).exists()

    def test_pin_hides_notice(self, node):
        write_legacy(node, IOTUNE_OUTPUT)
        refresh()
        io_refresh.set_pinned(True)
        assert not io_refresh.NOTICE.exists()
        assert refresh() == "skip"

    def test_policy_file(self, node):
        write_legacy(node, I7IE_LARGE_OLD)
        io_refresh.POLICY.write_text("notify\n")
        assert refresh() == "notify"
        io_refresh.POLICY.write_text("bogus\n")
        assert refresh() == "apply"  # unknown policy falls back to the default

    def test_rollback_without_backup_fails(self, node):
        with pytest.raises(FileNotFoundError):
            io_refresh.rollback()

    def test_refresh_waits_for_the_lock(self, node):
        write_legacy(node, I7IE_LARGE_OLD)
        with io_refresh.locked():
            worker = threading.Thread(target=refresh)
            worker.start()
            worker.join(0.3)
            assert worker.is_alive(), "refresh ran while another one held the lock"
            assert file_values(node) == I7IE_LARGE_OLD
        worker.join(5)
        assert file_values(node) == I7IE_LARGE_NOW


class FakeInstance:
    def __init__(self, instancetype, instance_class, nvme_disk_count=0, cpu=64, ocpus=None, supported=True):
        self.instancetype, self._class, self.nvme_disk_count = instancetype, instance_class, nvme_disk_count
        self.cpu, self.ocpus, self._supported = cpu, ocpus, supported

    def instance_class(self):
        return self._class

    def is_supported_instance_class(self):
        return self._supported


@pytest.fixture
def tables():
    """The installed tables under /opt are the repo's common/ ones."""
    real_open = open

    def open_tables(path, *args, **kwargs):
        if str(path).startswith("/opt/scylladb/scylla-machine-image/"):
            path = REPO / "common" / Path(path).name
        return real_open(path, *args, **kwargs)

    with unittest.mock.patch("builtins.open", side_effect=open_tables):
        yield


class TestPresetsPerCloud:
    def test_gcp_disk_count_rule_is_a_preset(self, node, tables):
        """n2 with local SSD has no table entry of its own, Google's 4-disk numbers apply."""
        gcp = io_setup.GcpIoSetup(FakeInstance("n2-standard-32", "n2", nvme_disk_count=4))
        preset = gcp.compute()
        assert preset
        # What GcpIoSetup's built-in rule wrote for 4 disks before gcp_io_params.yaml existed.
        write_legacy(node, (680000, 2650 * 1024 * 1024, 360000, 1400 * 1024 * 1024))
        assert gcp.refresh().action == ("adopt" if io_refresh.values_of(preset)[0] == 680000 else "apply")
        assert file_values(node) == io_refresh.values_of(preset)

    @pytest.mark.parametrize(
        ("cls", "instance"),
        [
            (io_setup.AwsIoSetup, FakeInstance("i7ie.nonexistent", "i7ie")),
            (io_setup.GcpIoSetup, FakeInstance("z3-highmem-1-lssd", "z3", nvme_disk_count=1)),
            (io_setup.AzureIoSetup, FakeInstance("Standard_L1s_v9", "Lsv9")),
            (io_setup.OciIoSetup, FakeInstance("VM.DenseIO.X.Flex", "DenseIO", ocpus=3)),
        ],
    )
    def test_no_preset_never_runs_iotune(self, node, tables, cls, instance):
        write_legacy(node, IOTUNE_OUTPUT)
        with unittest.mock.patch("subprocess.run") as run:
            assert cls(instance).refresh().action == "skip"
        run.assert_not_called()
        assert file_values(node) == IOTUNE_OUTPUT

    def test_first_boot_records_provenance(self, node, tables):
        aws = io_setup.AwsIoSetup(FakeInstance("i7ie.large", "i7ie"))
        aws.generate()
        assert file_values(node) == I7IE_LARGE_NOW
        assert node.conf.read_text() == io_refresh.io_conf_line()
        assert io_refresh.read_provenance()["keys"] == ["i7ie.large"]


class TestEntryPoints:
    def test_refresh_without_raid_is_a_quiet_no_op(self):
        with unittest.mock.patch("os.path.ismount", return_value=False):
            assert io_setup.main(["--refresh"]) == 0
            assert io_setup.main([]) == 1

    def test_refresh_on_unsupported_instance_is_a_no_op(self, node, tables):
        aws = io_setup.AwsIoSetup(FakeInstance("t3.micro", "t3", supported=False))
        with (
            unittest.mock.patch("os.path.ismount", return_value=True),
            unittest.mock.patch.object(io_setup, "get_cloud_instance"),
            unittest.mock.patch.object(io_setup, "get_io_setup", return_value=aws),
        ):
            assert io_setup.main(["--refresh"]) == 0

    def test_boot_refresh_failure_does_not_fail_setup(self):
        image_setup = load_script("scylla_image_setup")
        with unittest.mock.patch.object(image_setup, "run", side_effect=TimeoutError("metadata")) as run:
            image_setup.refresh_io_properties()  # must not raise
        assert "--refresh" in run.call_args.args[0]

    @pytest.mark.parametrize(("policy", "written"), [("notify", "notify\n"), ("bogus", None), (None, None)])
    def test_policy_from_user_data(self, node, policy, written):
        from common.scylla_configure import ScyllaMachineImageConfigurator

        configurator = ScyllaMachineImageConfigurator.__new__(ScyllaMachineImageConfigurator)
        configurator._instance_user_data = {} if policy is None else {"io_properties_refresh": policy}
        configurator.set_io_properties_refresh_policy()
        assert (io_refresh.read_text(io_refresh.POLICY)) == written

    def test_packaging_hooks_are_guarded_and_non_fatal(self):
        postinst = (REPO / "dist/debian/debian/scylla-machine-image.postinst").read_text()
        spec = (REPO / "dist/redhat/scylla-machine-image.spec").read_text()
        for text, never_fail in ((postinst, "--refresh || true"), (spec, "--refresh || :")):
            assert "-e /etc/scylla/machine_image_configured" in text
            assert f"timeout 120 /opt/scylladb/scylla-machine-image/scylla_cloud_io_setup {never_fail}" in text
