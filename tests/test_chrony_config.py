#
# Copyright 2026 ScyllaDB
#
# SPDX-License-Identifier: Apache-2.0

import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path


sys.path.append(str(Path(__file__).parent.parent))

_module_path = str(Path(__file__).parent.parent / "packer" / "scylla_install_image")
scylla_install_image = SourceFileLoader("scylla_install_image", _module_path).load_module()
sys.modules["scylla_install_image"] = scylla_install_image

from unittest.mock import mock_open, patch  # noqa: E402

from scylla_install_image import build_chrony_conf, configure_chrony, get_chrony_sources  # noqa: E402


class TestGetChronySources:
    def test_aws_sources(self):
        sources = get_chrony_sources("aws")
        assert "server 169.254.169.123 prefer iburst minpoll 4 maxpoll 4" in sources
        assert "pool time.aws.com iburst" in sources

    def test_gce_sources(self):
        sources = get_chrony_sources("gce")
        assert "server metadata.google.internal prefer iburst" in sources
        assert "pool" not in sources.lower()

    def test_azure_sources(self):
        sources = get_chrony_sources("azure")
        assert "refclock PHC /dev/ptp_hyperv poll 3 dpoll -2 offset 0" in sources
        assert "server 169.254.169.254 iburst" in sources

    def test_oci_sources(self):
        sources = get_chrony_sources("oci")
        assert "server 169.254.169.254 prefer iburst" in sources


class TestBuildChronyConf:
    def test_contains_universal_settings(self):
        for cloud in ("aws", "gce", "azure", "oci"):
            conf = build_chrony_conf(cloud)
            assert "makestep 1.0 3" in conf, f"missing makestep for {cloud}"
            assert "driftfile /var/lib/chrony/drift" in conf, f"missing driftfile for {cloud}"
            assert "rtcsync" in conf, f"missing rtcsync for {cloud}"
            assert "logdir /var/log/chrony" in conf, f"missing logdir for {cloud}"
            assert "maxupdateskew 100.0" in conf, f"missing maxupdateskew for {cloud}"
            assert "leapsectz right/UTC" in conf, f"missing leapsectz for {cloud}"


class TestConfigureChrony:
    @patch("scylla_install_image.run")
    @patch("scylla_install_image.glob.glob", return_value=["/etc/chrony/sources.d/foo", "/etc/chrony/sources.d/bar"])
    @patch("scylla_install_image.os.remove")
    @patch("scylla_install_image.os.makedirs")
    @patch("builtins.open", new_callable=mock_open)
    def test_wipes_vendor_defaults(self, mock_file, mock_makedirs, mock_remove, mock_glob, mock_run):
        configure_chrony("aws")
        mock_glob.assert_any_call("/etc/chrony/sources.d/*")
        mock_glob.assert_any_call("/etc/chrony/conf.d/*")
        mock_remove.assert_any_call("/etc/chrony/sources.d/foo")
        mock_remove.assert_any_call("/etc/chrony/sources.d/bar")

    @patch("scylla_install_image.run")
    @patch("scylla_install_image.glob.glob", return_value=[])
    @patch("scylla_install_image.os.remove")
    @patch("scylla_install_image.os.makedirs")
    @patch("builtins.open", new_callable=mock_open)
    def test_writes_chrony_conf(self, mock_file, mock_makedirs, mock_remove, mock_glob, mock_run):
        configure_chrony("gce")
        mock_file.assert_any_call("/etc/chrony/chrony.conf", "w")
        handle = mock_file()
        written = "".join(c.args[0] for c in handle.write.call_args_list)
        assert "metadata.google.internal" in written

    @patch("scylla_install_image.run")
    @patch("scylla_install_image.glob.glob", return_value=[])
    @patch("scylla_install_image.os.remove")
    @patch("scylla_install_image.os.makedirs")
    @patch("builtins.open", new_callable=mock_open)
    def test_enables_chrony_wait(self, mock_file, mock_makedirs, mock_remove, mock_glob, mock_run):
        configure_chrony("aws")
        mock_run.assert_any_call("systemctl enable chrony-wait.service", shell=True, check=True)

    @patch("scylla_install_image.run")
    @patch("scylla_install_image.glob.glob", return_value=[])
    @patch("scylla_install_image.os.remove")
    @patch("scylla_install_image.os.makedirs")
    @patch("builtins.open", new_callable=mock_open)
    def test_creates_systemd_dropin(self, mock_file, mock_makedirs, mock_remove, mock_glob, mock_run):
        configure_chrony("oci")
        mock_makedirs.assert_any_call("/etc/systemd/system/scylla-server.service.d", mode=0o755, exist_ok=True)
        mock_file.assert_any_call("/etc/systemd/system/scylla-server.service.d/10-time-sync.conf", "w")

    @patch("scylla_install_image.run")
    @patch("scylla_install_image.glob.glob", return_value=[])
    @patch("scylla_install_image.os.remove")
    @patch("scylla_install_image.os.makedirs")
    @patch("builtins.open", new_callable=mock_open)
    def test_azure_writes_udev_rule(self, mock_file, mock_makedirs, mock_remove, mock_glob, mock_run):
        configure_chrony("azure")
        mock_file.assert_any_call("/etc/udev/rules.d/99-ptp-hyperv.rules", "w")

    @patch("scylla_install_image.run")
    @patch("scylla_install_image.glob.glob", return_value=[])
    @patch("scylla_install_image.os.remove")
    @patch("scylla_install_image.os.makedirs")
    @patch("builtins.open", new_callable=mock_open)
    def test_runs_daemon_reload(self, mock_file, mock_makedirs, mock_remove, mock_glob, mock_run):
        configure_chrony("aws")
        mock_run.assert_any_call("systemctl daemon-reload", shell=True, check=True)

    @patch("scylla_install_image.run")
    @patch("scylla_install_image.glob.glob", return_value=[])
    @patch("scylla_install_image.os.remove")
    @patch("scylla_install_image.os.makedirs")
    @patch("builtins.open", new_callable=mock_open)
    def test_disables_cloud_init_ntp(self, mock_file, mock_makedirs, mock_remove, mock_glob, mock_run):
        configure_chrony("gce")
        mock_makedirs.assert_any_call("/etc/cloud/cloud.cfg.d", exist_ok=True)
        mock_file.assert_any_call("/etc/cloud/cloud.cfg.d/99-disable-ntp.cfg", "w")
